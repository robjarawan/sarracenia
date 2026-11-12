import ftplib
import pytest
from tests.conftest import *

import sarracenia.config
import sarracenia.flow
import copy

__COMPONENT="subscribe"
__CONFIG="flow_class_test"

def __make_fake_config(lines=[]):
    """ build and return a fake config object
    """
    options = copy.deepcopy(sarracenia.config.default_config())
    options.component = __COMPONENT
    options.config = __CONFIG
    options.action = 'start'
    for line in lines:
        options.parse_line(__COMPONENT, __CONFIG, f"{__COMPONENT}/{__CONFIG}", 1, line)
    options.metricsFilename = '/tmp/fake_filename_nothing_here'
    options.novipFilename = options.metricsFilename
    options.acceptUnmatched = False
    options.finalize()
    return options


def test_msg_accepted_with_sundew_extension():
    """
    Regression test for https://github.com/MetPX/sarracenia/issues/1573
    sundew_extension should be included in filtering, even when URL has a port
    """
    options = __make_fake_config(lines=["accept .*rxer:CCCC:TT:3:Direct.*"])

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'http://dms-dev1.domain:8180/data/msc/'
    msg['relPath'] = 'forecast/atmospheric/aviation/file.txt'
    msg['sundew_extension'] = 'rxer:CCCC:TT:3:Direct'

    flow.worklist.incoming.append(msg)

    # based on the accept statement, the message should only be accepted when the sundew_extension is correctly added
    flow.filter()
    assert(len(flow.worklist.rejected) == 0)
    assert(msg not in flow.worklist.rejected)
    assert(msg in flow.worklist.incoming)

def test_msg_rejected_when_sundew_extension_already_present():
    """
    If the path itself already contains a different sundew extension,
    the sundew_extension header in the msg should not be used for filtering
    """
    options = __make_fake_config(lines=["accept .*rxer:CCCC:TT:3:Direct.*"])

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'http://dms-dev1.domain:8180/data/msc/'
    msg['relPath'] = 'forecast/atmospheric/aviation/file.txt:something:CWAO:SA:3:Direct'
    msg['sundew_extension'] = 'rxer:CCCC:TT:3:Direct'

    flow.worklist.incoming.append(msg)

    # msg already has a different sundew extension that does not match the accept, it should be rejected
    flow.filter()

    # worklist.rejected gets acked and set to [] at the end of filter
    assert(len(flow.worklist.incoming) == 0)
    assert(len(flow.worklist.rejected) == 0)
    assert(msg not in flow.worklist.incoming)


def __trailing_slash_flow(root):
    options = __make_fake_config(lines=["download True", "mirror True", f"directory {root}", "accept .*"])
    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    return flow


def __trailing_slash_message(relPath, **extra):
    msg = sarracenia.Message()
    msg['pubTime'] = '20261009T120000.123'
    msg['baseUrl'] = 'none://'
    msg['relPath'] = relPath
    msg.update(extra)
    return msg


def test_relpath_ending_in_slash_creates_directory(tmp_path):
    # issue #1503: sr3c posts mkdir some_dir/ with the trailing slash
    root = tmp_path / "root"
    root.mkdir()
    flow = __trailing_slash_flow(root)
    flow.worklist.incoming.append(__trailing_slash_message("sub/newdir/", fileOp={'directory': ''}))

    flow.filter()
    flow.work()

    assert (root / "sub" / "newdir").is_dir()
    assert len(flow.worklist.ok) == 1


def test_relpath_ending_in_slash_without_fileop_is_rejected(tmp_path):
    # a file message ending in / has no file name to write, it is rejected instead of crashing
    root = tmp_path / "root"
    root.mkdir()
    flow = __trailing_slash_flow(root)
    msg = __trailing_slash_message("sub/name/", size=8,
                                   content={"encoding": "utf-8", "value": "payload\n"},
                                   identity={"method": "arbitrary", "value": "x"})
    flow.worklist.incoming.append(msg)

    flow.filter()
    flow.work()

    assert flow.worklist.ok == []
    assert msg['report']['code'] == 422
    assert not (root / "sub" / "name").exists()


