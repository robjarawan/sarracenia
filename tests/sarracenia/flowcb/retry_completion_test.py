"""Retry queue completion and restart recovery tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import sarracenia
from sarracenia.flow import Flow
from sarracenia.flowcb.log import Log
from sarracenia.flowcb.retry import Retry


class Options:

    def __init__(self, tmp_path, batch=4):
        self.batch = batch
        self.component = "sarra"
        self.config = "final-dequeue-recovery.conf"
        self.housekeeping = 0.0
        self.logFormat = ""
        self.logLevel = "DEBUG"
        self.no = 1
        self.pid_filename = str(tmp_path / "state" / "pid")
        self.post_broker = True
        self.publishers = [object()]
        self.queueName = "final-dequeue-recovery"
        self.redisqueue_serverurl = "redis://unused.invalid:6379/0"
        self.retryCountMax = 0
        self.retry_driver = "disk"
        self.retry_refilter = False
        self.retry_ttl = 0

    def add_option(self, option, option_type, default=None):
        if not hasattr(self, option):
            setattr(self, option, default)


def make_message(index):
    message = sarracenia.Message()
    message.update(baseUrl="file:/local-fixture",
                   identity={
                       "method": "sha512", "value": "fixture-%d" % index
                   },
                   pubTime=sarracenia.nowstr(),
                   relPath="data-%d.bin" % index,
                   size=10)
    return message


def make_worklist():
    return SimpleNamespace(directories_ok=[],
                           failed=[],
                           incoming=[],
                           ok=[],
                           rejected=[])


def seed(queue):
    queue.put([make_message(1), make_message(2)])
    queue.on_housekeeping()
    assert len(queue) == 2


def reopen(options, queue_name):
    retry = Retry(options)
    retry.on_start()
    queue = getattr(retry, queue_name)
    queue.on_housekeeping()
    recovered = len(queue)
    retry.on_stop()
    return recovered


def dispatch(retry, worklist, entry_point):
    """Run a Retry callback through Flow's exception-catching dispatcher."""
    flow = SimpleNamespace(_logLevel_debug=False,
                           plugins={entry_point: [getattr(retry,
                                                          entry_point)]},
                           worklist=worklist)
    Flow._runCallbacksWorklist(flow, entry_point)


def read_new_messages(queue):
    """Return records durably appended to a queue's `.new` file."""
    queue.new_fp.flush()
    with open(queue.new_path, 'r') as queue_file:
        return [queue.msgFromJSON(line) for line in queue_file]


@pytest.mark.parametrize("batch,expected_dequeued", [(0, 0), (2, 1)])
def test_download_retry_controls_remain_recoverable(tmp_path, batch,
                                                       expected_dequeued):
    """A restart recovers the queue before a get and after a partial get."""
    options = Options(tmp_path, batch=batch)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    assert len(worklist.incoming) == expected_dequeued
    retry.on_stop()

    assert reopen(options, "download_retry") == 2


def test_download_retry_final_batch_remains_recoverable_until_after_work(
        tmp_path):
    """The final work-retry batch is recoverable until work completes."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    assert len(worklist.incoming) == 2
    retry.on_stop()  # Process stops before Retry.after_work records the outcome.

    assert reopen(options, "download_retry") == 2


def test_refiltered_download_retry_remains_recoverable_until_after_work(
        tmp_path):
    """The gather callback retains refiltered retries until work completes."""
    options = Options(tmp_path)
    options.retry_refilter = True
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    keep_going, incoming = retry.gather(2)
    assert keep_going
    assert len(incoming) == 2
    retry.on_stop()  # Process stops before Retry.after_work records the outcome.

    assert reopen(options, "download_retry") == 2


def test_post_retry_final_batch_remains_recoverable_until_after_post(tmp_path):
    """The final post-retry batch is recoverable until posting completes."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.post_retry)

    worklist = make_worklist()
    retry.after_work(worklist)
    assert len(worklist.ok) == 2
    retry.on_stop()  # Process stops before Retry.after_post records the outcome.

    assert reopen(options, "post_retry") == 2


def test_download_retry_final_batch_is_retired_after_successful_work(tmp_path):
    """Completed work does not replay after restart."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    worklist.ok = worklist.incoming
    worklist.incoming = []
    retry.after_work(worklist)
    retry.on_stop()

    assert reopen(options, "download_retry") == 0


def test_download_retry_failure_is_requeued_before_final_batch_is_retired(
        tmp_path):
    """Failed work remains recoverable after the original batch is retired."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    worklist.failed = worklist.incoming
    worklist.incoming = []
    retry.after_work(worklist)
    retry.on_stop()

    assert reopen(options, "download_retry") == 2


