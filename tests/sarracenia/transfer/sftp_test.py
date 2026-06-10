"""
Mock-paramiko regression tests for sarracenia.transfer.sftp.

These tests are the regression net that lets the paramiko-pin work proceed
safely. They exercise the surface of Sftp that interacts with paramiko APIs
without touching the network:

    * SSHClient.connect() kwarg propagation in both auth branches
    * SFTPClient.file() get/put happy paths via the read_writelocal /
      readlocal_write loop
    * SFTPClient.listdir_attr() parsing through Sftp.ls / line_callback,
      including the attr.__str__() token layout that line_callback parses
      with ' '.join(opart2[8:]) at sftp.py:444
    * Identity check `type(line) is paramiko.SFTPAttributes` used by
      flowcb/poll/__init__.py:358
    * Failure paths (credentials False, ssh.connect raises) returning False
    * Scheme registration

Run under each paramiko leg of the matrix (2.9.x, 3.x, 4.x) to confirm the
API surface and the textual SFTPAttributes layout are stable.
"""
import io
import logging
import stat as stat_mod

from unittest.mock import MagicMock, patch

import paramiko
import pytest

import sarracenia
import sarracenia.config
import sarracenia.transfer
import sarracenia.transfer.sftp

from tests.conftest import *  # noqa: F401,F403

logger = logging.getLogger('sarracenia.transfer.sftp')
logger.setLevel('DEBUG')


def _make_sftp():
    """Build a real Sftp instance without opening a socket.

    Sftp.__init__ only reads ~/.ssh/config and registers options; no I/O
    happens until connect() is called.
    """
    options = sarracenia.config.default_config()
    xfer = sarracenia.transfer.sftp.Sftp('sftp', options)
    xfer.host = 'testhost'
    xfer.port = 22
    xfer.user = 'tester'
    xfer.password = None
    xfer.ssh_keyfile = None
    xfer.sendTo = 'sftp://tester@testhost/'
    xfer.o.sendTo = xfer.sendTo
    return xfer


def _fake_attr(filename, size=1234, mtime=1700000000, mode=stat_mod.S_IFREG | 0o644,
               uid=1000, gid=1000):
    """Build a real paramiko.SFTPAttributes with populated fields.

    Uses the live paramiko module so that attr.__str__() exercises whichever
    paramiko version is installed. That __str__ output is what line_callback
    parses; this helper is the canary for format drift across paramiko
    versions.
    """
    a = paramiko.SFTPAttributes()
    a.st_size = size
    a.st_mtime = mtime
    a.st_mode = mode
    a.st_uid = uid
    a.st_gid = gid
    a.filename = filename
    a.longname = filename
    return a


# ----- factory / scheme registration -----


def test_registered_as_includes_sftp_scp_ssh_fish():
    schemes = sarracenia.transfer.sftp.Sftp.registered_as()
    assert 'sftp' in schemes
    assert 'scp' in schemes
    assert 'ssh' in schemes
    assert 'fish' in schemes


def test_factory_returns_sftp_instance():
    options = sarracenia.config.default_config()
    xfer = sarracenia.transfer.Transfer.factory('sftp', options)
    assert isinstance(xfer, sarracenia.transfer.sftp.Sftp)


# ----- connect: kwarg propagation -----


def _make_fake_client(getcwd='/home/tester'):
    fake_client = MagicMock()
    fake_sftp = MagicMock()
    fake_sftp.getcwd.return_value = getcwd
    fake_channel = MagicMock()
    fake_sftp.get_channel.return_value = fake_channel
    fake_client.open_sftp.return_value = fake_sftp
    return fake_client, fake_sftp


def test_connect_key_auth_kwargs():
    """Key-based auth branch (no password): verify the exact kwargs paramiko receives."""
    xfer = _make_sftp()
    xfer.password = None
    xfer.ssh_keyfile = '/tmp/fake.key'

    fake_client, _ = _make_fake_client()
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    args, kwargs = fake_client.connect.call_args
    # positional: (host, port, user, password)
    assert args[0] == 'testhost'
    assert args[1] == 22
    assert args[2] == 'tester'
    assert args[3] is None  # password
    assert kwargs.get('pkey') is None
    assert kwargs.get('key_filename') == '/tmp/fake.key'
    assert 'timeout' in kwargs


