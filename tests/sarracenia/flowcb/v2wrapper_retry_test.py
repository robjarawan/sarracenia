import sarracenia
import sarracenia.config
import sarracenia.flow
import sarracenia.flowcb
from sarracenia.flowcb.retry import Retry
from sarracenia.flowcb.v2wrapper import V2Wrapper


def _config(tmp_path):
    options = sarracenia.config.no_file_config()
    options.component = 'subscribe'
    options.config = 'v2_retry_test'
    options.download = True
    options.plugins_early = []
    options.plugins_late = []
    options.destfn_scripts = []
    options.v2plugins = {'on_file': ['fixture']}
    options.publishers = [object()]
    options.no = 1
    options.batch = 10
    options.pid_filename = str(tmp_path / 'subscribe.pid')
    options.metricsFilename = str(tmp_path / 'metrics')
    options.novipFilename = str(tmp_path / 'novip')
    options.retry_driver = 'disk'
    options.retry_ttl = 0
    options.retryCountMax = 0
    return options


def _message():
    message = sarracenia.Message()
    message['pubTime'] = '20260906T120000.000000000'
    message['baseUrl'] = 'file:/source'
    message['relPath'] = 'incoming/data.bin'
    message['retrievePath'] = 'wire/data.bin'
    message['subtopic'] = ['incoming']
    message['new_baseUrl'] = 'file:/destination'
    message['new_relPath'] = 'outgoing/data.bin'
    message['new_subtopic'] = ['outgoing']
    return message


def _load_callbacks(flow, retry, wrapper, monkeypatch):
    callbacks = {
        'sarracenia.flowcb.retry.Retry': retry,
        'sarracenia.flowcb.v2wrapper.V2Wrapper': wrapper,
    }
    flow.plugins['load'] = list(callbacks)
    monkeypatch.setattr(sarracenia.flowcb, 'load_library',
                        lambda path, options: callbacks[path])
    assert flow.loadCallbacks()


def test_failed_on_file_is_persisted_with_source_identity(tmp_path, monkeypatch):
    options = _config(tmp_path)
    flow = sarracenia.flow.Flow(options)
    retry = Retry(options)
    retry.on_start()

    wrapper = object.__new__(V2Wrapper)
    wrapper.run_entry = lambda entry_point, message: entry_point != 'on_file'
    _load_callbacks(flow, retry, wrapper, monkeypatch)

    message = _message()
    flow.worklist.ok = [message]
    flow.work_message_adjust(message)

    assert message['baseUrl'] == 'file:/destination'
    assert message['relPath'] == 'outgoing/data.bin'
    assert 'retrievePath' not in message

    flow._runCallbacksWorklist('after_work')

    assert (len(retry.download_retry), len(flow.worklist.failed),
            len(flow.worklist.ok)) == (1, 0, 0)
    assert flow.plugins['after_work'][-1].__self__ is retry

    retry.download_retry.on_housekeeping()
    queued = retry.download_retry.get(1)[0]
    assert queued['baseUrl'] == 'file:/source'
    assert queued['relPath'] == 'incoming/data.bin'
    assert queued['retrievePath'] == 'wire/data.bin'
    assert queued['subtopic'] == ['incoming']


def test_retry_sink_restores_adjusted_source_fields(tmp_path):
    options = _config(tmp_path)
    flow = sarracenia.flow.Flow(options)
    retry = Retry(options)
    retry.on_start()

    wrapper = object.__new__(V2Wrapper)
    wrapper.run_entry = lambda entry_point, message: entry_point != 'on_file'

    message = _message()
    flow.worklist.ok = [message]
    flow.work_message_adjust(message)

    wrapper.after_work(flow.worklist)
    retry.after_work(flow.worklist)

    assert len(retry.download_retry) == 1
    retry.download_retry.on_housekeeping()
    queued = retry.download_retry.get(1)[0]
    assert (queued.get('baseUrl'), queued.get('relPath'),
            queued.get('retrievePath'), queued.get('subtopic')) == (
                'file:/source', 'incoming/data.bin', 'wire/data.bin',
                ['incoming'])


def test_successful_on_file_remains_ready_to_post(tmp_path, monkeypatch):
    options = _config(tmp_path)
    flow = sarracenia.flow.Flow(options)
    retry = Retry(options)
    retry.on_start()

    wrapper = object.__new__(V2Wrapper)
    wrapper.run_entry = lambda entry_point, message: True
    _load_callbacks(flow, retry, wrapper, monkeypatch)

    message = _message()
    flow.worklist.ok = [message]
    flow.work_message_adjust(message)
    flow._runCallbacksWorklist('after_work')

    assert flow.worklist.ok == [message]
    assert flow.worklist.failed == []
    assert len(retry.download_retry) == 0
    assert message['baseUrl'] == 'file:/destination'
    assert message['relPath'] == 'outgoing/data.bin'
