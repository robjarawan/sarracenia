import os
import pytest
import tempfile
import urllib.parse
from unittest.mock import patch, MagicMock
from tests.conftest import *

import logging
from sarracenia.config.credentials import Credential, CredentialDB

logger = logging.getLogger(__name__)


class Test_Credential:
    def test_init_with_url(self):
        c = Credential('https://user:pass@host.example.com:8080/path')
        assert c.url is not None
        assert c.url.scheme == 'https'
        assert c.url.hostname == 'host.example.com'
        assert c.url.port == 8080
        assert c.url.username == 'user'
        assert c.url.password == 'pass'

    def test_init_without_url(self):
        c = Credential()
        assert c.url is None
        assert c.ssh_keyfile is None
        assert c.passive is True
        assert c.binary is True
        assert c.tls is False
        assert c.prot_p is False
        assert c.bearer_token is None
        assert c.login_method is None
        assert c.s3_endpoint is None
        assert c.s3_session_token is None
        assert c.s3_anonymous is False
        assert c.azure_credentials is None
        assert c.implicit_ftps is False

    def test_str_hides_password(self):
        c = Credential('sftp://alice:secret123@server.example.com/path')
        s = str(c)
        assert 'secret123' not in s
        assert 'alice' in s
        assert 'server.example.com' in s

    def test_str_ftp_shows_relevant_options(self):
        c = Credential('ftp://user:pass@ftphost/')
        c.passive = True
        c.binary = True
        c.tls = True
        s = str(c)
        assert 'ftp://' in s

    def test_str_sftp_shows_ssh_keyfile(self):
        c = Credential('sftp://user@sftphost/')
        c.ssh_keyfile = '/home/user/.ssh/id_rsa'
        s = str(c)
        assert 'ssh_keyfile' in s

    def test_str_amqp_shows_login_method(self):
        c = Credential('amqps://user:pass@broker/')
        c.login_method = 'EXTERNAL'
        s = str(c)
        assert 'login_method' in s

    def test_str_https_with_s3_session_token(self):
        c = Credential('https://user:pass@s3host/')
        c.s3_session_token = 'token123'
        s = str(c)
        assert 's3_session_token=Yes' in s

    def test_str_https_with_azure_credentials(self):
        c = Credential('https://user:pass@azurehost/')
        c.azure_credentials = 'azure_cred_value'
        s = str(c)
        assert 'azure_credentials=Yes' in s

    def test_str_with_port(self):
        c = Credential('https://user:pass@host:9999/path')
        s = str(c)
        assert '9999' in s

    def test_to_json(self):
        c = Credential('https://user:pass@host/')
        j = c.to_json()
        assert isinstance(j, str)
        assert 'https://' in j

    def test_str_no_username(self):
        c = Credential('https://host.example.com/')
        s = str(c)
        assert 'host.example.com' in s


class Test_CredentialDB_isTrue:
    def setup_method(self):
        self.db = CredentialDB()

    def test_true_values(self):
        for val in ['true', 'True', 'TRUE', 'yes', 'Yes', 'YES', 'on', 'On', 'ON', '1']:
            assert self.db.isTrue(val) is True

    def test_false_values(self):
        for val in ['false', 'False', 'no', 'No', 'off', 'Off', '0', 'random']:
            assert self.db.isTrue(val) is False


class Test_CredentialDB_isValid:
    def setup_method(self):
        self.db = CredentialDB()

    def test_valid_with_user_and_password(self):
        url = urllib.parse.urlparse('https://user:pass@host/')
        assert self.db.isValid(url) is True

    def test_valid_file_scheme(self):
        url = urllib.parse.urlparse('file:///tmp/test.txt')
        assert self.db.isValid(url) is True

    def test_invalid_no_netloc_not_file(self):
        url = urllib.parse.urlparse('ftp:///path')
        assert self.db.isValid(url) is False

    def test_valid_http_no_credentials(self):
        url = urllib.parse.urlparse('http://host.example.com/')
        assert self.db.isValid(url) is True

    def test_valid_https_no_credentials(self):
        url = urllib.parse.urlparse('https://host.example.com/')
        assert self.db.isValid(url) is True

    def test_valid_sftp_no_credentials(self):
        url = urllib.parse.urlparse('sftp://host.example.com/')
        assert self.db.isValid(url) is True

    def test_valid_s3_no_credentials(self):
        url = urllib.parse.urlparse('s3://bucket/')
        assert self.db.isValid(url) is True

    def test_valid_azure_no_credentials(self):
        url = urllib.parse.urlparse('azure://container/')
        assert self.db.isValid(url) is True

    def test_invalid_unknown_scheme_no_creds(self):
        url = urllib.parse.urlparse('custom://host/')
        assert self.db.isValid(url) is False

    def test_valid_sftp_user_only(self):
        url = urllib.parse.urlparse('sftp://alice@host/')
        assert self.db.isValid(url) is True

    def test_invalid_http_user_only(self):
        url = urllib.parse.urlparse('http://alice@host/')
        assert self.db.isValid(url) is False

    def test_sftp_with_ssh_keyfile_exists(self):
        url = urllib.parse.urlparse('sftp://alice@host/')
        details = Credential()
        details.url = url
        with tempfile.NamedTemporaryFile() as f:
            details.ssh_keyfile = f.name
            assert self.db.isValid(url, details) is True

    def test_sftp_with_ssh_keyfile_not_exists(self):
        url = urllib.parse.urlparse('sftp://alice@host/')
        details = Credential()
        details.url = url
        details.ssh_keyfile = '/nonexistent/path/to/key'
        assert self.db.isValid(url, details) is False

    def test_valid_amqp_with_user_and_pass(self):
        url = urllib.parse.urlparse('amqp://guest:guest@localhost/')
        assert self.db.isValid(url) is True


