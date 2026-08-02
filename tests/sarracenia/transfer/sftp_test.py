import pytest
from unittest.mock import patch, MagicMock

import logging

import sarracenia
import sarracenia.config
from sarracenia.transfer.sftp import Sftp

logger = logging.getLogger(__name__)


@pytest.fixture
def sftp_options():
    options = sarracenia.config.default_config()
    options.sendTo = 'sftp://testuser@testhost/path'
    options.timeout = 5
    options.credentials = MagicMock()
    url = MagicMock()
    url.hostname = 'testhost'
    url.port = 22
    url.username = 'testuser'
    url.password = 'testpass'
    details = MagicMock()
    details.url = url
    details.ssh_keyfile = None
    options.credentials.get.return_value = (True, details)
    return options


class Test_SftpConnectCleanup:
    """Test that failed SFTP connections close sockets to prevent fd leaks."""

    @patch('sarracenia.transfer.sftp.paramiko.SSHClient')
    def test_connect_failure_closes_ssh(self, mock_ssh_class, sftp_options):
        """When ssh.connect() raises, the SSHClient should be closed."""
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = Exception("Connection refused")
        mock_ssh_class.return_value = mock_ssh

        sftp = Sftp('sftp', sftp_options)
        result = sftp.connect()

        assert result is False
        mock_ssh.close.assert_called_once()
        assert sftp.ssh is None

    @patch('sarracenia.transfer.sftp.paramiko.SSHClient')
    def test_connect_auth_failure_closes_ssh(self, mock_ssh_class, sftp_options):
        """When ssh.connect() raises auth error, the SSHClient should be closed."""
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = Exception("Authentication failed")
        mock_ssh_class.return_value = mock_ssh

        sftp = Sftp('sftp', sftp_options)
        result = sftp.connect()

        assert result is False
        mock_ssh.close.assert_called_once()

    @patch('sarracenia.transfer.sftp.paramiko.SSHClient')
    def test_connect_success_does_not_close(self, mock_ssh_class, sftp_options):
        """Successful connect should not close the connection."""
        mock_ssh = MagicMock()
        mock_sftp_channel = MagicMock()
        mock_sftp_channel.getcwd.return_value = '/home/testuser'
        mock_ssh.open_sftp.return_value = mock_sftp_channel
        mock_ssh_class.return_value = mock_ssh

        sftp = Sftp('sftp', sftp_options)
        result = sftp.connect()

        assert result is True
        mock_ssh.close.assert_not_called()
        assert sftp.connected is True

    @patch('sarracenia.transfer.sftp.paramiko.SSHClient')
    def test_repeated_connect_failures_no_fd_leak(self, mock_ssh_class, sftp_options):
        """Multiple failed connect attempts should each close their ssh client."""
        mock_ssh_instances = [MagicMock() for _ in range(5)]
        for m in mock_ssh_instances:
            m.connect.side_effect = Exception("Connection refused")

        sftp = Sftp('sftp', sftp_options)

        for i in range(5):
            mock_ssh_class.side_effect = [mock_ssh_instances[i]]
            result = sftp.connect()
            assert result is False

        for m in mock_ssh_instances:
            m.close.assert_called_once()


class Test_AccelScpKeyfile:
    """accelScpCommand must pass the ssh key resolved by credentials() to scp.

    Regression test for the accelerated transfer ignoring ssh_keyfile, so a
    non-default key name only worked by hard coding -i in the command.
    """

    def test_keyfile_option_expands_when_key_in_use(self, sftp_options):
        xfer = Sftp('sftp', sftp_options)
        xfer.ssh_keyfile = '/home/sarra/.ssh/id_ecdsa_for_specific_client'
        assert xfer.accelKeyfileOption() == '-i /home/sarra/.ssh/id_ecdsa_for_specific_client'

    def test_keyfile_option_is_empty_without_a_key(self, sftp_options):
        xfer = Sftp('sftp', sftp_options)
        xfer.ssh_keyfile = None
        assert xfer.accelKeyfileOption() == ''

    def test_ssh_keyfile_defaults_to_none_before_credentials_run(self, sftp_options):
        """credentials() may fail; the accelerated path must not raise AttributeError."""
        xfer = Sftp('sftp', sftp_options)
        assert xfer.ssh_keyfile is None
        assert xfer.accelKeyfileOption() == ''

    def test_default_command_carries_the_key(self, sftp_options):
        """The shipped default must substitute to a command scp can authenticate with."""
        xfer = Sftp('sftp', sftp_options)
        xfer.ssh_keyfile = '/key/path'
        cmd = sftp_options.accelScpCommand.replace('%k', xfer.accelKeyfileOption())
        cmd = cmd.replace('%s', 'user@host:/remote/f').replace('%d', './f').split()
        assert cmd == ['/usr/bin/scp', '-i', '/key/path', 'user@host:/remote/f', './f']

    def test_no_key_leaves_no_empty_argument(self, sftp_options):
        """An empty %k must not leave a stray '' in the argument list."""
        xfer = Sftp('sftp', sftp_options)
        xfer.ssh_keyfile = None
        cmd = sftp_options.accelScpCommand.replace('%k', xfer.accelKeyfileOption())
        cmd = cmd.replace('%s', 'user@host:/remote/f').replace('%d', './f').split()
        assert cmd == ['/usr/bin/scp', 'user@host:/remote/f', './f']

    def test_command_without_the_token_is_unchanged(self, sftp_options):
        """Configs that already override accelScpCommand must keep working."""
        xfer = Sftp('sftp', sftp_options)
        xfer.ssh_keyfile = '/key/path'
        legacy = '/usr/bin/scp -i /hard/coded %s %d'
        cmd = legacy.replace('%k', xfer.accelKeyfileOption())
        cmd = cmd.replace('%s', 'user@host:/remote/f').replace('%d', './f').split()
        assert cmd == ['/usr/bin/scp', '-i', '/hard/coded', 'user@host:/remote/f', './f']
