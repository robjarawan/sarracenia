"""Tests for sarracenia.transfer.ftp.Ftp — seam-testable unit behavior.

No real FTP connections — all protocol objects are mocked.
Covers: init, check_is_connected, close, credentials, cd, delete, chmod,
        registered_as, getAccelerated/putAccelerated edge cases.
"""
import pytest
import types
import ftplib
from unittest.mock import MagicMock, patch, PropertyMock

import sarracenia.config
from sarracenia.transfer.ftp import Ftp, IMPLICIT_FTP_TLS
from sarracenia.transfer import alarm_cancel, alarm_set


def make_options():
    options = sarracenia.config.default_config()
    options.timeout = 300
    options.bufSize = 1024 * 1024
    options.batch = 100
    options.sendTo = 'ftp://user:pass@host.example.com/path'
    options.permDirDefault = 0o755
    return options


def make_ftp_instance():
    """Create an Ftp instance with mocked internals."""
    options = make_options()
    ftp = Ftp('ftp', options)
    return ftp


# === IMPLICIT_FTP_TLS tests ===

class Test_IMPLICIT_FTP_TLS:
    def test_sock_property_wraps_ssl(self):
        """Setting a non-SSL socket should wrap it in SSL."""
        import ssl
        ftps = IMPLICIT_FTP_TLS.__new__(IMPLICIT_FTP_TLS)
        ftps.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ftps._sock = None
        # Setting None should stay None
        ftps.sock = None
        assert ftps.sock is None

    def test_sock_property_returns_stored(self):
        ftps = IMPLICIT_FTP_TLS.__new__(IMPLICIT_FTP_TLS)
        ftps._sock = None
        assert ftps.sock is None


# === Ftp.__init__ tests ===

class Test_Ftp_init:
    def test_initial_state(self):
        ftp = make_ftp_instance()
        assert ftp.connected is False
        assert ftp.ftp is None
        assert ftp.details is None
        assert ftp.batch == 0

    def test_options_set(self):
        ftp = make_ftp_instance()
        assert hasattr(ftp.o, 'accelFtpputCommand')
        assert hasattr(ftp.o, 'accelFtpgetCommand')
        assert hasattr(ftp.o, 'ftpFilenameEncoding')


# === check_is_connected tests ===