class Test_CredentialDB_add_and_has:
    def setup_method(self):
        self.db = CredentialDB()

    def test_add_and_has(self):
        self.db.add('https://user:pass@host/')
        # The key strips the password
        assert self.db.has('https://user@host/') is True

    def test_add_with_details(self):
        details = Credential('https://user:pass@host/')
        self.db.add('https://user@host/', details)
        assert self.db.has('https://user@host/') is True

    def test_has_missing(self):
        assert self.db.has('https://nobody@nowhere/') is False


class Test_CredentialDB_get:
    def setup_method(self):
        self.db = CredentialDB()

    def test_get_cached(self):
        self.db.add('https://user:pass@host/')
        cached, cred = self.db.get('https://user@host/')
        assert cached is True
        assert cred is not None

    def test_get_amqp_anonymous_default(self):
        """AMQP URL with no user should auto-add anonymous."""
        cached, cred = self.db.get('amqp://localhost/')
        assert cred is not None
        assert cred.url.username == 'anonymous'

    def test_get_invalid_url(self):
        cached, cred = self.db.get('badscheme://host/')
        assert cred is None

    def test_get_valid_not_cached(self):
        cached, cred = self.db.get('https://user:pass@newhost/')
        assert cached is False
        assert cred is not None

    def test_get_resolves_existing_credential(self):
        self.db.add('sftp://alice:secret@server/', Credential('sftp://alice:secret@server/'))
        cached, cred = self.db.get('sftp://alice:secret@server/')
        assert cred is not None


class Test_CredentialDB_parse:
    def setup_method(self):
        self.db = CredentialDB()

    def test_parse_simple_url(self):
        self.db._parse('https://user:pass@host/')
        assert len(self.db.credentials) == 1

    def test_parse_with_ssh_keyfile(self):
        with tempfile.NamedTemporaryFile() as f:
            self.db._parse(f'sftp://alice@host/ ssh_keyfile={f.name}')
            cred = list(self.db.credentials.values())[0]
            assert cred.ssh_keyfile == f.name

    def test_parse_with_passive(self):
        self.db._parse('ftp://user:pass@host/ passive')
        cred = list(self.db.credentials.values())[0]
        assert cred.passive is True

    def test_parse_with_active(self):
        self.db._parse('ftp://user:pass@host/ active')
        cred = list(self.db.credentials.values())[0]
        assert cred.passive is False

    def test_parse_with_binary(self):
        self.db._parse('ftp://user:pass@host/ binary')
        cred = list(self.db.credentials.values())[0]
        assert cred.binary is True

    def test_parse_with_ascii(self):
        self.db._parse('ftp://user:pass@host/ ascii')
        cred = list(self.db.credentials.values())[0]
        assert cred.binary is False

    def test_parse_with_tls(self):
        self.db._parse('ftp://user:pass@host/ tls')
        cred = list(self.db.credentials.values())[0]
        assert cred.tls is True

    def test_parse_with_ssl(self):
        self.db._parse('ftp://user:pass@host/ ssl')
        cred = list(self.db.credentials.values())[0]
        assert cred.tls is False

    def test_parse_with_prot_p(self):
        self.db._parse('ftp://user:pass@host/ prot_p')
        cred = list(self.db.credentials.values())[0]
        assert cred.prot_p is True

    def test_parse_with_bearer_token(self):
        self.db._parse('https://host/ bearer_token=mytoken123')
        cred = list(self.db.credentials.values())[0]
        assert cred.bearer_token == 'mytoken123'

    def test_parse_with_bt_alias(self):
        self.db._parse('https://host/ bt=mytoken123')
        cred = list(self.db.credentials.values())[0]
        assert cred.bearer_token == 'mytoken123'

    def test_parse_with_login_method(self):
        self.db._parse('amqps://user:pass@host/ login_method=EXTERNAL')
        cred = list(self.db.credentials.values())[0]
        assert cred.login_method == 'EXTERNAL'

    def test_parse_with_s3_session_token(self):
        self.db._parse('https://user:pass@s3host/ s3_session_token=mytoken%3D%3D')
        cred = list(self.db.credentials.values())[0]
        assert cred.s3_session_token == 'mytoken=='

    def test_parse_with_s3_endpoint(self):
        self.db._parse('https://user:pass@s3host/ s3_endpoint=https://custom.endpoint.com')
        cred = list(self.db.credentials.values())[0]
        assert cred.s3_endpoint == 'https://custom.endpoint.com'

    def test_parse_with_s3_anonymous(self):
        self.db._parse('https://host/ s3_anonymous')
        cred = list(self.db.credentials.values())[0]
        assert cred.s3_anonymous is True

    def test_parse_with_azure_storage_credentials(self):
        self.db._parse('https://user:pass@azhost/ azure_storage_credentials=cred%3Dval')
        cred = list(self.db.credentials.values())[0]
        assert cred.azure_credentials == 'cred=val'

    def test_parse_with_implicit_ftps(self):
        self.db._parse('ftp://user:pass@host/ implicit_ftps')
        cred = list(self.db.credentials.values())[0]
        assert cred.implicit_ftps is True
        assert cred.tls is True

    def test_parse_empty_line(self):
        self.db._parse('')
        assert len(self.db.credentials) == 0

    def test_parse_comment_line(self):
        self.db._parse('# this is a comment')
        assert len(self.db.credentials) == 0

    def test_parse_whitespace_line(self):
        self.db._parse('   \t  ')
        assert len(self.db.credentials) == 0

    def test_parse_multiple_options(self):
        self.db._parse('ftp://user:pass@host/ passive,binary,tls')
        cred = list(self.db.credentials.values())[0]
        assert cred.passive is True
        assert cred.binary is True
        assert cred.tls is True

    def test_parse_unknown_option_warns(self):
        self.db._parse('https://user:pass@host/ unknown_option=value')
        assert len(self.db.credentials) == 1


