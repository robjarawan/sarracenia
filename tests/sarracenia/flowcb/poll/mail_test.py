import pytest
import types
from unittest.mock import patch, MagicMock
from tests.conftest import *

import imaplib
import poplib

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.mail import Mail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_options(scheme="imaps", username="user", password="pass",
                  hostname="mail.example.com", port=None):
    """Return an options object wired with a fake credentials store."""
    options = sarracenia.config.default_config()
    options.pollUrl = f"{scheme}://{username}@{hostname}/"

    url_ns = types.SimpleNamespace(
        username=username,
        password=password,
        hostname=hostname,
        scheme=scheme,
        port=port,
    )
    cred = types.SimpleNamespace(url=url_ns)
    cred_db = MagicMock()
    cred_db.get.return_value = (True, cred)
    options.credentials = cred_db
    return options


def _make_instance(options=None, **kw):
    """Create a Mail instance without triggering Poll.__init__."""
    if options is None:
        options = _make_options(**kw)
    inst = Mail.__new__(Mail)
    inst.o = options
    inst.metrics = {
        'transferRxBytes': 0,
        'transferRxFiles': 0,
        'transferTxBytes': 0,
        'transferTxFiles': 0,
    }
    return inst


def _raw_email(subject="Test Subject"):
    """Return a raw RFC‑822 message as bytes."""
    return (
        f"From: sender@example.com\r\n"
        f"To: receiver@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"\r\n"
        f"Body text.\r\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Test_Mail_init
# ---------------------------------------------------------------------------

class Test_Mail_init:
    def test_init_sets_options(self):
        options = _make_options()
        inst = Mail(options)
        assert inst.o is options


# ---------------------------------------------------------------------------
# Test_Mail_poll_credentials
# ---------------------------------------------------------------------------

class Test_Mail_poll_credentials:
    def test_credentials_failure_returns_empty_list(self):
        """When credentials.get returns (False, None) the method now returns []
        (previously returned None — fixed for contract consistency)."""
        inst = _make_instance(scheme="imaps")
        inst.o.credentials.get.return_value = (False, None)
        result = inst.poll()
        assert result == []

    def test_credentials_success_sets_protocol(self):
        """Valid credentials should allow the poll to proceed (not return None)."""
        inst = _make_instance(scheme="imaps")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={"fake": True}):
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.search.return_value = ("OK", [b""])
            mock_conn.select.return_value = ("OK", [b"1"])
            result = inst.poll()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Test_Mail_poll_imaps
# ---------------------------------------------------------------------------

class Test_Mail_poll_imaps:
    def test_imaps_happy_path_single_message(self):
        inst = _make_instance(scheme="imaps")
        raw = _raw_email("Hello")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={"msg": 1}) as mock_fi:
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"1"])
            mock_conn.search.return_value = ("OK", [b"1"])
            mock_conn.fetch.return_value = ("OK", [(b"1", raw)])
            result = inst.poll()
        assert len(result) == 1
        assert result[0] == {"msg": 1}
        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()

    def test_imaps_happy_path_multiple_messages(self):
        inst = _make_instance(scheme="imaps")
        raw1 = _raw_email("Msg1")
        raw2 = _raw_email("Msg2")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={"m": True}):
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"2"])
            mock_conn.search.return_value = ("OK", [b"1 2"])
            mock_conn.fetch.side_effect = [
                ("OK", [(b"1", raw1)]),
                ("OK", [(b"2", raw2)]),
            ]
            result = inst.poll()
        assert len(result) == 2

    def test_imaps_empty_inbox(self):
        inst = _make_instance(scheme="imaps")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo") as mock_fi:
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"0"])
            mock_conn.search.return_value = ("OK", [b""])
            result = inst.poll()
        assert result == []
        mock_fi.assert_not_called()

    def test_imaps_connection_error_returns_empty_list(self):
        inst = _make_instance(scheme="imaps")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
            mock_imap.IMAP4_SSL.side_effect = imaplib.IMAP4.error("conn refused")
            mock_imap.IMAP4 = imaplib.IMAP4  # keep real error class accessible
            result = inst.poll()
        assert result == []

    def test_imaps_default_port_993(self):
        inst = _make_instance(scheme="imaps", port=None)
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"0"])
            mock_conn.search.return_value = ("OK", [b""])
            inst.poll()
        mock_imap.IMAP4_SSL.assert_called_once_with("mail.example.com", port=993)


# ---------------------------------------------------------------------------
# Test_Mail_poll_imap
# ---------------------------------------------------------------------------

