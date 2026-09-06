"""Retry queue completion and restart recovery tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import sarracenia
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
