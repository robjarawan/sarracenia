"""Cross-protocol transfer contract tests: FTP, SFTP, HTTPS error-state invariants.

Focus areas:
1. Connect failure → disconnected state invariants
2. No unconditional state=True after failure  
3. Close/disconnect idempotency
4. Exception translation
5. Retry/no-retry decisions
6. Metadata/stat failure behavior
7. Path normalization
8. Partial transfer / offset / cleanup behavior
9. Consistency across protocols
"""
import pytest
import types
import ftplib
import os
import ssl
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch, PropertyMock, call
from stat import S_ISDIR

import sarracenia.config
from sarracenia.transfer.ftp import Ftp
from sarracenia.transfer.sftp import Sftp
from sarracenia.transfer.https import Https
from sarracenia.transfer import alarm_cancel, alarm_set


# === Helpers ===

def make_options(**overrides):
    options = sarracenia.config.default_config()
    options.timeout = 300
    options.bufSize = 1024 * 1024
    options.batch = 100
    options.sendTo = 'ftp://user:pass@host.example.com/path'
    options.permDirDefault = 0o755
    options.nofsetstat = False
    for k, v in overrides.items():
        setattr(options, k, v)
    return options


def make_ftp(**overrides):
    options = make_options(sendTo='ftp://user:pass@host.example.com/path', **overrides)
    return Ftp('ftp', options)

def make_sftp(**overrides):
    options = make_options(sendTo='sftp://user:pass@host.example.com/path', **overrides)
    with patch('paramiko.SSHConfig'), patch('os.path.isfile', return_value=False):
        return Sftp('sftp', options)

def make_https(**overrides):
    options = make_options(sendTo='https://user:pass@host.example.com/path', **overrides)
    options.tlsRigour = 'normal'
    with patch('ssl.SSLContext'):
        return Https('https', options)


# === 1. Connect failure → disconnected state invariants ===

class Test_connect_failure_leaves_disconnected:
    """When connect() fails for any reason, self.connected MUST be False afterward."""

    def test_ftp_connect_failure_credentials(self):
        inst = make_ftp()
        inst.o.credentials = MagicMock()
        inst.o.credentials.get.side_effect = Exception("no creds")
        result = inst.connect()
        assert result is False
        assert inst.connected is False

    def test_sftp_connect_failure_credentials(self):
        inst = make_sftp()
        inst.o.credentials = MagicMock()
        inst.o.credentials.get.side_effect = Exception("no creds")
        result = inst.connect()
        assert result is False
        assert inst.connected is False

    def test_https_connect_failure_credentials(self):
        inst = make_https()
        inst.o.credentials = MagicMock()
        inst.o.credentials.get.side_effect = Exception("no creds")
        with patch.object(inst, 'credentials', return_value=False):
            result = inst.connect()
        assert result is False
        assert inst.connected is False

    def test_ftp_connect_network_failure(self):
        """FTP connect fails during socket connect."""
        inst = make_ftp()
        inst.o.credentials = MagicMock()
        mock_url = MagicMock()
        mock_url.hostname = 'host.example.com'
        mock_url.port = 21
        mock_url.username = 'user'
        mock_url.password = 'pass'
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.passive = True
        mock_details.binary = True
        mock_details.tls = False
        mock_details.prot_p = False
        mock_details.implicit_ftps = False
        inst.o.credentials.get.return_value = (True, mock_details)

        with patch('ftplib.FTP') as MockFTP:
            MockFTP.return_value.connect.side_effect = ConnectionRefusedError("refused")
            result = inst.connect()
        assert result is False
        assert inst.connected is False

    def test_sftp_connect_network_failure(self):
        """SFTP connect fails during SSH connect."""
        inst = make_sftp()
        inst.o.credentials = MagicMock()
        mock_url = MagicMock()
        mock_url.hostname = 'host.example.com'
        mock_url.port = 22
        mock_url.username = 'user'
        mock_url.password = 'pass'
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.ssh_keyfile = None
        inst.o.credentials.get.return_value = (True, mock_details)

        with patch('paramiko.SSHClient') as MockSSH:
            MockSSH.return_value.connect.side_effect = ConnectionRefusedError("refused")
            result = inst.connect()
        assert result is False
        assert inst.connected is False


