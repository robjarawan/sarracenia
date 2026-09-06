"""Regression tests for publisher-specific post retry obligations.

The tests exercise Flow fan-out, the message post callback, Retry, and DiskQueue.
All protocol publishers are local recording fixtures.
"""

import copy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.config.credentials import Credential
from sarracenia.config.publisher import Publisher, Publishers
from sarracenia.diskqueue import DiskQueue
from sarracenia.flow import Flow
from sarracenia.flowcb.post.message import Message as Poster
from sarracenia.flowcb.retry import Retry
from sarracenia.moth.amqp import AMQP
from sarracenia.moth.mqtt import MQTT


class RecordingPublisher:

    def __init__(self, name, succeeds):
        self.name = name
        self.succeeds = succeeds
        self.messages = []

    def putNewMessage(self, message):
        self.messages.append(message)
        return self.succeeds


def options(tmp_path):
    cfg = no_file_config()
    cfg.component = 'sarra'
    cfg.config = 'publisher-proof'
    cfg.no = 1
    cfg.logLevel = 'info'
    cfg.retry_ttl = 0
    cfg.retryCountMax = 0
    cfg.batch = 10
    cfg.timeout = 1
    cfg.pid_filename = str(tmp_path / 'publisher-proof.pid')
    cfg.metricsFilename = str(tmp_path / 'metrics.json')
    cfg.novipFilename = str(tmp_path / 'novip')
    cfg.bufSize = 1024
    cfg.post_broker = Credential('amqp://guest@localhost/')
    cfg.post_baseDir = str(tmp_path)
    cfg.post_baseUrl = 'file:' + str(tmp_path)
    cfg.post_topicPrefix = ['v03', 'post']
    cfg.publishers = []
    return cfg


def configured_publishers(cfg, names):
    publishers = []
    for name in names:
        cfg.post_exchange = 'audit-' + name
        publishers.append(Publisher(cfg))
    cfg.publishers = publishers
    return cfg


def message():
    msg = sarracenia.Message()
    msg.update(
        baseUrl='file:/source',
        relPath='data.bin',
        pubTime=sarracenia.nowstr(),
        size=10,
        identity={'method': 'arbitrary', 'value': 'same-product'},
        new_file='data.bin',
        new_dir='/source',
        local_offset=0,
    )
    return msg


def worklist(**items):
    values = dict(incoming=[], ok=[], rejected=[], failed=[], directories_ok=[])
    values.update(items)
    return SimpleNamespace(**values)


def poster(cfg, recording_publishers):
    with patch('sarracenia.moth.Moth.pubFactory', side_effect=recording_publishers):
        return Poster(cfg)


def persist_and_reopen(cfg, msg, name):
    queue = DiskQueue(cfg, name)
    queue.put([msg])
    queue.on_housekeeping()
    queue.close()
    reopened = DiskQueue(cfg, name)
    recovered = reopened.get(10)
    reopened.close()
    assert len(recovered) == 1
    return recovered[0]


def failed_retry_for_second_publisher(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['a', 'b'])
    first = RecordingPublisher('a', True)
    second = RecordingPublisher('b', False)
    callback = poster(cfg, [first, second])
    msg = message()
    msg['publisher_index'] = 1
    msg['publisher_identity'] = callback._publisher_identity(cfg.publishers[1])
    msg['_deleteOnPost'].update(['publisher_index', 'publisher_identity'])
    batch = worklist(ok=[msg])
    callback.post(batch)
    assert batch.ok == []
    assert batch.failed == [msg]
    return cfg, msg


def test_flow_retry_preserves_both_failed_publisher_obligations(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['a', 'b'])
    flow = Flow(cfg)
    source = message()
    source['new_dir'] = str(tmp_path)
    flow.do = lambda: setattr(flow.worklist, 'ok', [source])
    flow.work()

    first = RecordingPublisher('a', False)
    second = RecordingPublisher('b', False)
    callback = poster(cfg, [first, second])
    callback.post(flow.worklist)
    assert len(flow.worklist.failed) == 2

    retry = Retry(cfg)
    retry.on_start()
    try:
        retry.after_post(flow.worklist)
        retry.post_retry.on_housekeeping()
        recovered = retry.post_retry.get(10)
    finally:
        retry.on_stop()

    assert sorted(item['publisher_index'] for item in recovered) == [0, 1]


def test_retry_follows_original_publisher_after_reorder_and_restart(tmp_path):
    cfg, failed = failed_retry_for_second_publisher(tmp_path)
    replay = persist_and_reopen(cfg, failed, 'reorder_retry')

    reordered = configured_publishers(options(tmp_path / 'reordered'), ['b', 'a'])
    destination_b = RecordingPublisher('b', True)
    wrong_destination_a = RecordingPublisher('a', True)
    callback = poster(reordered, [destination_b, wrong_destination_a])
    batch = worklist(ok=[replay])
    callback.post(batch)

    assert len(destination_b.messages) == 1
    assert wrong_destination_a.messages == []
    assert batch.ok == [replay]
    assert batch.failed == []
    assert 'publisher_identity' not in replay