class Test_CredentialDB_read:
    def test_read_file(self):
        db = CredentialDB()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write('https://user1:pass1@host1/\n')
            f.write('# comment line\n')
            f.write('sftp://user2:pass2@host2/\n')
            f.write('\n')
            f.write('ftp://user3:pass3@host3/ passive,binary\n')
            fname = f.name
        try:
            db.read(fname)
            assert len(db.credentials) == 3
        finally:
            os.unlink(fname)

    def test_read_nonexistent_file(self):
        db = CredentialDB()
        db.read('/nonexistent/path/to/credentials.conf')
        assert len(db.credentials) == 0

    def test_read_empty_file(self):
        db = CredentialDB()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write('')
            fname = f.name
        try:
            db.read(fname)
            assert len(db.credentials) == 0
        finally:
            os.unlink(fname)


class Test_CredentialDB_resolve:
    def setup_method(self):
        self.db = CredentialDB()

    def test_resolve_matching_credential(self):
        cred = Credential('sftp://alice:secret@server/')
        self.db.credentials['sftp://alice@server/'] = cred
        ok, details = self.db._resolve('sftp://alice:secret@server/')
        assert ok is True
        assert details is not None

    def test_resolve_no_match(self):
        ok, details = self.db._resolve('sftp://nobody@nowhere/')
        assert ok is False
        assert details is None

    def test_resolve_scheme_mismatch(self):
        cred = Credential('sftp://alice:secret@server/')
        self.db.credentials['sftp://alice@server/'] = cred
        ok, details = self.db._resolve('https://alice:secret@server/')
        assert ok is False

    def test_resolve_hostname_mismatch(self):
        cred = Credential('sftp://alice:secret@server1/')
        self.db.credentials['sftp://alice@server1/'] = cred
        ok, details = self.db._resolve('sftp://alice:secret@server2/')
        assert ok is False

    def test_resolve_amqp_vhost_default(self):
        cred = Credential('amqp://guest:guest@localhost/')
        self.db.credentials['amqp://guest@localhost/'] = cred
        ok, details = self.db._resolve('amqp://guest:guest@localhost/')
        assert ok is True

    def test_resolve_amqp_vhost_mismatch(self):
        cred = Credential('amqp://guest:guest@localhost/vhost1')
        self.db.credentials['amqp://guest@localhost/vhost1'] = cred
        ok, details = self.db._resolve('amqp://guest:guest@localhost/vhost2')
        assert ok is False

    def test_resolve_url_parsed_from_string(self):
        cred = Credential('https://user:pass@host/')
        self.db.credentials['https://user@host/'] = cred
        ok, details = self.db._resolve('https://user:pass@host/')
        assert ok is True


class Test_CredentialDB_validate_urlstr:
    def setup_method(self):
        self.db = CredentialDB()

    def test_validate_valid_url(self):
        ok, cred = self.db.validate_urlstr('https://user:pass@host/')
        # Valid URL gets added to cache
        assert cred is not None

    def test_validate_invalid_url(self):
        ok, cred = self.db.validate_urlstr('badscheme://nohost/')
        assert ok is False
        assert cred is not None  # Returns a Credential even on failure