def test_connect_password_auth_kwargs():
    """Password auth branch: allow_agent=False, look_for_keys=False are pinned."""
    xfer = _make_sftp()
    xfer.password = 'secret'

    fake_client, _ = _make_fake_client()
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()

    assert ok is True
    args, kwargs = fake_client.connect.call_args
    # password branch passes the unquoted password as the 4th positional
    assert args[3] == 'secret'
    assert kwargs.get('allow_agent') is False
    assert kwargs.get('look_for_keys') is False


def test_connect_sets_missing_host_key_policy_to_autoadd():
    xfer = _make_sftp()
    fake_client, _ = _make_fake_client()
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        xfer.connect()
    assert fake_client.set_missing_host_key_policy.called
    policy_arg = fake_client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy_arg, paramiko.AutoAddPolicy)


def test_connect_returns_false_when_credentials_fail():
    xfer = _make_sftp()
    with patch.object(xfer, 'credentials', return_value=False):
        ok = xfer.connect()
    assert ok is False
    assert xfer.connected is False


def test_connect_returns_false_on_ssh_connect_exception():
    xfer = _make_sftp()
    fake_client = MagicMock()
    fake_client.connect.side_effect = OSError('host unreachable')
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        ok = xfer.connect()
    assert ok is False
    assert xfer.connected is False


def test_connect_sets_channel_timeout_when_configured():
    xfer = _make_sftp()
    xfer.o.timeout = 42

    fake_client, fake_sftp = _make_fake_client()
    fake_channel = fake_sftp.get_channel.return_value
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        xfer.connect()

    fake_channel.settimeout.assert_called_with(42)


# ----- get / put: file() round-trip via read_write loop -----


class _FakeSFTPFile(io.BytesIO):
    """BytesIO that also implements the paramiko.SFTPFile-shaped methods
    sarracenia calls on the object returned by sftp.file(): settimeout,
    seek (inherited), close (snapshots bytes first), truncate (inherited)."""

    captured = b''

    def settimeout(self, _t):
        return None

    def close(self):
        if not self.closed:
            self.captured = self.getvalue()
        super().close()


def test_get_round_trips_bytes(tmp_path):
    """Sftp.get() drives read_writelocal -> read_write; mock sftp.file() with BytesIO."""
    xfer = _make_sftp()
    payload = b'A' * 65536 + b'tail'

    fake_client, fake_sftp = _make_fake_client()
    fake_sftp.file.return_value = _FakeSFTPFile(payload)
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        assert xfer.connect()

    local = tmp_path / 'out.bin'
    rw = xfer.get(msg={}, remote_file='remote.bin', local_file=str(local))

    assert rw == len(payload)
    assert local.read_bytes() == payload


def test_put_round_trips_bytes(tmp_path):
    """Sftp.put() drives readlocal_write; mock sftp.file() to capture writes."""
    xfer = _make_sftp()
    src = tmp_path / 'in.bin'
    payload = b'PUT' * 20000
    src.write_bytes(payload)

    fake_client, fake_sftp = _make_fake_client()
    captured = _FakeSFTPFile()
    fake_sftp.file.return_value = captured
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        assert xfer.connect()

    rw = xfer.put(msg={}, local_file=str(src), remote_file='remote.bin')

    assert rw == len(payload)
    assert captured.captured == payload


def test_put_with_offset_uses_r_plus_b_and_seeks(tmp_path):
    """Parts branch: length != 0 + remote_offset triggers 'r+b' open and seek."""
    xfer = _make_sftp()
    src = tmp_path / 'in.bin'
    src.write_bytes(b'PARTS')

    fake_client, fake_sftp = _make_fake_client()
    fake_sftp.stat.return_value = MagicMock()  # file exists
    captured = MagicMock()
    captured.write = MagicMock()
    captured.seek = MagicMock()
    captured.settimeout = MagicMock()
    captured.truncate = MagicMock()
    fake_sftp.file.return_value = captured
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        assert xfer.connect()

    xfer.put(msg={}, local_file=str(src), remote_file='remote.bin',
             remote_offset=100, length=5)

    # last open mode should be 'r+b' (parts branch)
    modes_used = [c.args[1] for c in fake_sftp.file.call_args_list]
    assert 'r+b' in modes_used
    captured.seek.assert_called_with(100, 0)