@pytest.mark.parametrize("accel", [False, True], ids=["put", "putAccelerated"])
def test_send_inflight_umask_puts_file_once(tmp_path, monkeypatch, accel):
    # issue #1821: the umask branch of send() called put() a second time,
    # so every file was sent with two STORs.
    from unittest.mock import MagicMock

    src = tmp_path / "data.bin"
    src.write_bytes(b"x" * 1700)
    # send() changes into the source directory and stays there.
    monkeypatch.chdir(tmp_path)

    options = __make_fake_config(lines=[
        "sendTo ftp://user@localhost/", "inflight umask", "mirror False",
        "directory /dest", "accept .*", "permCopy on", "dry_run off",
        f"baseDir {tmp_path}",
        "accelThreshold 1k" if accel else "accelThreshold 0"
    ])
    flow = sarracenia.flow.Flow(options)
    flow.scheme = 'ftp'
    proto = MagicMock()
    proto.check_is_connected.return_value = True
    proto.getcwd.return_value = '/dest'
    proto.put.return_value = 1700
    proto.putAccelerated.return_value = 1700
    flow.proto = {'ftp': proto}

    msg = sarracenia.Message()
    msg['pubTime'] = '20261112T120000.123'
    msg['baseUrl'] = 'file:'
    msg['relPath'] = 'data.bin'
    msg['size'] = 1700
    msg['mode'] = '644'
    msg['new_dir'] = '/dest'
    msg['new_file'] = 'data.bin'
    msg['local_offset'] = 0

    assert flow.send(msg, flow.o) == 1

    # one upload, by the method that fits the file size, and nothing to rename.
    uploads = [c[0] for c in proto.method_calls if c[0] in ('put', 'putAccelerated')]
    assert uploads == (['putAccelerated'] if accel else ['put'])
    proto.rename.assert_not_called()

    # the file is created unreadable, so the chmod has to follow the upload.
    names = [c[0] for c in proto.method_calls if c[0] in ('umask', 'put', 'putAccelerated', 'chmod')]
    assert names == ['umask', uploads[0], 'chmod']


@pytest.mark.parametrize("inflight", ["inflight umask", "inflight .tmp", "inflight .", ""],
                         ids=["umask", "suffix", "dot", "default"])
def test_send_fails_when_put_raises(tmp_path, monkeypatch, inflight):
    # issue #1822: a put() that raises has to end as a failed send, so the
    # message is retried instead of being reported as sent.
    from unittest.mock import MagicMock

    src = tmp_path / "data.bin"
    src.write_bytes(b"x" * 1700)
    monkeypatch.chdir(tmp_path)

    lines = ["sendTo ftp://user@localhost/", "mirror False", "directory /dest", "accept .*",
             "dry_run off", "accelThreshold 0", f"baseDir {tmp_path}"]
    if inflight:
        lines.append(inflight)
    flow = sarracenia.flow.Flow(__make_fake_config(lines=lines))
    flow.scheme = 'ftp'
    proto = MagicMock()
    proto.check_is_connected.return_value = True
    proto.getcwd.return_value = '/dest'
    proto.put.side_effect = ftplib.error_perm("552 Exceeded storage allocation.")
    flow.proto = {'ftp': proto}

    msg = sarracenia.Message()
    msg['pubTime'] = '20261112T120000.123'
    msg['baseUrl'] = 'file:'
    msg['relPath'] = 'data.bin'
    msg['size'] = 1700
    msg['new_dir'] = '/dest'
    msg['new_file'] = 'data.bin'
    msg['local_offset'] = 0

    assert flow.send(msg, flow.o) == 0
    assert proto.put.call_count == 1
    proto.rename.assert_not_called()
    proto.chmod.assert_not_called()