class Test_Mail_poll_imap:
    def test_imap_happy_path(self):
        inst = _make_instance(scheme="imap")
        raw = _raw_email("Subject IMAP")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={"ok": True}):
            mock_conn = MagicMock()
            mock_imap.IMAP4.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"1"])
            mock_conn.search.return_value = ("OK", [b"1"])
            mock_conn.fetch.return_value = ("OK", [(b"1", raw)])
            result = inst.poll()
        assert len(result) == 1
        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()

    def test_imap_connection_error_returns_empty_list(self):
        inst = _make_instance(scheme="imap")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
            mock_imap.IMAP4.side_effect = imaplib.IMAP4.error("refused")
            mock_imap.IMAP4.error = imaplib.IMAP4.error
            result = inst.poll()
        assert result == []

    def test_imap_default_port_143(self):
        inst = _make_instance(scheme="imap", port=None)
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_imap.IMAP4.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"0"])
            mock_conn.search.return_value = ("OK", [b""])
            inst.poll()
        mock_imap.IMAP4.assert_called_once_with("mail.example.com", port=143)


# ---------------------------------------------------------------------------
# Test_Mail_poll_pops
# ---------------------------------------------------------------------------

class Test_Mail_poll_pops:
    def test_pops_happy_path(self):
        inst = _make_instance(scheme="pops")
        lines = [b"From: a@b.com", b"Subject: PopS Msg", b"", b"Body"]
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop, \
             patch("sarracenia.Message.fromFileInfo", return_value={"pop": True}):
            mock_conn = MagicMock()
            mock_pop.POP3_SSL.return_value = mock_conn
            mock_conn.list.return_value = ("+OK", [b"1 100"], 0)
            mock_conn.retr.return_value = ("+OK", lines, 0)
            result = inst.poll()
        assert len(result) == 1
        mock_conn.quit.assert_called_once()

    def test_pops_connection_error_returns_empty_list(self):
        inst = _make_instance(scheme="pops")
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop:
            mock_pop.POP3_SSL.side_effect = poplib.error_proto("refused")
            mock_pop.error_proto = poplib.error_proto
            result = inst.poll()
        assert result == []

    def test_pops_default_port_995(self):
        inst = _make_instance(scheme="pops", port=None)
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_pop.POP3_SSL.return_value = mock_conn
            mock_conn.list.return_value = ("+OK", [], 0)
            inst.poll()
        mock_pop.POP3_SSL.assert_called_once_with("mail.example.com", port=995)


# ---------------------------------------------------------------------------
# Test_Mail_poll_pop
# ---------------------------------------------------------------------------

class Test_Mail_poll_pop:
    def test_pop_happy_path(self):
        inst = _make_instance(scheme="pop")
        lines = [b"From: a@b.com", b"Subject: Pop Msg", b"", b"Body"]
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop, \
             patch("sarracenia.Message.fromFileInfo", return_value={"pop": True}):
            mock_conn = MagicMock()
            mock_pop.POP3.return_value = mock_conn
            mock_conn.list.return_value = ("+OK", [b"1 100"], 0)
            mock_conn.retr.return_value = ("+OK", lines, 0)
            result = inst.poll()
        assert len(result) == 1
        mock_conn.quit.assert_called_once()

    def test_pop_connection_error_returns_empty_list(self):
        inst = _make_instance(scheme="pop")
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop:
            mock_pop.POP3.side_effect = poplib.error_proto("refused")
            mock_pop.error_proto = poplib.error_proto
            result = inst.poll()
        assert result == []

    def test_pop_default_port_110(self):
        inst = _make_instance(scheme="pop", port=None)
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_pop.POP3.return_value = mock_conn
            mock_conn.list.return_value = ("+OK", [], 0)
            inst.poll()
        mock_pop.POP3.assert_called_once_with("mail.example.com", port=110)


# ---------------------------------------------------------------------------
# Test_Mail_poll_edge
# ---------------------------------------------------------------------------

class Test_Mail_poll_edge:
    def test_unknown_protocol_returns_empty_list(self):
        """A scheme that contains neither 'imap' nor 'pop' still returns
        gathered_messages (an empty list), after logging an error."""
        inst = _make_instance(scheme="smtp")
        result = inst.poll()
        assert result == []

    def test_metrics_updated_imap(self):
        """IMAP path should increment transferRxBytes by len(data)."""
        inst = _make_instance(scheme="imaps")
        raw = _raw_email("Metrics")
        with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"1"])
            # data returned by search is a list; code does len(data)
            search_data = [b"1"]
            mock_conn.search.return_value = ("OK", search_data)
            mock_conn.fetch.return_value = ("OK", [(b"1", raw)])
            inst.poll()
        # len(data) where data == [b"1"] → list length is 1
        assert inst.metrics['transferRxBytes'] == len(search_data)

    def test_metrics_updated_pop(self):
        """POP path should increment transferRxBytes by cumulative line lengths."""
        inst = _make_instance(scheme="pops")
        lines = [b"From: a@b.com", b"Subject: M", b"", b"Body line"]
        expected_bytes = sum(len(line) for line in lines)
        with patch("sarracenia.flowcb.poll.mail.poplib") as mock_pop, \
             patch("sarracenia.Message.fromFileInfo", return_value={}):
            mock_conn = MagicMock()
            mock_pop.POP3_SSL.return_value = mock_conn
            mock_conn.list.return_value = ("+OK", [b"1 100"], 0)
            mock_conn.retr.return_value = ("+OK", lines, 0)
            inst.poll()
        assert inst.metrics['transferRxBytes'] == expected_bytes