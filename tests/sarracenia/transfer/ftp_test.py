import ftplib
from unittest.mock import MagicMock, patch

import pytest

from sarracenia.transfer import TimeoutException
from sarracenia.transfer.ftp import Ftp


class MockOptions:

    def __init__(self):
        self.sendTo = "ftp://user:pass@localhost/"
        self.timeout = 30
        self.logLevel = "DEBUG"
        self.logFormat = ""
        self.batch = 100
        self.byteRateMax = 0
        self.bufSize = 8192
        self.ftpFilenameEncoding = "utf-8"
        self.tlsRigour = "normal"
        self.credentials = MagicMock()

    def add_option(self, option, kind, default=None):
        if not hasattr(self, option):
            setattr(self, option, default)


def make_ftp_transfer():
    options = MockOptions()
    options.credentials.get.return_value = (
        True,
        MagicMock(
            url=MagicMock(
                hostname="localhost",
                port=21,
                username="user",
                password="pass",
            ),
            tls=False,
            prot_p=False,
            passive=True,
            binary=True,
            implicit_ftps=False,
        ),
    )
    transfer = Ftp.__new__(Ftp)
    transfer.o = options
    transfer.connected = False
    transfer.ftp = None
    transfer.sendTo = options.sendTo
    transfer.host = "localhost"
    transfer.port = 21
    transfer.user = "user"
    transfer.password = "pass"
    transfer.tls = False
    transfer.implicit_ftps = False
    transfer.prot_p = False
    transfer.passive = True
    transfer.binary = True
    transfer.originalDir = "."
    transfer.pwd = "."
    return transfer


def test_connect_closes_ftp_on_login_failure():
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.return_value = None
    mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def test_connect_closes_ftp_on_connect_failure():
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.side_effect = OSError("Connection refused")

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def test_connect_closes_ftp_on_set_pasv_failure():
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.return_value = None
    mock_ftp.login.return_value = None
    mock_ftp.set_pasv.side_effect = OSError("set_pasv failed")

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def test_connect_keyboard_interrupt_propagates():
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.side_effect = KeyboardInterrupt

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                with pytest.raises(KeyboardInterrupt):
                    transfer.connect()


def test_connect_success_does_not_close():
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.return_value = None
    mock_ftp.login.return_value = None
    mock_ftp.set_pasv.return_value = None
    mock_ftp.pwd.return_value = "/home/user"

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is True
    assert transfer.connected is True
    assert transfer.ftp is mock_ftp
    mock_ftp.close.assert_not_called()


def make_connected_transfer(tmp_path):
    transfer = make_ftp_transfer()
    transfer.init()
    transfer.ftp = MagicMock(spec=ftplib.FTP)
    transfer.connected = True
    local_file = tmp_path / "data.bin"
    local_file.write_bytes(b"x" * 1700)
    return transfer, str(local_file)


def sends_all_then(error):
    """ behave like ftplib: hand every block to the callback, then fail on the final reply. """
    def stor(cmd, fp, *args):
        callback = args[-1]
        while True:
            buf = fp.read(8192)
            if not buf:
                break
            callback(buf)
        if error:
            raise error
    return stor


class Alarms:
    """ stand in for the alarm functions, remember whether a timer is left armed. """

    def __init__(self):
        self.armed = False

    def set(self, seconds):
        self.armed = True

    def cancel(self):
        self.armed = False


@pytest.fixture
def alarms():
    a = Alarms()
    with patch("sarracenia.transfer.alarm_set", a.set), \
         patch("sarracenia.transfer.alarm_cancel", a.cancel), \
         patch("sarracenia.transfer.ftp.alarm_set", a.set), \
         patch("sarracenia.transfer.ftp.alarm_cancel", a.cancel):
        yield a


def test_put_returns_bytes_written(tmp_path, alarms):
    transfer, local_file = make_connected_transfer(tmp_path)
    transfer.ftp.storbinary.side_effect = sends_all_then(None)

    assert transfer.put({}, local_file, "data.bin") == 1700
    assert not alarms.armed
    assert transfer.connected


PUT_FAILURES = [
    # (error, connection still usable afterwards)
    (ftplib.error_perm("552 Requested file action aborted. Exceeded storage allocation."), True),
    (ftplib.error_temp("451 Requested action aborted. Local error in processing."), True),
    (TimeoutException("signal alarm timed out"), False),
    (ConnectionResetError("Connection reset by peer"), False),
]


@pytest.mark.parametrize("binary", [True, False], ids=["storbinary", "storlines"])
@pytest.mark.parametrize("error,usable", PUT_FAILURES, ids=["552", "451", "timeout", "reset"])
def test_put_raises_when_server_does_not_confirm(tmp_path, alarms, error, usable, binary):
    # issue #1822: all the bytes went out, then the server refused the file
    # or never answered. put() used to log that and return the byte count.
    transfer, local_file = make_connected_transfer(tmp_path)
    transfer.binary = binary
    ftp = transfer.ftp
    ftp.storbinary.side_effect = sends_all_then(error)
    ftp.storlines.side_effect = sends_all_then(error)

    with patch.object(transfer, "local_read_close", wraps=transfer.local_read_close) as read_close:
        with pytest.raises(type(error)):
            transfer.put({}, local_file, "data.bin")

    # the cleanup from #966: no timer left running, local file closed.
    assert not alarms.armed
    read_close.assert_called_once()

    # after a refusal the reply was read, so the connection is kept. After a
    # timeout or a socket error a late reply could still arrive, so it is dropped.
    assert transfer.connected is usable
    if usable:
        ftp.close.assert_not_called()
    else:
        ftp.close.assert_called_once()
        assert transfer.check_is_connected() is False