def __umask_sender(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    src = tmp_path / "data.bin"
    src.write_bytes(b"x" * 1700)
    monkeypatch.chdir(tmp_path)

    options = __make_fake_config(lines=[
        "sendTo ftp://user@localhost/", "inflight umask", "mirror False",
        "directory /dest", "accept .*", "permCopy on", "accelThreshold 0",
        "dry_run off", f"baseDir {tmp_path}"
    ])
    flow = sarracenia.flow.Flow(options)
    flow.scheme = 'ftp'
    proto = MagicMock()
    proto.check_is_connected.return_value = True
    proto.getcwd.return_value = '/dest'
    proto.put.return_value = 1700
    flow.proto = {'ftp': proto}

    msg = sarracenia.Message()
    msg['pubTime'] = '20261112T120000.123'
    msg['baseUrl'] = 'file:'
    msg['relPath'] = 'data.bin'
    msg['size'] = 1700
    msg['mode'] = '644'
    msg['new_dir'] = '/dest'
    msg['new_file'] = 'data.bin'
    msg['local_offset'] = 0

    def calls():
        found = [c[0] for c in proto.method_calls if c[0] in ('delete', 'umask', 'put', 'chmod')]
        proto.reset_mock()
        return found

    return flow, proto, msg, calls


REFUSED = ftplib.error_perm("553 Could not create file.")


def test_send_inflight_umask_removes_refused_file_before_retry(tmp_path, monkeypatch, caplog):
    # an upload that fails part way in umask mode leaves a file with no
    # permissions on the server, and the server refuses to overwrite it.
    flow, proto, msg, calls = __umask_sender(tmp_path, monkeypatch)

    # the upload times out: nothing is known about the remote file yet.
    proto.put.side_effect = sarracenia.transfer.TimeoutException("signal alarm timed out")
    assert flow.send(msg, flow.o) == 0
    assert calls() == ['umask', 'put']

    # the retry is refused: the file is there and can't be written to.
    proto.put.side_effect = REFUSED
    assert flow.send(msg, flow.o) == 0
    assert calls() == ['umask', 'put']

    # next attempt: remove it first and say so, then a normal upload.
    proto.put.side_effect = None
    with caplog.at_level("INFO"):
        assert flow.send(msg, flow.o) == 1
    proto.delete.assert_called_once_with('data.bin')
    assert calls() == ['delete', 'umask', 'put', 'chmod']
    assert "removing ftp://user@localhost/ /dest/data.bin after the server refused" in caplog.text

    # a later send of the same name is a normal one again.
    assert flow.send(msg, flow.o) == 1
    assert calls() == ['umask', 'put', 'chmod']


@pytest.mark.parametrize("error", [
    ftplib.error_temp("425 Can't open data connection."),
    sarracenia.transfer.TimeoutException("signal alarm timed out"),
    OSError("local file unreadable"),
], ids=["425", "timeout", "oserror"])
def test_send_inflight_umask_keeps_remote_file_when_not_refused(tmp_path, monkeypatch, error):
    # a failure that says nothing about the remote file must not delete it:
    # an earlier good copy may still be waiting there.
    flow, proto, msg, calls = __umask_sender(tmp_path, monkeypatch)

    proto.put.side_effect = error
    for attempt in range(3):
        assert flow.send(msg, flow.o) == 0
        assert calls() == ['umask', 'put']


def test_send_inflight_umask_retries_removal_that_failed(tmp_path, monkeypatch, caplog):
    flow, proto, msg, calls = __umask_sender(tmp_path, monkeypatch)

    proto.put.side_effect = REFUSED
    assert flow.send(msg, flow.o) == 0
    calls()

    # the removal itself fails, and so does the upload, for a reason that says
    # nothing about the remote file: say so, and try the removal again next time.
    proto.delete.side_effect = Exception("connection lost")
    proto.put.side_effect = sarracenia.transfer.TimeoutException("signal alarm timed out")
    with caplog.at_level("WARNING"):
        assert flow.send(msg, flow.o) == 0
    assert calls() == ['delete', 'umask', 'put']
    assert "could not remove failed upload" in caplog.text

    proto.delete.side_effect = None
    proto.put.side_effect = None
    assert flow.send(msg, flow.o) == 1
    assert calls() == ['delete', 'umask', 'put', 'chmod']


def test_send_inflight_umask_removal_is_per_directory(tmp_path, monkeypatch):
    flow, proto, msg, calls = __umask_sender(tmp_path, monkeypatch)

    proto.put.side_effect = REFUSED
    assert flow.send(msg, flow.o) == 0
    calls()

    # the same file name going to another directory is not touched.
    proto.put.side_effect = None
    other = copy.deepcopy(msg)
    other['new_dir'] = '/elsewhere'
    proto.getcwd.return_value = '/elsewhere'
    assert flow.send(other, flow.o) == 1
    assert calls() == ['umask', 'put', 'chmod']