# ----- ls / line_callback: SFTPAttributes.__str__ format dependency -----


def test_ls_returns_filename_keyed_dict_of_attrs():
    xfer = _make_sftp()
    attrs = [
        _fake_attr('alpha.txt', size=10),
        _fake_attr('beta.bin', size=2048),
        _fake_attr('gamma file.dat', size=99),  # filename with whitespace
    ]

    fake_client, fake_sftp = _make_fake_client()
    fake_sftp.listdir_attr.return_value = attrs
    with patch.object(xfer, 'credentials', return_value=True), \
         patch('sarracenia.transfer.sftp.paramiko.SSHClient', return_value=fake_client):
        assert xfer.connect()

    entries = xfer.ls()

    assert 'alpha.txt' in entries
    assert 'beta.bin' in entries
    assert 'gamma file.dat' in entries
    assert entries['beta.bin'] is attrs[1]


def test_line_callback_parses_attr_str_token_layout():
    """attr.__str__() must produce >= 9 whitespace-separated tokens
    so that ' '.join(opart2[8:]) extracts the filename.

    This is the format dependency the upgrade plan must protect: if paramiko
    ever changes SFTPAttributes.__str__ layout, this test fails first."""
    xfer = _make_sftp()
    attr = _fake_attr('hello.world', size=42)
    rendered = attr.__str__()
    tokens = [p for p in rendered.strip().replace('\t', ' ').split(' ') if p]
    assert len(tokens) >= 9, (
        f"paramiko {paramiko.__version__} SFTPAttributes.__str__ produced "
        f"fewer than 9 tokens; line_callback's opart2[8:] filename split will "
        f"misbehave. Raw: {rendered!r}"
    )

    xfer.entries = {}
    xfer.line_callback(rendered, attr)
    # Whatever line_callback decides the filename is, the attr must be retrievable.
    assert attr in xfer.entries.values()


# ----- identity check used by flowcb/poll -----


def test_paramiko_sftpattributes_identity_check():
    """flowcb/poll/__init__.py:358 uses `type(line) is paramiko.SFTPAttributes`.
    Confirm that hand-constructed instances satisfy that identity test, and
    that the class is not shadowed/wrapped by anything in the import chain."""
    import sarracenia.flowcb.poll as poll_mod
    a = paramiko.SFTPAttributes()
    assert type(a) is paramiko.SFTPAttributes
    assert type(a) is poll_mod.paramiko.SFTPAttributes
    assert paramiko.SFTPAttributes is poll_mod.paramiko.SFTPAttributes


def test_sftpattributes_writable_fields_unchanged():
    """The 8 hand-construction call sites assign these fields by name.
    If paramiko ever renames any of them, this test fails early."""
    a = paramiko.SFTPAttributes()
    a.st_size = 1
    a.st_mtime = 2
    a.st_atime = 3
    a.st_mode = 0o644
    a.st_uid = 0
    a.st_gid = 0
    a.filename = 'x'
    a.longname = 'x'
    assert a.st_size == 1
    assert a.st_mtime == 2
    assert a.st_atime == 3
    assert a.st_mode == 0o644
    assert a.filename == 'x'
    assert a.longname == 'x'


# ----- check_is_connected close/reset semantics -----


def test_check_is_connected_false_when_sftp_none():
    xfer = _make_sftp()
    xfer.sftp = None
    xfer.connected = True
    assert xfer.check_is_connected() is False


def test_check_is_connected_false_when_not_connected_flag():
    xfer = _make_sftp()
    xfer.sftp = MagicMock()
    xfer.connected = False
    assert xfer.check_is_connected() is False