def test_removed_publisher_stays_retryable_through_flow_post(tmp_path):
    cfg, failed = failed_retry_for_second_publisher(tmp_path)
    replay = persist_and_reopen(cfg, failed, 'removed_source_retry')

    remaining = configured_publishers(options(tmp_path / 'remaining'), ['a'])
    destination_a = RecordingPublisher('a', True)
    callback = poster(remaining, [destination_a])
    retry = Retry(remaining)
    retry.on_start()
    flow = Flow(remaining)
    flow.worklist = worklist(ok=[replay])
    flow.plugins['post'] = [callback.post]
    flow.plugins['after_post'] = [retry.after_post]
    flow._runCallbackMetrics = lambda: None
    try:
        flow.post(sarracenia.nowflt())
        retry.post_retry.on_housekeeping()
        recovered = retry.post_retry.get(10)
    finally:
        retry.on_stop()

    assert destination_a.messages == []
    assert len(recovered) == 1
    assert recovered[0].get('publisher_identity') is not None


def test_unchanged_publisher_order_control_retries_second_destination(tmp_path):
    cfg, failed = failed_retry_for_second_publisher(tmp_path)
    replay = persist_and_reopen(cfg, failed, 'unchanged_retry')
    destination_a = RecordingPublisher('a', True)
    destination_b = RecordingPublisher('b', True)
    callback = poster(cfg, [destination_a, destination_b])
    batch = worklist(ok=[replay])
    callback.post(batch)
    assert destination_a.messages == []
    assert len(destination_b.messages) == 1
    assert batch.ok == [replay]
    assert 'publisher_identity' not in replay


def test_single_publisher_retry_control_survives_housekeeping(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['a'])
    msg = message()
    msg['publisher_index'] = 0
    msg['_deleteOnPost'].add('publisher_index')
    recovered = persist_and_reopen(cfg, msg, 'single_retry')
    assert recovered['publisher_index'] == 0


def test_exact_duplicate_control_is_still_consolidated(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['a'])
    msg = message()
    msg['publisher_index'] = 0
    msg['_deleteOnPost'].add('publisher_index')
    queue = DiskQueue(cfg, 'duplicate_retry')
    queue.put([msg, copy.deepcopy(msg)])
    queue.on_housekeeping()
    recovered = queue.get(10)
    queue.close()
    assert len(recovered) == 1


def test_flow_stamps_fresh_publisher_obligations_before_post_retry(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['a', 'b'])
    flow = Flow(cfg)
    source = message()
    source['new_dir'] = str(tmp_path)
    flow.do = lambda: setattr(flow.worklist, 'ok', [source])

    flow.work()

    assert [item['publisher_identity'] for item in flow.worklist.ok] == [
        Poster._publisher_identity(publisher) for publisher in cfg.publishers
    ]
    assert all('publisher_identity' in item['_deleteOnPost']
               for item in flow.worklist.ok)
    assert flow.worklist.ok[0]['_deleteOnPost'] is not \
        flow.worklist.ok[1]['_deleteOnPost']


def test_legacy_index_only_retry_is_held_after_publisher_reorder(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['b', 'a'])
    destinations = [RecordingPublisher('b', True), RecordingPublisher('a', True)]
    callback = poster(cfg, destinations)
    legacy = message()
    legacy['publisher_index'] = 1
    legacy['_deleteOnPost'].add('publisher_index')
    batch = worklist(ok=[legacy])

    callback.post(batch)

    assert all(destination.messages == [] for destination in destinations)
    assert batch.ok == []
    assert batch.failed == [legacy]
    assert 'publisher_identity' not in legacy


def test_legacy_index_zero_is_held_with_one_replacement_publisher(tmp_path):
    cfg = configured_publishers(options(tmp_path), ['replacement'])
    destination = RecordingPublisher('replacement', True)
    callback = poster(cfg, [destination])
    legacy = message()
    legacy['publisher_index'] = 0
    legacy['_deleteOnPost'].add('publisher_index')
    batch = worklist(ok=[legacy])

    callback.post(batch)

    assert destination.messages == []
    assert batch.ok == []
    assert batch.failed == [legacy]


def test_persisted_identity_restores_missing_wire_marker(tmp_path):
    cfg, failed = failed_retry_for_second_publisher(tmp_path)
    failed['_deleteOnPost'].discard('publisher_identity')
    replay = persist_and_reopen(cfg, failed, 'missing_marker_retry')
    destination_a = RecordingPublisher('a', True)
    destination_b = RecordingPublisher('b', False)
    batch = worklist(ok=[replay])

    poster(cfg, [destination_a, destination_b]).post(batch)

    assert destination_a.messages == []
    assert len(destination_b.messages) == 1
    assert batch.ok == []
    assert batch.failed == [replay]
    assert 'publisher_identity' in replay
    assert 'publisher_identity' in replay['_deleteOnPost']