# === 2. No unconditional state=True after failure ===

class Test_no_unconditional_connected_true:
    """Verify that connect never sets connected=True when it should be False."""

    def test_ftp_connect_sets_false_before_attempt(self):
        """connected should be False at start of connect()."""
        inst = make_ftp()
        inst.connected = True  # Start as True
        inst.o.credentials = MagicMock()
        inst.o.credentials.get.side_effect = Exception("fail")
        inst.connect()
        assert inst.connected is False  # Must be reset

    def test_sftp_connect_sets_false_before_attempt(self):
        inst = make_sftp()
        inst.connected = True
        inst.o.credentials = MagicMock()
        inst.o.credentials.get.side_effect = Exception("fail")
        inst.connect()
        assert inst.connected is False

    def test_https_connect_opener_creation_fails(self):
        """If opener creation raises, connected must be False."""
        inst = make_https()
        with patch('urllib.request.HTTPPasswordMgrWithDefaultRealm', side_effect=Exception("broken")):
            result = inst.connect()
        assert inst.connected is False


# === 3. Close/disconnect idempotency ===

class Test_close_idempotency:
    """Calling close() multiple times must not raise."""

    def test_ftp_double_close(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.connected = True
        inst.close()
        # Second close - ftp object still present (base init doesn't reset it)
        inst.close()  # Must not raise

    def test_sftp_double_close(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.ssh = MagicMock()
        inst.connected = True
        inst.close()
        inst.close()  # Must not raise

    def test_https_double_close(self):
        inst = make_https()
        inst.connected = True
        inst.close()
        inst.close()  # Must not raise

    def test_ftp_close_calls_quit(self):
        """close() should attempt to quit the FTP connection."""
        inst = make_ftp()
        mock_ftp = MagicMock()
        inst.ftp = mock_ftp
        inst.connected = True
        inst.close()
        mock_ftp.quit.assert_called_once()

    def test_sftp_close_calls_close_on_both(self):
        """close() should close both SFTP and SSH channels."""
        inst = make_sftp()
        mock_sftp = MagicMock()
        mock_ssh = MagicMock()
        inst.sftp = mock_sftp
        inst.ssh = mock_ssh
        inst.connected = True
        inst.close()
        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()

    def test_https_close_resets_state(self):
        inst = make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.close()
        assert inst.connected is False


# === 4. Exception translation ===

class Test_exception_translation:
    """Verify that protocol-specific exceptions are handled, not propagated."""

    def test_ftp_close_swallows_eoferror(self):
        inst = make_ftp()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = EOFError()
        inst.ftp = mock_ftp
        inst.close()  # Must not raise
        mock_ftp.close.assert_called_once()

    def test_ftp_close_swallows_error_temp(self):
        inst = make_ftp()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = ftplib.error_temp("421 timeout")
        inst.ftp = mock_ftp
        inst.close()  # Must not raise
        mock_ftp.close.assert_called_once()

    def test_sftp_close_swallows_exceptions(self):
        inst = make_sftp()
        mock_sftp = MagicMock()
        mock_ssh = MagicMock()
        mock_sftp.close.side_effect = Exception("already closed")
        mock_ssh.close.side_effect = Exception("already closed")
        inst.sftp = mock_sftp
        inst.ssh = mock_ssh
        inst.close()  # Must not raise

    def test_sftp_chmod_swallows_exception(self):
        """chmod failure should log warning but not raise."""
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.sftp.chmod.side_effect = IOError("permission denied")
        inst.o.nofsetstat = False
        # Should not raise
        inst.chmod(0o755, '/some/path')

    def test_sftp_chmod_skipped_when_nofsetstat(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.o.nofsetstat = True
        inst.chmod(0o755, '/some/path')
        inst.sftp.chmod.assert_not_called()


# === 5. Retry/no-retry decisions via check_is_connected ===

class Test_retry_decisions:
    """check_is_connected decides whether to retry or disconnect."""

    def test_ftp_check_returns_false_after_batch_limit(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.batch = inst.o.batch + 1
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()

    def test_sftp_check_returns_false_after_batch_limit(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.batch = inst.o.batch + 1
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()

    def test_ftp_check_detects_sendTo_change(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.connected = True
        inst.sendTo = 'ftp://old-host/path'
        inst.o.sendTo = 'ftp://new-host/path'
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()

    def test_sftp_check_detects_sendTo_change(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.connected = True
        inst.sendTo = 'sftp://old-host/path'
        inst.o.sendTo = 'sftp://new-host/path'
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()

    def test_https_check_detects_sendTo_change(self):
        inst = make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.sendTo = 'https://old-host/path'
        inst.o.sendTo = 'https://new-host/path'
        result = inst.check_is_connected()
        assert result is False

    def test_ftp_check_health_probe_failure(self):
        """When pwd() fails, connection is dropped."""
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.ftp.pwd.side_effect = ConnectionResetError("broken pipe")
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.batch = 0
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()

    def test_sftp_check_health_probe_failure(self):
        """When chdir() fails, connection is dropped."""
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.sftp.chdir.side_effect = ConnectionResetError("broken pipe")
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.batch = 0
        inst.originalDir = '/home'
        inst.close = MagicMock()
        assert inst.check_is_connected() is False
        inst.close.assert_called_once()


# === 6. Metadata/stat failure behavior ===

class Test_stat_failure:
    """stat() should return None on failure, not crash."""

    def test_sftp_stat_returns_none_on_exception(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.sftp.stat.side_effect = IOError("no such file")
        result = inst.stat('/nonexistent/file.txt')
        assert result is None

    def test_sftp_stat_returns_attrs_on_success(self):
        import paramiko
        inst = make_sftp()
        inst.sftp = MagicMock()
        mock_attrs = paramiko.SFTPAttributes()
        mock_attrs.st_size = 1024
        mock_attrs.st_mtime = 1000000
        inst.sftp.stat.return_value = mock_attrs
        result = inst.stat('/some/file.txt')
        assert result is not None


# === 7. SFTP delete behavior ===

class Test_sftp_delete:
    """SFTP delete handles files, directories, and missing paths."""

    def test_delete_file(self):
        import paramiko
        inst = make_sftp()
        inst.sftp = MagicMock()
        mock_stat = MagicMock()
        mock_stat.st_mode = 0o100644  # regular file
        inst.sftp.lstat.return_value = mock_stat
        inst.delete('/some/file.txt')
        inst.sftp.remove.assert_called_once_with('/some/file.txt')

    def test_delete_directory(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        mock_stat = MagicMock()
        mock_stat.st_mode = 0o040755  # directory
        inst.sftp.lstat.return_value = mock_stat
        inst.delete('/some/dir')
        inst.sftp.rmdir.assert_called_once_with('/some/dir')

    def test_delete_nonexistent(self):
        """Delete of nonexistent file should silently return."""
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.sftp.lstat.side_effect = IOError("No such file")
        inst.delete('/nonexistent')
        inst.sftp.remove.assert_not_called()
        inst.sftp.rmdir.assert_not_called()


# === 8. SFTP utime behavior ===

class Test_sftp_utime:
    def test_utime_calls_sftp_utime(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.o.nofsetstat = False
        inst.utime('/some/file', (1000, 2000))
        inst.sftp.utime.assert_called_once_with('/some/file', (1000, 2000))

    def test_utime_skipped_when_nofsetstat(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.o.nofsetstat = True
        inst.utime('/some/file', (1000, 2000))
        inst.sftp.utime.assert_not_called()

    def test_utime_swallows_exception(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.sftp.utime.side_effect = IOError("permission denied")
        inst.o.nofsetstat = False
        # Should not raise
        inst.utime('/some/file', (1000, 2000))


# === 9. SFTP get with offset ===

class Test_sftp_get_offset:
    def test_get_seeks_to_offset(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        mock_rfp = MagicMock()
        inst.sftp.file.return_value = mock_rfp
        inst.read_writelocal = MagicMock(return_value=512)
        msg = {}
        result = inst.get(msg, 'remote.txt', 'local.txt', remote_offset=100)
        mock_rfp.seek.assert_called_once_with(100, 0)
        assert result == 512

    def test_get_no_seek_at_zero_offset(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        mock_rfp = MagicMock()
        inst.sftp.file.return_value = mock_rfp
        inst.read_writelocal = MagicMock(return_value=1024)
        msg = {}
        result = inst.get(msg, 'remote.txt', 'local.txt', remote_offset=0)
        mock_rfp.seek.assert_not_called()
        assert result == 1024


# === 10. FTP get binary/ascii ===

class Test_ftp_get_modes:
    def test_get_binary_mode(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.binary = True
        inst.sumalgo = None
        inst.local_write_open = MagicMock(return_value=MagicMock())
        inst.write_chunk_init = MagicMock()
        inst.write_chunk = MagicMock()
        inst.write_chunk_end = MagicMock(return_value=1024)
        inst.local_write_close = MagicMock()
        msg = {}
        result = inst.get(msg, 'remote.txt', 'local.txt')
        inst.ftp.retrbinary.assert_called_once()
        assert result == 1024

    def test_get_ascii_mode(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.binary = False
        inst.sumalgo = None
        inst.local_write_open = MagicMock(return_value=MagicMock())
        inst.write_chunk_init = MagicMock()
        inst.write_chunk = MagicMock()
        inst.write_chunk_end = MagicMock(return_value=512)
        inst.local_write_close = MagicMock()
        msg = {}
        result = inst.get(msg, 'remote.txt', 'local.txt')
        inst.ftp.retrlines.assert_called_once()
        assert result == 512

    def test_get_exception_does_not_crash(self):
        """FTP get catches exceptions during download."""
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.ftp.retrbinary.side_effect = ftplib.error_temp("temp failure")
        inst.binary = True
        inst.sumalgo = None
        inst.local_write_open = MagicMock(return_value=MagicMock())
        inst.write_chunk_init = MagicMock()
        inst.write_chunk = MagicMock()
        inst.write_chunk_end = MagicMock(return_value=0)
        inst.local_write_close = MagicMock()
        msg = {}
        # Should not raise
        result = inst.get(msg, 'remote.txt', 'local.txt')
        assert result == 0  # write_chunk_end returns 0 on failure


# === 11. FTP put binary/ascii ===

class Test_ftp_put_modes:
    def test_put_binary_mode(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.binary = True
        inst.local_read_open = MagicMock(return_value=MagicMock())
        inst.write_chunk_init = MagicMock()
        inst.write_chunk = MagicMock()
        inst.write_chunk_end = MagicMock(return_value=2048)
        inst.local_read_close = MagicMock()
        msg = {}
        result = inst.put(msg, 'local.txt', 'remote.txt')
        inst.ftp.storbinary.assert_called_once()
        assert result == 2048

    def test_put_ascii_mode(self):
        inst = make_ftp()
        inst.ftp = MagicMock()
        inst.binary = False
        inst.local_read_open = MagicMock(return_value=MagicMock())
        inst.write_chunk_init = MagicMock()
        inst.write_chunk = MagicMock()
        inst.write_chunk_end = MagicMock(return_value=1024)
        inst.local_read_close = MagicMock()
        msg = {}
        result = inst.put(msg, 'local.txt', 'remote.txt')
        inst.ftp.storlines.assert_called_once()
        assert result == 1024


# === 12. SFTP cd_forced ===

class Test_sftp_cd_forced:
    def test_cd_forced_direct_success(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        inst.originalDir = '/home'
        inst.cd_forced('/data/incoming')
        assert inst.sftp.chdir.call_count >= 2

    def test_cd_forced_creates_subdirs(self):
        inst = make_sftp()
        inst.sftp = MagicMock()
        call_count = [0]
        def chdir_side_effect(path):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise IOError("No such directory")
        inst.sftp.chdir.side_effect = chdir_side_effect
        inst.originalDir = '/home'
        inst.cd_forced('/data/new/dir')
        assert inst.sftp.mkdir.call_count > 0


# === 13. FTP connect modes ===

class Test_ftp_connect_modes:
    """Test the 3 FTP connection modes: plain, implicit FTPS, explicit FTPS."""

    def _make_cred_ftp(self, tls=False, implicit_ftps=False, prot_p=False, port=None):
        inst = make_ftp()
        inst.o.credentials = MagicMock()
        mock_url = MagicMock()
        mock_url.hostname = 'host.example.com'
        mock_url.port = port
        mock_url.username = 'user'
        mock_url.password = 'pass'
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.passive = True
        mock_details.binary = True
        mock_details.tls = tls
        mock_details.prot_p = prot_p
        mock_details.implicit_ftps = implicit_ftps
        inst.o.credentials.get.return_value = (True, mock_details)
        return inst

    def test_plain_ftp_connects(self):
        inst = self._make_cred_ftp(tls=False)
        with patch('ftplib.FTP') as MockFTP:
            mock_ftp = MagicMock()
            mock_ftp.pwd.return_value = '/home'
            MockFTP.return_value = mock_ftp
            result = inst.connect()
        assert result is True
        assert inst.connected is True
        assert inst.port == 21

    def test_implicit_ftps_connects(self):
        inst = self._make_cred_ftp(tls=True, implicit_ftps=True, prot_p=True)
        with patch('sarracenia.transfer.ftp.IMPLICIT_FTP_TLS') as MockFTPS:
            mock_ftp = MagicMock()
            mock_ftp.pwd.return_value = '/home'
            MockFTPS.return_value = mock_ftp
            result = inst.connect()
        assert result is True
        assert inst.connected is True
        assert inst.port == 990

    def test_explicit_ftps_connects(self):
        inst = self._make_cred_ftp(tls=True, implicit_ftps=False, prot_p=True)
        with patch('ftplib.FTP_TLS') as MockFTPS:
            mock_ftp = MagicMock()
            mock_ftp.pwd.return_value = '/home'
            MockFTPS.return_value = mock_ftp
            result = inst.connect()
        assert result is True
        assert inst.connected is True

    def test_plain_ftp_default_port(self):
        inst = self._make_cred_ftp(tls=False, port=None)
        with patch('ftplib.FTP') as MockFTP:
            mock_ftp = MagicMock()
            mock_ftp.pwd.return_value = '/home'
            MockFTP.return_value = mock_ftp
            inst.connect()
        assert inst.port == 21

    def test_implicit_ftps_default_port(self):
        inst = self._make_cred_ftp(tls=True, implicit_ftps=True, port=None)
        with patch('sarracenia.transfer.ftp.IMPLICIT_FTP_TLS') as MockFTPS:
            mock_ftp = MagicMock()
            mock_ftp.pwd.return_value = '/home'
            MockFTPS.return_value = mock_ftp
            inst.connect()
        assert inst.port == 990


# === 14. SFTP connect ===

class Test_sftp_connect:
    def _make_cred_sftp(self, password='pass', ssh_keyfile=None, port=22):
        inst = make_sftp()
        inst.o.credentials = MagicMock()
        mock_url = MagicMock()
        mock_url.hostname = 'host.example.com'
        mock_url.port = port
        mock_url.username = 'user'
        mock_url.password = password
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.ssh_keyfile = ssh_keyfile
        inst.o.credentials.get.return_value = (True, mock_details)
        return inst

    def test_sftp_connect_success(self):
        inst = self._make_cred_sftp()
        with patch('paramiko.SSHClient') as MockSSH:
            mock_ssh = MagicMock()
            mock_sftp = MagicMock()
            mock_sftp.getcwd.return_value = '/home'
            mock_ssh.open_sftp.return_value = mock_sftp
            MockSSH.return_value = mock_ssh
            result = inst.connect()
        assert result is True
        assert inst.connected is True

    def test_sftp_connect_closes_existing(self):
        """If already connected, connect() should close first."""
        inst = self._make_cred_sftp()
        inst.connected = True
        inst.sftp = MagicMock()
        inst.ssh = MagicMock()
        with patch('paramiko.SSHClient') as MockSSH:
            mock_ssh = MagicMock()
            mock_sftp = MagicMock()
            mock_sftp.getcwd.return_value = '/home'
            mock_ssh.open_sftp.return_value = mock_sftp
            MockSSH.return_value = mock_ssh
            result = inst.connect()
        assert result is True


# === 15. FTP line_callback edge cases ===

class Test_ftp_line_callback_edge:
    def test_windows_format(self):
        inst = make_ftp()
        inst.entries = {}
        inst.line_callback("08-15-24  02:30PM         12345 file.txt")
        assert 'file.txt' in inst.entries

    def test_filename_with_spaces(self):
        inst = make_ftp()
        inst.entries = {}
        inst.line_callback("-rw-r--r-- 1 user group 100 Jan 1 12:00 my file name.txt")
        assert 'my file name.txt' in inst.entries

    def test_uw_style_extra_auth_field(self):
        """University of Wisconsin FTP has extra auth field - non-numeric 5th column."""
        inst = make_ftp()
        inst.entries = {}
        # UW format: permissions, links, user, group, AUTH_FIELD, size, month, day, time, filename
        # opart2[4] is 'AUTH' (non-numeric), so filename starts at index 9
        inst.line_callback("-rw-r--r-- 1 user group AUTH 100 Jan 1 12:00 file.txt")
        assert 'file.txt' in inst.entries


# === 16. registered_as consistency ===

class Test_registered_as:
    def test_ftp_registered_protocols(self):
        protos = Ftp.registered_as()
        assert 'ftp' in protos

    def test_sftp_registered_protocols(self):
        protos = Sftp.registered_as()
        assert 'sftp' in protos
        assert 'ssh' in protos

    def test_https_registered_protocols(self):
        protos = Https.registered_as()
        assert 'https' in protos
        assert 'http' in protos


# === 17. HTTPS check_is_connected ===

class Test_https_check_is_connected:
    def test_returns_false_when_not_connected(self):
        inst = make_https()
        inst.connected = False
        assert inst.check_is_connected() is False

    def test_returns_false_when_opener_none(self):
        inst = make_https()
        inst.connected = True
        inst.opener = None
        inst.head_opener = MagicMock()
        inst.sendTo = inst.o.sendTo
        assert inst.check_is_connected() is False

    def test_returns_false_when_head_opener_none(self):
        inst = make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = None
        inst.sendTo = inst.o.sendTo
        assert inst.check_is_connected() is False

    def test_returns_true_when_all_set(self):
        inst = make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.sendTo = inst.o.sendTo
        assert inst.check_is_connected() is True


# === 18. HTTPS cd behavior ===

class Test_https_cd:
    def test_cd_sets_path_and_cwd(self):
        inst = make_https()
        inst.cd('/data/incoming/file.txt')
        assert inst.path == '/data/incoming/file.txt'
        assert inst.cwd == '/data/incoming'

    def test_cd_root_path(self):
        inst = make_https()
        inst.cd('/file.txt')
        assert inst.path == '/file.txt'
        assert inst.cwd == '/'