def test_download_retry_is_not_retired_when_requeue_fails(tmp_path,
                                                            monkeypatch):
    """The original work retry remains authoritative if requeue fails."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    worklist.failed = worklist.incoming
    worklist.incoming = []

    failing_put = Mock(side_effect=OSError("synthetic retry write failure"))
    monkeypatch.setattr(retry.download_retry, "put", failing_put)
    try:
        retry.after_work(worklist)
    except OSError as err:
        assert str(err) == "synthetic retry write failure"
    assert failing_put.call_count == 1
    retry.on_stop()

    assert reopen(options, "download_retry") == 2


def test_post_retry_final_batch_is_retired_after_successful_post(tmp_path):
    """Completed posts do not replay after restart."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.post_retry)

    worklist = make_worklist()
    retry.after_work(worklist)
    retry.after_post(worklist)
    retry.on_stop()

    assert reopen(options, "post_retry") == 0


def test_post_retry_failure_is_requeued_before_final_batch_is_retired(
        tmp_path):
    """Failed posts remain recoverable after the original batch is retired."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.post_retry)

    worklist = make_worklist()
    retry.after_work(worklist)
    worklist.failed = worklist.ok
    worklist.ok = []
    retry.after_post(worklist)
    retry.on_stop()

    assert reopen(options, "post_retry") == 2


def test_post_retry_is_not_retired_when_requeue_fails(tmp_path, monkeypatch):
    """The original post retry remains authoritative if requeue fails."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.post_retry)

    worklist = make_worklist()
    retry.after_work(worklist)
    worklist.failed = worklist.ok
    worklist.ok = []

    failing_put = Mock(side_effect=OSError("synthetic retry write failure"))
    monkeypatch.setattr(retry.post_retry, "put", failing_put)
    try:
        retry.after_post(worklist)
    except OSError as err:
        assert str(err) == "synthetic retry write failure"
    assert failing_put.call_count == 1
    retry.on_stop()

    assert reopen(options, "post_retry") == 2