@pytest.mark.parametrize('names,index', [(['b', 'a'], 1), (['replacement'], 0)])
def test_persisted_legacy_retry_is_requeued_without_delivery(
        tmp_path, names, index):
    cfg = configured_publishers(options(tmp_path), names)
    destinations = [RecordingPublisher(name, True) for name in names]
    callback = poster(cfg, destinations)
    legacy = message()
    legacy['publisher_index'] = index
    legacy['_deleteOnPost'].add('publisher_index')
    retry = Retry(cfg)
    retry.on_start()
    try:
        retry.post_retry.put([legacy])
        retry.post_retry.on_housekeeping()
        flow = Flow(cfg)
        flow.plugins['post'] = [callback.post]
        flow.plugins['after_post'] = [retry.after_post]
        flow._runCallbackMetrics = lambda: None

        retry.after_work(flow.worklist)
        flow.post(sarracenia.nowflt())
        retry.post_retry.on_housekeeping()
        recovered = retry.post_retry.get(10)
    finally:
        retry.on_stop()

    assert all(destination.messages == [] for destination in destinations)
    assert len(recovered) == 1
    assert recovered[0]['publisher_index'] == index
    assert 'publisher_identity' not in recovered[0]


@pytest.mark.parametrize('publisher_results', [(False, True), (True, False)])
def test_failed_fanout_sibling_keeps_private_identity_marker(
        tmp_path, publisher_results):
    cfg = configured_publishers(options(tmp_path), ['a', 'b'])
    flow = Flow(cfg)
    source = message()
    source['new_dir'] = str(tmp_path)
    flow.do = lambda: setattr(flow.worklist, 'ok', [source])
    flow.work()
    callbacks = [
        RecordingPublisher(name, result)
        for name, result in zip(['a', 'b'], publisher_results)
    ]

    poster(cfg, callbacks).post(flow.worklist)

    assert len(flow.worklist.failed) == 1
    failed = flow.worklist.failed[0]
    assert 'publisher_identity' in failed
    assert 'publisher_identity' in failed['_deleteOnPost']
    wire_message = copy.copy(failed)
    for key in wire_message['_deleteOnPost']:
        wire_message.pop(key, None)
    assert 'publisher_identity' not in wire_message


@pytest.mark.parametrize('protocol', ['amqp', 'mqtt'])
def test_persisted_failed_sibling_does_not_advertise_retry_identity(
        tmp_path, protocol):
    cfg = configured_publishers(options(tmp_path), ['a', 'b'])
    flow = Flow(cfg)
    source = message()
    source['new_dir'] = str(tmp_path)
    flow.do = lambda: setattr(flow.worklist, 'ok', [source])
    flow.work()
    poster(cfg, [RecordingPublisher('a', False),
                 RecordingPublisher('b', True)]).post(flow.worklist)
    replay = persist_and_reopen(cfg, flow.worklist.failed[0],
                                'wire_retry_' + protocol)
    exported = Mock(return_value=('{}', {'topic': ['v03', 'fixture']},
                                  'application/json'))
    props = sarracenia.moth.default_options()
    props.update(cfg.dictify())
    props.update(cfg.publishers[0])

    if protocol == 'amqp':
        publisher = AMQP(props, False)
        publisher.connection = SimpleNamespace(connected=True)
        publisher.channel = Mock(is_open=True)
        export_path = 'sarracenia.moth.amqp.PostFormat.exportAny'
    else:
        publisher = MQTT(props, False)
        publisher.connected = True
        publisher.pending_publishes = []
        publisher.unexpected_publishes = []
        publisher.client = Mock()
        publisher.client.publish.return_value = SimpleNamespace(rc=0, mid=1)
        export_path = 'sarracenia.moth.mqtt.PostFormat.exportAny'

    with patch(export_path, exported):
        assert publisher.putNewMessage(replay)

    wire_message = exported.call_args.args[0]
    assert 'publisher_identity' not in wire_message


@pytest.mark.parametrize('disabled_setting', ['broker', 'publishers'])
def test_post_retry_is_not_dequeued_without_a_post_destination(
        tmp_path, disabled_setting):
    cfg = configured_publishers(options(tmp_path), ['a'])
    if disabled_setting == 'broker':
        cfg.publishers = Publishers()
        cfg.post_broker = None
    else:
        cfg.publishers = []
    retry = Retry(cfg)
    retry.on_start()
    try:
        retry.post_retry.put([message()])
        retry.post_retry.on_housekeeping()
        batch = worklist()

        retry.after_work(batch)

        assert batch.ok == []
        assert len(retry.post_retry) == 1
    finally:
        retry.on_stop()