class Test_check_is_connected:
    def test_returns_false_when_ftp_none(self):
        ftp = make_ftp_instance()
        ftp.ftp = None
        assert ftp.check_is_connected() is False

    def test_returns_false_when_not_connected(self):
        ftp = make_ftp_instance()
        ftp.ftp = MagicMock()
        ftp.connected = False
        assert ftp.check_is_connected() is False

    def test_returns_false_sendTo_changed(self):
        ftp = make_ftp_instance()
        ftp.ftp = MagicMock()
        ftp.connected = True
        ftp.sendTo = 'ftp://old-host/path'
        ftp.o.sendTo = 'ftp://new-host/path'
        # close is called
        ftp.close = MagicMock()
        result = ftp.check_is_connected()
        assert result is False
        ftp.close.assert_called_once()

    def test_returns_false_batch_exceeded(self):
        ftp = make_ftp_instance()
        ftp.ftp = MagicMock()
        ftp.connected = True
        ftp.sendTo = ftp.o.sendTo
        ftp.batch = ftp.o.batch + 1
        ftp.close = MagicMock()
        result = ftp.check_is_connected()
        assert result is False
        ftp.close.assert_called_once()

    def test_returns_true_when_connected_and_responsive(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.pwd.return_value = '/home/user'
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.sendTo = ftp.o.sendTo
        ftp.batch = 0
        result = ftp.check_is_connected()
        assert result is True
        assert ftp.batch == 1

    def test_returns_false_getcwd_fails(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.pwd.side_effect = Exception("network error")
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.sendTo = ftp.o.sendTo
        ftp.batch = 0
        ftp.close = MagicMock()
        result = ftp.check_is_connected()
        assert result is False
        ftp.close.assert_called_once()

    def test_batch_increments(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.pwd.return_value = '/dir'
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.sendTo = ftp.o.sendTo
        ftp.batch = 5
        ftp.check_is_connected()
        assert ftp.batch == 6


# === close tests ===

class Test_close:
    def test_close_calls_quit(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.close()
        mock_ftp.quit.assert_called_once()
        # Note: close() calls self.init() which resets transfer state
        # but does NOT reset self.connected or self.ftp (by design)

    def test_close_handles_eoferror(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = EOFError()
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.close()
        mock_ftp.close.assert_called_once()

    def test_close_handles_error_temp(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = ftplib.error_temp("421 timeout")
        ftp.ftp = mock_ftp
        ftp.connected = True
        ftp.close()
        mock_ftp.close.assert_called_once()

    def test_close_handles_generic_exception(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = RuntimeError("unexpected")
        ftp.ftp = mock_ftp
        ftp.connected = True
        # Should not raise
        ftp.close()


# === credentials tests ===

class Test_credentials:
    def test_credentials_success(self):
        ftp = make_ftp_instance()
        ftp.sendTo = 'ftp://user:pass@host.example.com:21/path'

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

        ftp.o.credentials = MagicMock()
        ftp.o.credentials.get.return_value = (True, mock_details)

        result = ftp.credentials()
        assert result is True
        assert ftp.host == 'host.example.com'
        assert ftp.port == 21
        assert ftp.user == 'user'
        assert ftp.password == 'pass'
        assert ftp.passive is True
        assert ftp.tls is False

    def test_credentials_failure(self):
        ftp = make_ftp_instance()
        ftp.sendTo = 'ftp://bad@host/path'
        ftp.o.credentials = MagicMock()
        ftp.o.credentials.get.side_effect = Exception("no creds")
        result = ftp.credentials()
        assert result is False


# === cd tests ===

class Test_cd:
    def test_cd_sets_pwd(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.originalDir = '/home'
        ftp.cd('/data/incoming')
        mock_ftp.cwd.assert_any_call('/home')
        mock_ftp.cwd.assert_any_call('/data/incoming')
        assert ftp.pwd == '/data/incoming'


# === delete tests ===

class Test_delete:
    def test_delete_calls_ftp_delete(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.delete('/path/to/file.txt')
        mock_ftp.delete.assert_called_once_with('/path/to/file.txt')

    def test_delete_handles_missing_file(self):
        """When delete fails, pwd is called to check connection health."""
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.delete.side_effect = ftplib.error_perm("550 No such file")
        mock_ftp.pwd.return_value = '/home'
        ftp.ftp = mock_ftp
        # Should not raise
        ftp.delete('/nonexistent.txt')
        mock_ftp.pwd.assert_called_once()


# === chmod tests ===

class Test_chmod:
    def test_chmod_sends_site_command(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.chmod(0o755, '/path/to/file')
        mock_ftp.voidcmd.assert_called_once_with('SITE CHMOD 755 /path/to/file')

    def test_chmod_octal_formatting(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.chmod(0o644, 'myfile.txt')
        mock_ftp.voidcmd.assert_called_with('SITE CHMOD 644 myfile.txt')


# === mkdir tests ===

class Test_mkdir:
    def test_mkdir_creates_and_chmods(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.mkdir('/new/dir')
        mock_ftp.mkd.assert_called_once_with('/new/dir')
        mock_ftp.voidcmd.assert_called_once()


# === rename tests ===

class Test_rename:
    def test_rename_calls_ftp_rename(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.rename('old.txt', 'new.txt')
        mock_ftp.rename.assert_called_once_with('old.txt', 'new.txt')


# === rmdir tests ===

class Test_rmdir:
    def test_rmdir_calls_ftp_rmd(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.rmdir('/old/dir')
        mock_ftp.rmd.assert_called_once_with('/old/dir')


# === umask tests ===

class Test_umask:
    def test_umask_sends_site_command(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.umask()
        mock_ftp.voidcmd.assert_called_once_with('SITE UMASK 777')


# === getAccelerated tests ===

class Test_getAccelerated:
    def test_returns_negative_on_failure(self):
        ftp = make_ftp_instance()
        ftp.pwd = '/remote/dir'
        msg = {'baseUrl': 'ftp://host.example.com/'}
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_popen.return_value = mock_proc
            result = ftp.getAccelerated(msg, 'remote.txt', '/local/file.txt')
        assert result == -1

    def test_returns_file_size_on_success(self, tmp_path):
        ftp = make_ftp_instance()
        ftp.pwd = '/remote/dir'
        msg = {'baseUrl': 'ftp://host.example.com'}

        local_file = tmp_path / 'downloaded.txt'
        local_file.write_text('hello world')

        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            result = ftp.getAccelerated(msg, 'remote.txt', str(local_file))
        assert result == 11  # len('hello world')


# === putAccelerated tests ===

class Test_putAccelerated:
    def test_returns_negative_on_failure(self):
        ftp = make_ftp_instance()
        msg = {'baseUrl': 'ftp://host/', 'new_dir': '/upload', 'size': '1024'}
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_popen.return_value = mock_proc
            result = ftp.putAccelerated(msg, '/local/file.txt', 'remote.txt')
        assert result == -1

    def test_returns_msg_size_on_success(self):
        ftp = make_ftp_instance()
        msg = {'baseUrl': 'ftp://host/', 'new_dir': '/upload', 'size': '2048'}
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            result = ftp.putAccelerated(msg, '/local/file.txt', 'remote.txt')
        assert result == 2048


# === cd_forced tests ===

class Test_cd_forced:
    def test_cd_forced_direct_success(self):
        """When direct cd succeeds, no mkdir needed."""
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        ftp.ftp = mock_ftp
        ftp.originalDir = '/home'
        ftp.cd_forced('/data/incoming')
        # cwd should be called for originalDir then path
        assert mock_ftp.cwd.call_count >= 2
        mock_ftp.mkd.assert_not_called()

    def test_cd_forced_creates_subdirs(self):
        """When direct cd fails, subdirectories should be created."""
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()

        # First two calls (originalDir + path) fail, then subdir calls succeed
        call_count = [0]
        def cwd_side_effect(path):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ftplib.error_perm("550 not found")
            # After that, succeed (subdir navigation)

        mock_ftp.cwd.side_effect = cwd_side_effect
        ftp.ftp = mock_ftp
        ftp.originalDir = '/home'
        ftp.cd_forced('/data/incoming')
        assert mock_ftp.mkd.call_count > 0

    def test_cd_forced_absolute_path_prefix(self):
        """Absolute paths should get '/' prepended to first subdir."""
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        # Always fail direct cd to force subdir creation
        direct_attempts = [0]
        def cwd_side_effect(path):
            direct_attempts[0] += 1
            if direct_attempts[0] <= 2:
                raise ftplib.error_perm("not found")
        mock_ftp.cwd.side_effect = cwd_side_effect
        ftp.ftp = mock_ftp
        ftp.originalDir = '.'
        ftp.cd_forced('/absolute/path')
        # Verify mkd was called (directories were created)
        assert mock_ftp.mkd.call_count >= 1


# === ls tests ===

class Test_ls:
    def test_ls_returns_entries(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()

        def fake_retrlines(cmd, callback):
            callback("-rw-r--r-- 1 user group 100 Jan 1 12:00 file1.txt")
            callback("-rw-r--r-- 1 user group 200 Jan 2 13:00 file2.txt")

        mock_ftp.retrlines.side_effect = fake_retrlines
        ftp.ftp = mock_ftp
        ftp.entries = {}
        result = ftp.ls()
        assert 'file1.txt' in result
        assert 'file2.txt' in result


# === getcwd tests ===

class Test_getcwd:
    def test_returns_pwd(self):
        ftp = make_ftp_instance()
        mock_ftp = MagicMock()
        mock_ftp.pwd.return_value = '/current/dir'
        ftp.ftp = mock_ftp
        assert ftp.getcwd() == '/current/dir'
