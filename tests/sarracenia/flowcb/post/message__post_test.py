"""Regression tests for publisher-specific post retry obligations.

The tests exercise Flow fan-out, the message post callback, Retry, and DiskQueue.
All protocol publishers are local recording fixtures.
"""

import copy
from types import SimpleNamespace
from unittest.mock import patch

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.config.credentials import Credential
from sarracenia.config.publisher import Publisher
from sarracenia.diskqueue import DiskQueue
from sarracenia.flow import Flow
from sarracenia.flowcb.post.message import Message as Poster
from sarracenia.flowcb.retry import Retry


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
    msg['_deleteOnPost'].add('publisher_index')
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
