import pytest
from tests.conftest import *
from unittest.mock import patch, MagicMock

import jsonpickle, os

from sarracenia.diskqueue import DiskQueue
from sarracenia import Message as SR3Message

class Options:
    def __init__(self):
        self.no = 1
        self.retry_ttl = 0
        self.logLevel = "DEBUG"
        self.logFormat = ""
        self.queueName = "TEST_QUEUE_NAME"
        self.component = "sarra"
        self.retry_driver = 'disk'
        self.redisqueue_serverurl = "redis://Never.Going.To.Resolve:6379/0"
        self.config = "foobar.conf"
        self.pid_filename = "/tmp/sarracenia/diskqueue_test/pid_filename"
        self.housekeeping = float(39)
        self.batch = 0
    def add_option(self, option, type, default = None):
        if not hasattr(self, option):
            setattr(self, option, default)

def make_message():
    m = SR3Message()
    m["pubTime"] = "20180118151049.356378078"
    m["topic"] = "v02.post.sent_by_tsource2send"
    m["mtime"] = "20180118151048"
    m["headers"] = {
            "atime": "20180118151049.356378078", 
            "from_cluster": "localhost",
            "mode": "644",
            "parts": "1,69,1,0,0",
            "source": "tsource",
            "sum": "d,c35f14e247931c3185d5dc69c5cd543e",
            "to_clusters": "localhost"
        }
    m["baseUrl"] =  "https://NotARealURL"
    m["relPath"] = "ThisIsAPath/To/A/File.txt"
    m["notice"] = "20180118151050.45 ftp://anonymous@localhost:2121 /sent_by_tsource2send/SXAK50_KWAL_181510___58785"
    m["_deleteOnPost"] = set()
    return m

