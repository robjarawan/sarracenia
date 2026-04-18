import logging
from unittest.mock import MagicMock, patch

import pytest

import sarracenia
import sarracenia.config
import sarracenia.transfer
import sarracenia.transfer.sftp

from tests.conftest import *  # noqa: F401,F403

logger = logging.getLogger('sarracenia.transfer.sftp')
logger.setLevel('DEBUG')


def _make_sftp(use_compression=None):
    """Build a real Sftp instance without opening a socket.

    The Sftp constructor only reads ~/.ssh/config and registers options, so it
    is safe to instantiate directly. Credentials and network I/O only happen
    inside connect().
    """
    options = sarracenia.config.default_config()
    xfer = sarracenia.transfer.sftp.Sftp('sftp', options)
    if use_compression is not None:
        xfer.o.useCompression = use_compression
    xfer.host = 'testhost'
    xfer.port = 22
    xfer.user = 'tester'
    xfer.password = None
    xfer.ssh_keyfile = None
    xfer.sendTo = 'sftp://tester@testhost/'
    xfer.o.sendTo = xfer.sendTo
    return xfer


def test_useCompression_option_registered():
    """The useCompression option is added by Sftp.__init__ and defaults False."""
    xfer = _make_sftp()
    assert hasattr(xfer.o, 'useCompression')
    assert xfer.o.useCompression is False


def test_useCompression_can_be_enabled():
    """Setting the flag on the options object is preserved."""
    xfer = _make_sftp(use_compression=True)
    assert xfer.o.useCompression is True


def test_connect_passes_compress_false_by_default():
    """Default behaviour: ssh.connect() is called with compress=False."""
    xfer = _make_sftp(use_compression=False)

    fake_client = MagicMock()
    fake_transport = MagicMock(remote_compression='none')
    fake_client.get_transport.return_value = fake_transport
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    assert fake_client.connect.called
    _, kwargs = fake_client.connect.call_args
    assert kwargs.get('compress') is False


def test_connect_passes_compress_true_when_enabled():
    """With useCompression True, ssh.connect() must receive compress=True."""
    xfer = _make_sftp(use_compression=True)

    fake_client = MagicMock()
    fake_transport = MagicMock(remote_compression='zlib@openssh.com')
    fake_client.get_transport.return_value = fake_transport
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    _, kwargs = fake_client.connect.call_args
    assert kwargs.get('compress') is True


def test_connect_passes_compress_true_with_password():
    """Password auth branch must also receive compress=useCompression."""
    xfer = _make_sftp(use_compression=True)
    xfer.password = 'secret'

    fake_client = MagicMock()
    fake_transport = MagicMock(remote_compression='zlib@openssh.com')
    fake_client.get_transport.return_value = fake_transport
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    _, kwargs = fake_client.connect.call_args
    assert kwargs.get('compress') is True
    # password branch uses allow_agent=False and look_for_keys=False
    assert kwargs.get('allow_agent') is False
    assert kwargs.get('look_for_keys') is False


def test_connect_logs_negotiated_compression(caplog):
    """When compression is on, negotiated algorithm is logged at INFO."""
    xfer = _make_sftp(use_compression=True)

    fake_client = MagicMock()
    fake_transport = MagicMock(remote_compression='zlib@openssh.com')
    fake_client.get_transport.return_value = fake_transport
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with caplog.at_level(logging.INFO, logger='sarracenia.transfer.sftp'), \
         patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        xfer.connect()

    info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any('compression requested' in m and 'zlib@openssh.com' in m
               for m in info_messages), \
        f"expected negotiated-compression INFO log, got {info_messages}"


def test_connect_does_not_log_when_disabled(caplog):
    """When compression is off, no compression-INFO message is emitted."""
    xfer = _make_sftp(use_compression=False)

    fake_client = MagicMock()
    fake_transport = MagicMock(remote_compression='none')
    fake_client.get_transport.return_value = fake_transport
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with caplog.at_level(logging.INFO, logger='sarracenia.transfer.sftp'), \
         patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        xfer.connect()

    info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert not any('compression' in m for m in info_messages), \
        f"did not expect compression log, got {info_messages}"


def test_connect_tolerates_unreadable_transport(caplog):
    """If get_transport() raises, we still log once and return success."""
    xfer = _make_sftp(use_compression=True)

    fake_client = MagicMock()
    fake_client.get_transport.side_effect = RuntimeError('transport gone')
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = '/home/tester'
    fake_client.open_sftp.return_value = fake_sftp

    with caplog.at_level(logging.INFO, logger='sarracenia.transfer.sftp'), \
         patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any('negotiation unreadable' in m for m in info_messages)


def test_connect_failure_still_returns_false():
    """If ssh.connect() raises, connect() returns False and does not crash."""
    xfer = _make_sftp(use_compression=True)

    fake_client = MagicMock()
    fake_client.connect.side_effect = OSError('unreachable')

    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is False
    assert xfer.connected is False


def test_registered_as_includes_sftp_and_ssh():
    """Regression guard on the transport scheme registration."""
    schemes = sarracenia.transfer.sftp.Sftp.registered_as()
    assert 'sftp' in schemes
    assert 'ssh' in schemes
    assert 'scp' in schemes
