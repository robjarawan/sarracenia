import ftplib
from unittest.mock import MagicMock, patch

import pytest

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


def test_connect_cancels_alarm_before_close_on_failure():
    # Lock in the SIGALRM-safe ordering: on failure, alarm_cancel must run
    # before ftp.close() so a pending SIGALRM cannot interrupt cleanup.
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.return_value = None
    mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")

    call_order = []

    def record_cancel():
        call_order.append("alarm_cancel")

    def record_close():
        call_order.append("ftp.close")

    mock_ftp.close.side_effect = record_close

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel", side_effect=record_cancel):
                result = transfer.connect()

    assert result is False
    assert call_order == ["alarm_cancel", "ftp.close"], \
        "alarm_cancel must run before ftp.close on the failure path"


def test_connect_swallows_close_error_during_cleanup():
    # If ftp.close itself raises during cleanup, connect must still return
    # cleanly (False) rather than propagating the cleanup error.
    transfer = make_ftp_transfer()

    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.connect.return_value = None
    mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")
    mock_ftp.close.side_effect = OSError("close failed")

    with patch("ftplib.FTP", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def _set_credentials(transfer, tls, implicit_ftps, port=21):
    # connect() calls self.credentials(), which resets self.tls and
    # self.implicit_ftps from the credentials mock. Override the mock so
    # the desired TLS branch is exercised.
    transfer.o.credentials.get.return_value = (
        True,
        MagicMock(
            url=MagicMock(
                hostname="localhost",
                port=port,
                username="user",
                password="pass",
            ),
            tls=tls,
            prot_p=False,
            passive=True,
            binary=True,
            implicit_ftps=implicit_ftps,
        ),
    )


def test_connect_closes_implicit_ftps_on_login_failure():
    # Implicit FTPS path: IMPLICIT_FTP_TLS().connect() + login().
    # If login raises, the constructed object must be closed.
    transfer = make_ftp_transfer()
    _set_credentials(transfer, tls=True, implicit_ftps=True, port=990)

    mock_ftp = MagicMock()
    mock_ftp.connect.return_value = None
    mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")

    with patch("sarracenia.transfer.ftp.IMPLICIT_FTP_TLS", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def test_connect_closes_ftp_tls_on_set_pasv_failure():
    # Explicit FTPS path: ftplib.FTP_TLS(host, user, password) connects+logs in
    # via its constructor, then set_pasv runs separately. If set_pasv raises,
    # the constructed FTP_TLS object must be closed.
    transfer = make_ftp_transfer()
    _set_credentials(transfer, tls=True, implicit_ftps=False)

    mock_ftp = MagicMock()
    mock_ftp.set_pasv.side_effect = OSError("set_pasv failed")

    with patch("ftplib.FTP_TLS", return_value=mock_ftp):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
    mock_ftp.close.assert_called_once()


def test_connect_ftp_tls_constructor_failure_does_not_crash_cleanup():
    # FTP_TLS connects+logs in via constructor. If the constructor raises,
    # the local ftp variable is never assigned and remains None. Cleanup
    # must skip ftp.close() rather than dereference None.
    transfer = make_ftp_transfer()
    _set_credentials(transfer, tls=True, implicit_ftps=False)

    with patch("ftplib.FTP_TLS", side_effect=ftplib.error_perm("530 denied")):
        with patch("sarracenia.transfer.ftp.alarm_set"):
            with patch("sarracenia.transfer.ftp.alarm_cancel"):
                result = transfer.connect()

    assert result is False
    assert transfer.connected is False