def test_msgFromJSON(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    message = make_message()

    assert message == download_retry.msgFromJSON(jsonpickle.encode(message))

def test_msgToJSON(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    message = make_message()

    assert jsonpickle.encode(message) + '\n' == download_retry.msgToJSON(message)

def test__is_exired__TooSoon(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    BaseOptions.retry_ttl = 100000
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    message = make_message()

    assert download_retry.is_expired(message) == True

def test__is_exired__TooLate(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    BaseOptions.retry_ttl = 1
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    import sarracenia
    message = make_message()
    message["pubTime"] = sarracenia.nowstr()

    assert download_retry.is_expired(message) == False

def test___len__(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    download_retry.msg_count += 1
    assert len(download_retry) == 1

    download_retry.msg_count_new += 1
    assert len(download_retry) == 2

def test_in_cache(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    message = make_message()
    download_retry.retry_cache = {}

    assert download_retry.in_cache(message) == False

    # Checking if it's there actually adds it, so checking it again right after should return True
    assert download_retry.in_cache(message) == True

def test_needs_requeuing(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'work_retry')

    message = make_message()
    download_retry.retry_cache = {}

    assert download_retry.needs_requeuing(message) == True
    assert download_retry.needs_requeuing(message) == False

    download_retry.o.retry_ttl = 1000000

    assert download_retry.needs_requeuing(message) == False
    
def test_put__Single(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_put__Single')

    message = make_message()
    download_retry.put([message])
    assert download_retry.msg_count_new == 1

    line = jsonpickle.encode(message) + '\n'

    assert open(download_retry.new_path, 'r').read() == line

def test_put__Multi(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_put__Multi')

    message = make_message()
    download_retry.put([message, message, message])
    assert download_retry.msg_count_new == 3

    line = jsonpickle.encode(message) + '\n'

    contents = open(download_retry.new_path, 'r').read()

    assert contents == line + line + line

def test_cleanup(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_cleanup')

    message = make_message()
    fp = open(download_retry.queue_file, 'a')
    fp.write(jsonpickle.encode(message) + '\n')
    download_retry.msg_count = 1

    assert os.path.exists(download_retry.queue_file) == True
    assert download_retry.msg_count == 1

    download_retry.cleanup()

    assert os.path.exists(download_retry.queue_file) == False
    assert download_retry.msg_count == 0

def test_msg_get_from_file__NoLine(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_msg_get_from_file__NoLine')

    fp_new, msg = download_retry.msg_get_from_file(None, download_retry.queue_file)

    assert fp_new == None
    assert msg == None

def test_msg_get_from_file(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_msg_get_from_file')

    message = make_message()

    fp = open(download_retry.queue_file, 'a')
    fp.write(jsonpickle.encode(message) + '\n')
    fp.flush()
    fp.close()

    fp_new, msg = download_retry.msg_get_from_file(None, download_retry.queue_file)

    import io
    assert isinstance(fp_new, io.TextIOWrapper) == True
    assert msg == message

def test_get__Single(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_get__Single')

    message = make_message()

    fp = open(download_retry.queue_file, 'a')
    line = jsonpickle.encode(message) + '\n'
    fp.write(line)
    fp.flush()
    fp.close()
    download_retry.msg_count = 1

    gotten = download_retry.get()

    assert len(gotten) == 1
    assert gotten == [message]

def test_get__Multi(tmp_path):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_get__Multi')

    message = make_message()

    fp = open(download_retry.queue_file, 'a')
    line = jsonpickle.encode(message) + '\n'
    fp.write(line + line)
    fp.flush()
    fp.close()
    download_retry.msg_count = 2

    gotten = download_retry.get(2)

    assert len(gotten) == 2
    assert gotten == [message, message]

def test_on_housekeeping__FinishRetry(tmp_path, caplog):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_on_housekeeping__FinishRetry')

    hk_out = download_retry.on_housekeeping()

    assert hk_out == None

    # This should not be logged unless there is actually messages in the queue
    log_found_notFinished = False
    for record in caplog.records:
        if "Resuming retries" in record.message:
            log_found_notFinished = True
    
    assert log_found_notFinished == False

    m1 = make_message()
    download_retry.put([m1])

    # put message into Queue from new
    download_retry.on_housekeeping()

    # run housekeeping again and now it should say it's not done
    download_retry.on_housekeeping()
    # This should not be logged unless there is actually messages in the queue
    log_found_notFinished = False
    for record in caplog.records:
        if "Resuming retries" in record.message:
            log_found_notFinished = True
    
    assert log_found_notFinished == True


def test_on_housekeeping(tmp_path, caplog):
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    download_retry = DiskQueue(BaseOptions, 'test_on_housekeeping')

    message = make_message()

    download_retry.new_fp = open(download_retry.new_path, 'a')
    line = jsonpickle.encode(message) + '\n'
    download_retry.new_fp.write(line + line)
    download_retry.new_fp.flush()

    hk_out = download_retry.on_housekeeping()

    assert hk_out == None
    assert os.path.exists(download_retry.queue_file) == True
    assert os.path.exists(download_retry.new_path) == False

    log_found_HasQueue = log_found_NumMessages = log_found_Elapsed = False

    for record in caplog.records:
        if "has queue" in record.message:
            log_found_HasQueue = True
        if "Number of messages in retry list" in record.message:
            log_found_NumMessages = True
        if "on_housekeeping elapse" in record.message:
            log_found_Elapsed = True

    assert log_found_HasQueue == True
    assert log_found_NumMessages == True
    assert log_found_Elapsed == True

def test_diskqueue(tmp_path, caplog):
    """ DiskQueue integration test, tests the behaviour of the class, mimicking how it's actually used in sr3.
    """
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'DiskQueue_Integration')
    m1 = make_message()
    m2 = make_message()
    m2['pubTime'] = "20200118151049.356378078"
    m3 = make_message()
    m3['pubTime'] = "20240118151049.356378078"

    dq.put([m1])

    assert len(dq) == 1
    assert dq.msg_count_new == 1
    assert dq.msg_count == 0

    dq.put([m2, m3])
    assert len(dq) == 3
    assert dq.msg_count_new == 3
    assert dq.msg_count == 0

    # should not be possible to get a message until after housekeeping
    got = dq.get(2)
    assert len(got) == 0
    assert len(dq) == 3
    assert dq.msg_count_new == 3
    assert dq.msg_count == 0

    # now all messages should be moved from new file to normal file
    dq.on_housekeeping()
    assert len(dq) == 3
    assert dq.msg_count_new == 0
    assert dq.msg_count == 3

    # now we can get
    got = dq.get(2)
    assert len(got) == 2
    assert len(dq) == 1
    assert dq.msg_count_new == 0
    assert dq.msg_count == 1
    assert dq.complete(len(got))

    # try running housekeeping again
    dq.on_housekeeping()
    assert len(dq) == 1
    assert dq.msg_count_new == 0
    assert dq.msg_count == 1

    log_found_resuming_retries = False
    for record in caplog.records:
        if "Resuming retries" in record.message:
            log_found_resuming_retries = True
    assert log_found_resuming_retries

    # add messages back
    dq.put([m1, m2])
    assert len(dq) == 3
    assert dq.msg_count_new == 2
    assert dq.msg_count == 1

    dq.on_housekeeping()
    assert len(dq) == 3
    assert dq.msg_count_new == 2
    assert dq.msg_count == 1

    log_found_resuming_retries = False
    for record in caplog.records:
        if "Resuming retries" in record.message:
            log_found_resuming_retries = True
    assert log_found_resuming_retries

    # get 1, now the queue is empty
    got = dq.get()
    assert len(got) == 1
    assert len(dq) == 2
    assert dq.msg_count_new == 2
    assert dq.msg_count == 0
    assert dq.complete(len(got))

    # now housekeeping can move new msgs to regular file
    dq.on_housekeeping()
    assert len(dq) == 2
    assert dq.msg_count_new == 0
    assert dq.msg_count == 2

    # add message back before closing, 1 in new, 2 in regular
    dq.put([m1])
    assert len(dq) == 3
    assert dq.msg_count_new == 1
    assert dq.msg_count == 2

    # close and re-open, messages in both new and regular file
    dq.close()
    dq = DiskQueue(BaseOptions, 'DiskQueue_Integration')
    assert len(dq) == 3
    assert dq.msg_count_new == 1
    assert dq.msg_count == 2


def test_close__None_fps(tmp_path):
    """close() should not crash when file pointers are None."""
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_close__None_fps')

    assert dq.housekeeping_fp is None
    assert dq.new_fp is None
    assert dq.queue_fp is None

    dq.close()

    assert dq.housekeeping_fp is None
    assert dq.new_fp is None
    assert dq.queue_fp is None


def test_close__already_closed_fps(tmp_path):
    """close() should handle already-closed file pointers gracefully."""
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_close__already_closed')

    message = make_message()
    dq.put([message])

    assert dq.new_fp is not None
    dq.new_fp.close()

    dq.close()

    assert dq.new_fp is None
    assert dq.msg_count == 0


def test_close__fsync_uses_fileno(tmp_path):
    """close() should call os.fsync with fileno(), not the file object.

    The old code had os.fsync(self.new_fp) which passes a file object
    instead of a file descriptor. This would raise TypeError, but the
    bare except:pass hid the bug. Verify fsync is called correctly now.
    """
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_close__fsync')

    message = make_message()
    dq.put([message])

    assert dq.new_fp is not None
    fd = dq.new_fp.fileno()

    with patch('os.fsync') as mock_fsync:
        dq.close()
        mock_fsync.assert_called_once_with(fd)


def test_close__keyboard_interrupt_propagates(tmp_path):
    """KeyboardInterrupt must not be caught by close().

    The old bare except: would swallow KeyboardInterrupt and SystemExit.
    After narrowing to except Exception:, these should propagate.
    """
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_close__kb_interrupt')

    mock_fp = MagicMock()
    mock_fp.close.side_effect = KeyboardInterrupt
    dq.housekeeping_fp = mock_fp

    with pytest.raises(KeyboardInterrupt):
        dq.close()


def test_get__keyboard_interrupt_propagates(tmp_path):
    """KeyboardInterrupt in os.unlink during get() should propagate."""
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_get__kb_interrupt')

    fp = open(dq.queue_file, 'w')
    fp.close()
    dq.msg_count = 1

    with patch('os.unlink', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            dq.get()


def test_msg_get_from_file__corrupted_lines(tmp_path):
    """Regression: msg_get_from_file used recursion to skip corrupted lines.
    With >1000 consecutive bad lines, this hit Python's recursion limit
    and crashed with RecursionError.  The fix uses a while loop instead.
    """
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_corrupted_lines')

    message = make_message()
    valid_line = jsonpickle.encode(message) + '\n'

    # write 2000 corrupted lines followed by one valid message
    with open(dq.queue_file, 'w') as fp:
        for i in range(2000):
            fp.write('THIS IS NOT VALID JSON line %d\n' % i)
        fp.write(valid_line)

    fp_out, msg = dq.msg_get_from_file(None, dq.queue_file)

    assert msg is not None, "valid message after 2000 corrupted lines was not found"
    assert msg == message
    assert fp_out is not None
    fp_out.close()


def test_msg_get_from_file__all_corrupted(tmp_path):
    """If the entire file is corrupted, msg_get_from_file should return
    None without crashing.
    """
    BaseOptions = Options()
    BaseOptions.pid_filename = str(tmp_path) + os.sep + "pidfilename.txt"
    dq = DiskQueue(BaseOptions, 'test_all_corrupted')

    with open(dq.queue_file, 'w') as fp:
        for i in range(500):
            fp.write('GARBAGE LINE %d\n' % i)

    fp_out, msg = dq.msg_get_from_file(None, dq.queue_file)

    assert fp_out is None
    assert msg is None


@pytest.mark.parametrize("already_inflight", [False, True])
def test_get_rolls_back_only_the_failed_batch(tmp_path, monkeypatch,
                                                already_inflight):
    """A mid-read error cannot create unowned in-flight queue records."""
    options = Options()
    options.pid_filename = str(tmp_path / "pid")
    queue = DiskQueue(options, "atomic_get")
    messages = [make_message() for _ in range(3)]
    for index, message in enumerate(messages):
        message["relPath"] = "atomic-%d" % index
    queue.put(messages)
    queue.on_housekeeping()

    owned = queue.get(1) if already_inflight else []
    initial_available = queue.msg_count
    initial_inflight = queue.inflight_count
    real_read = queue.msg_get_from_file
    calls = 0

    def fail_second_read(fp, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic retry read failure")
        return real_read(fp, path)

    monkeypatch.setattr(queue, "msg_get_from_file", fail_second_read)
    with pytest.raises(OSError, match="synthetic retry read failure"):
        queue.get(2)

    assert queue.msg_count == initial_available
    assert queue.inflight_count == initial_inflight

    monkeypatch.setattr(queue, "msg_get_from_file", real_read)
    remaining = queue.get(initial_available)
    assert len(remaining) == initial_available
    assert {message["relPath"] for message in owned + remaining} == {
        "atomic-0", "atomic-1", "atomic-2"
    }
    assert queue.complete(len(owned) + len(remaining))
    queue.close()


@pytest.mark.parametrize("failure_point", ["write", "after_prefix", "flush"])
def test_put_rolls_back_partial_append(tmp_path, failure_point):
    """Retrying a failed append starts at the last complete record boundary."""
    options = Options()
    options.pid_filename = str(tmp_path / "pid")
    queue = DiskQueue(options, "atomic_put")
    first = make_message()
    first["relPath"] = "already-persisted"
    second = make_message()
    second["relPath"] = "failed-attempt"
    third = make_message()
    third["relPath"] = "same-failed-attempt"
    queue.put([first])
    initial_size = os.path.getsize(queue.new_path)

    class FailingWriter:

        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.wrote_current_batch = False
            self.write_calls = 0

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def write(self, value):
            self.write_calls += 1
            if failure_point == "write" or (failure_point == "after_prefix"
                                             and self.write_calls == 2):
                self.wrapped.write(value[:len(value) // 2])
                self.wrapped.flush()
                raise OSError("synthetic partial write")
            result = self.wrapped.write(value)
            self.wrote_current_batch = True
            return result

        def flush(self):
            if failure_point == "flush" and self.wrote_current_batch:
                self.wrapped.flush()
                raise OSError("synthetic flush failure")
            return self.wrapped.flush()

        def close(self):
            return self.wrapped.close()

    queue.new_fp = FailingWriter(queue.new_fp)
    with pytest.raises(OSError, match="synthetic"):
        queue.put([second, third])

    assert queue.msg_count_new == 1
    assert os.path.getsize(queue.new_path) == initial_size
    assert queue.new_fp is None

    queue.put([second, third])
    queue.on_housekeeping()
    recovered = queue.get(3)
    assert [message["relPath"] for message in recovered] == [
        "already-persisted", "failed-attempt", "same-failed-attempt"
    ]
    assert queue.complete(3)
    queue.close()


def test_housekeeping_keeps_failed_append_rollback_blocked(tmp_path,
                                                            monkeypatch):
    """Housekeeping cannot expose a prefix from an ambiguous failed append."""
    options = Options()
    options.pid_filename = str(tmp_path / "pid")
    queue = DiskQueue(options, "failed_append_rollback")
    messages = [make_message(), make_message()]
    messages[0]["relPath"] = "complete-prefix"
    messages[1]["relPath"] = "torn-record"

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

    queue.new_fp = open(queue.new_path, 'a')
    queue.new_fp = PartialWriter(queue.new_fp)
    real_open = open

    def fail_rollback(path, mode='r', *args, **kwargs):
        if path == queue.new_path and mode == 'r+b':
            raise OSError("synthetic rollback failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_rollback)
    with pytest.raises(OSError, match="synthetic partial write"):
        queue.put(messages)

    assert queue.append_rollback_failed
    assert queue.msg_count == 0
    assert queue.msg_count_new == 0

    queue.on_housekeeping()

    assert queue.append_rollback_failed
    assert queue.msg_count == 0
    assert queue.get(1) == []
    with pytest.raises(OSError, match="append is blocked"):
        queue.put(messages)

    queue.close()
    assert queue.append_rollback_failed


@pytest.mark.parametrize("with_persisted_prefix", [False, True])
def test_failed_append_rollback_recovers_at_saved_boundary(
        tmp_path, monkeypatch, with_persisted_prefix):
    """A later append recovers a failed rollback without loss or duplicates."""
    options = Options()
    options.pid_filename = str(tmp_path / "pid")
    queue = DiskQueue(options, "recover_failed_append_rollback")
    expected_paths = []

    if with_persisted_prefix:
        persisted = make_message()
        persisted["relPath"] = "persisted-prefix"
        queue.put([persisted])
        expected_paths.append("persisted-prefix")
    else:
        queue.new_fp = open(queue.new_path, 'a')

    messages = [make_message() for _ in range(4)]
    for index, message in enumerate(messages):
        message["relPath"] = "recovered-%d" % index

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
    with pytest.raises(OSError, match="synthetic partial write"):
        queue.put(messages[:2])

    with real_open(queue.new_path, 'rb') as queue_file:
        blocked_bytes = queue_file.read()
    queue.on_housekeeping()
    queue.close()

    assert queue.append_rollback_failed
    with pytest.raises(OSError, match="append is blocked"):
        queue.put(messages[:3])
    with real_open(queue.new_path, 'rb') as queue_file:
        assert queue_file.read() == blocked_bytes

    queue.put(messages)
    assert not queue.append_rollback_failed
    queue.on_housekeeping()
    recovered = queue.get(10)
    expected_paths.extend("recovered-%d" % index for index in range(4))
    assert [message["relPath"] for message in recovered] == expected_paths
    assert queue.complete(len(recovered))
    queue.close()


def test_unlink_failure_retries_cleanup_without_replaying_completed_batch(
        tmp_path, monkeypatch):
    """Cleanup failure is distinct from unresolved processing ownership."""
    options = Options()
    options.pid_filename = str(tmp_path / "pid")
    queue = DiskQueue(options, "unlink_cleanup")
    queue.put([make_message()])
    queue.on_housekeeping()
    assert len(queue.get(1)) == 1

    real_unlink = os.unlink
    failures = 0

    def fail_queue_unlink_once(path):
        nonlocal failures
        if path == queue.queue_file and failures == 0:
            failures += 1
            raise OSError("synthetic unlink failure")
        real_unlink(path)

    monkeypatch.setattr(os, "unlink", fail_queue_unlink_once)
    assert queue.complete(1)
    assert queue.inflight_count == 0
    assert queue.cleanup_pending
    assert os.path.exists(queue.queue_file)

    queue.on_housekeeping()
    assert not queue.cleanup_pending
    assert not os.path.exists(queue.queue_file)

    queue.put([make_message()])
    queue.on_housekeeping()
    assert len(queue.get(1)) == 1
    assert queue.complete(1)
    queue.close()