@pytest.mark.parametrize("retry_refilter", [False, True])
def test_download_retry_write_failure_survives_later_flow_callback(
        tmp_path, monkeypatch, retry_refilter):
    """A caught retry-store failure cannot be forgotten on the next cycle."""
    options = Options(tmp_path)
    options.retry_refilter = retry_refilter
    options.retryCountMax = 2
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    if retry_refilter:
        _, worklist.incoming = retry.gather(2)
    else:
        retry.after_accept(worklist)
    worklist.failed = worklist.incoming
    worklist.incoming = []

    real_put = retry.download_retry.put
    calls = 0

    def fail_once(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic retry write failure")
        real_put(messages)

    monkeypatch.setattr(retry.download_retry, "put", fail_once)
    dispatch(retry, worklist, "after_work")
    assert retry.metricsReport()["msgs_in_download_retry"] == 2
    assert worklist.failed == []

    # The next cycle has no failed list to carry the obligation.
    worklist.failed = []
    dispatch(retry, worklist, "after_work")
    assert calls == 2
    assert retry.metricsReport()["msgs_in_download_retry"] == 2
    assert [message["_isRetry"] for message in
            read_new_messages(retry.download_retry)] == [2, 2]
    retry.on_stop()

    assert reopen(options, "download_retry") == 2


def test_post_retry_write_failure_survives_later_flow_callback(tmp_path,
                                                                 monkeypatch):
    """A caught post-retry write failure remains an outstanding obligation."""
    options = Options(tmp_path)
    options.retryCountMax = 1
    retry = Retry(options)
    retry.on_start()
    seed(retry.post_retry)

    worklist = make_worklist()
    retry.after_work(worklist)
    worklist.failed = worklist.ok
    worklist.ok = []

    real_put = retry.post_retry.put
    calls = 0

    def fail_once(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic retry write failure")
        real_put(messages)

    monkeypatch.setattr(retry.post_retry, "put", fail_once)
    dispatch(retry, worklist, "after_post")
    assert retry.metricsReport()["msgs_in_post_retry"] == 2
    assert worklist.failed == []

    worklist.failed = []
    dispatch(retry, worklist, "after_post")
    assert calls == 2
    assert retry.metricsReport()["msgs_in_post_retry"] == 2
    assert [message["_isRetry"] for message in
            read_new_messages(retry.post_retry)] == [1, 1]
    retry.on_stop()

    assert reopen(options, "post_retry") == 2


def test_post_retry_recovers_after_append_rollback_failure(
        tmp_path, monkeypatch):
    """Post retries resume persistence when a failed rollback later succeeds."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    queue = retry.post_retry
    queue.new_fp = open(queue.new_path, 'a')

    class PartialWriter:

        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.write_calls = 0

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def write(self, value):
            self.write_calls += 1
            if self.write_calls == 2:
                self.wrapped.write(value[:len(value) // 2])
                self.wrapped.flush()
                raise OSError("synthetic partial write")
            return self.wrapped.write(value)

        def close(self):
            return self.wrapped.close()

    queue.new_fp = PartialWriter(queue.new_fp)
    real_open = open
    rollback_failures = 2

    def fail_rollback_twice(path, mode='r', *args, **kwargs):
        nonlocal rollback_failures
        if (path == queue.new_path and mode == 'r+b'
                and rollback_failures > 0):
            rollback_failures -= 1
            raise OSError("synthetic rollback failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_rollback_twice)
    worklist = make_worklist()
    worklist.failed = [make_message(1), make_message(2)]
    dispatch(retry, worklist, "after_post")
    assert len(retry.post_retry_pending) == 2

    worklist.failed = [make_message(3)]
    dispatch(retry, worklist, "after_post")
    assert len(retry.post_retry_pending) == 3
    queue.on_housekeeping()
    retry.on_stop()

    worklist.failed = [make_message(4)]
    dispatch(retry, worklist, "after_post")
    assert retry.post_retry_pending == []
    assert [message["_isRetry"] for message in
            read_new_messages(queue)] == [1, 1, 1, 1]
    retry.on_stop()

    restarted = Retry(options)
    restarted.on_start()
    restarted.post_retry.on_housekeeping()
    recovered = restarted.post_retry.get(10)
    assert [message["relPath"] for message in recovered] == [
        "data-1.bin", "data-2.bin", "data-3.bin", "data-4.bin"
    ]
    assert [message["_isRetry"] for message in recovered] == [1, 1, 1, 1]
    assert restarted.post_retry.complete(4)
    restarted.on_stop()


def test_post_retry_failure_remains_visible_to_later_callbacks(tmp_path,
                                                                 caplog):
    """Persisting a post retry does not hide it from later callbacks."""
    options = Options(tmp_path)
    options.logEvents = {"after_post"}
    options.logMessageDump = False
    retry = Retry(options)
    retry.on_start()
    log = Log(options)
    message = make_message(1)
    worklist = make_worklist()
    worklist.failed = [message]
    flow = SimpleNamespace(_logLevel_debug=False,
                           plugins={"after_post": [retry.after_post,
                                                    log.after_post]},
                           worklist=worklist)

    caplog.set_level("INFO", logger="sarracenia.flowcb.log")
    Flow._runCallbacksWorklist(flow, "after_post")

    assert len(retry.post_retry) == 1
    assert worklist.failed == [message]
    assert any("failed to post, queued to retry" in record.message
               for record in caplog.records)
    retry.on_stop()


def test_inflight_retry_is_reported_until_processing_completes(tmp_path):
    """Exit checks must see a retry after dequeue and before completion."""
    options = Options(tmp_path)
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    assert retry.metricsReport()["msgs_in_download_retry"] == 2

    worklist.ok = worklist.incoming
    worklist.incoming = []
    retry.after_work(worklist)
    assert retry.metricsReport()["msgs_in_download_retry"] == 0
    retry.on_stop()


def test_terminal_retry_and_persisted_failure_complete_one_owned_batch(
        tmp_path):
    """A mixed terminal/retry outcome retires the original only after put."""
    options = Options(tmp_path)
    options.retryCountMax = 2
    retry = Retry(options)
    retry.on_start()
    seed(retry.download_retry)

    worklist = make_worklist()
    retry.after_accept(worklist)
    worklist.incoming[0]["_isRetry"] = 2
    worklist.failed = worklist.incoming
    worklist.incoming = []
    retry.after_work(worklist)
    assert retry.metricsReport()["msgs_in_download_retry"] == 1
    retry.on_stop()

    assert reopen(options, "download_retry") == 1
