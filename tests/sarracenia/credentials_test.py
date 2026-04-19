import pytest
import urllib.parse
import os
import types

from sarracenia.config.credentials import Credential, CredentialDB


class TestCredential:

    def test_init_no_url(self):
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

    def test_init_with_url(self):
        c = Credential("sftp://alice@herhost/path")
        assert c.url is not None
        assert c.url.scheme == "sftp"
        assert c.url.username == "alice"
        assert c.url.hostname == "herhost"

    def test_str_sftp(self):
        c = Credential("sftp://alice@herhost/")
        c.ssh_keyfile = "/home/alice/.ssh/id_rsa"
        s = str(c)
        assert "sftp://" in s
        assert "alice" in s
        assert "herhost" in s
        assert "ssh_keyfile" in s

    def test_str_ftp_shows_options(self):
        c = Credential("ftp://alice:pass@ftphost/")
        c.passive = True
        c.binary = True
        c.tls = True
        s = str(c)
        assert "ftp://" in s
        assert "passive" in s
        assert "binary" in s
        assert "tls" in s

    def test_str_amqp_shows_login_method(self):
        c = Credential("amqps://user:pass@broker/")
        c.login_method = "PLAIN"
        s = str(c)
        assert "amqps://" in s
        assert "login_method" in s

    def test_str_https_shows_bearer_token(self):
        c = Credential("https://server/")
        c.bearer_token = "mytoken"
        s = str(c)
        assert "https://" in s
        assert "bearer_token" in s

    def test_str_https_s3_session_token(self):
        c = Credential("https://server/")
        c.s3_session_token = "xyz"
        s = str(c)
        assert "s3_session_token=Yes" in s

    def test_str_https_azure_credentials(self):
        c = Credential("https://server/")
        c.azure_credentials = "abc"
        s = str(c)
        assert "azure_credentials=Yes" in s

    def test_str_with_port(self):
        c = Credential("ftp://user:pass@host:2121/")
        s = str(c)
        assert "2121" in s

    def test_to_json(self):
        c = Credential("sftp://alice@herhost/")
        j = c.to_json()
        assert j.startswith('"')
        assert j.endswith('"')
        assert "sftp://" in j


class TestCredentialDB:

    def test_init(self):
        db = CredentialDB()
        assert isinstance(db.credentials, dict)
        assert len(db.credentials) == 0

    def test_isTrue_true_values(self):
        db = CredentialDB()
        assert db.isTrue("true") is True
        assert db.isTrue("True") is True
        assert db.isTrue("TRUE") is True
        assert db.isTrue("yes") is True
        assert db.isTrue("on") is True
        assert db.isTrue("1") is True

    def test_isTrue_false_values(self):
        db = CredentialDB()
        assert db.isTrue("false") is False
        assert db.isTrue("no") is False
        assert db.isTrue("off") is False
        assert db.isTrue("0") is False
        assert db.isTrue("anything") is False

    def test_isValid_file_scheme(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("file:///tmp/test")
        assert db.isValid(url) is True

    def test_isValid_empty_netloc_non_file(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("ftp://")
        assert db.isValid(url) is False

    def test_isValid_user_and_password(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("ftp://user:pass@host/")
        assert db.isValid(url) is True

    def test_isValid_no_user_no_pass_http(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("http://host/")
        assert db.isValid(url) is True

    def test_isValid_no_user_no_pass_https(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("https://host/")
        assert db.isValid(url) is True

    def test_isValid_no_user_no_pass_sftp(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("sftp://host/")
        assert db.isValid(url) is True

    def test_isValid_no_user_no_pass_s3(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("s3://bucket/")
        assert db.isValid(url) is True

    def test_isValid_no_user_no_pass_unknown(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("weird://host/")
        assert db.isValid(url) is False

    def test_isValid_pass_no_user_sftp(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("sftp://:pass@host/")
        assert db.isValid(url) is True

    def test_isValid_pass_no_user_ftp_rejected(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("ftp://:pass@host/")
        assert db.isValid(url) is False

    def test_isValid_user_no_pass_sftp(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("sftp://user@host/")
        assert db.isValid(url) is True

    def test_isValid_user_no_pass_ftp_rejected(self):
        db = CredentialDB()
        url = urllib.parse.urlparse("ftp://user@host/")
        assert db.isValid(url) is False

    def test_isValid_sftp_bad_keyfile(self, tmp_path):
        db = CredentialDB()
        url = urllib.parse.urlparse("sftp://user@host/")
        details = Credential()
        details.url = url
        details.ssh_keyfile = str(tmp_path / "nonexistent_key")
        assert db.isValid(url, details) is False

    def test_isValid_sftp_good_keyfile(self, tmp_path):
        db = CredentialDB()
        url = urllib.parse.urlparse("sftp://user@host/")
        keypath = str(tmp_path / "mykey")
        open(keypath, "w").close()
        details = Credential()
        details.url = url
        details.ssh_keyfile = keypath
        assert db.isValid(url, details) is True

    def test_add_and_has(self):
        db = CredentialDB()
        db.add("ftp://user:pass@host/")
        assert db.has("ftp://user@host/")

    def test_add_without_details(self):
        db = CredentialDB()
        db.add("https://host/path")
        assert db.has("https://host/path")

    def test_add_with_details(self):
        db = CredentialDB()
        c = Credential("https://host/")
        db.add("https://host/", c)
        assert db.has("https://host/")
        assert db.credentials["https://host/"] is c

    def test_get_amqp_anonymous_default(self):
        db = CredentialDB()
        found, details = db.get("amqps://broker/")
        assert details is not None
        assert details.url.username == "anonymous"

    def test_get_invalid_returns_none(self):
        db = CredentialDB()
        found, details = db.get("weird://host/")
        assert found is False
        assert details is None

    def test_get_valid_not_cached(self):
        db = CredentialDB()
        found, details = db.get("https://user:pass@host/")
        assert found is False
        assert details is not None

    def test_parse_comment_line(self):
        db = CredentialDB()
        db._parse("# this is a comment")
        assert len(db.credentials) == 0

    def test_parse_empty_line(self):
        db = CredentialDB()
        db._parse("")
        assert len(db.credentials) == 0

    def test_parse_simple_url(self):
        db = CredentialDB()
        db._parse("https://user:pass@host/")
        assert len(db.credentials) == 1

    def test_parse_ftp_with_options(self):
        db = CredentialDB()
        db._parse("ftp://user:pass@host/ passive,binary,tls")
        cred = list(db.credentials.values())[0]
        assert cred.passive is True
        assert cred.binary is True
        assert cred.tls is True

    def test_parse_ftp_active_ascii(self):
        db = CredentialDB()
        db._parse("ftp://user:pass@host/ active,ascii")
        cred = list(db.credentials.values())[0]
        assert cred.passive is False
        assert cred.binary is False

    def test_parse_ssh_keyfile(self, tmp_path):
        keypath = str(tmp_path / "testkey")
        open(keypath, "w").close()
        db = CredentialDB()
        db._parse(f"sftp://user@host/ ssh_keyfile={keypath}")
        cred = list(db.credentials.values())[0]
        assert cred.ssh_keyfile == keypath

    def test_parse_bearer_token(self):
        db = CredentialDB()
        db._parse("https://host/ bearer_token=mytoken123")
        cred = list(db.credentials.values())[0]
        assert cred.bearer_token == "mytoken123"

    def test_parse_bearer_token_bt_alias(self):
        db = CredentialDB()
        db._parse("https://host/ bt=mytoken123")
        cred = list(db.credentials.values())[0]
        assert cred.bearer_token == "mytoken123"

    def test_parse_login_method(self):
        db = CredentialDB()
        db._parse("amqps://user:pass@broker/ login_method=PLAIN")
        cred = list(db.credentials.values())[0]
        assert cred.login_method == "PLAIN"

    def test_parse_s3_options(self):
        db = CredentialDB()
        db._parse("https://host/ s3_endpoint=https://s3.example.com,s3_anonymous")
        cred = list(db.credentials.values())[0]
        assert cred.s3_endpoint == "https://s3.example.com"
        assert cred.s3_anonymous is True

    def test_parse_implicit_ftps(self):
        db = CredentialDB()
        db._parse("ftp://user:pass@host/ implicit_ftps")
        cred = list(db.credentials.values())[0]
        assert cred.implicit_ftps is True
        assert cred.tls is True

    def test_parse_prot_p(self):
        db = CredentialDB()
        db._parse("ftp://user:pass@host/ prot_p")
        cred = list(db.credentials.values())[0]
        assert cred.prot_p is True

    def test_parse_ssl_sets_tls_false(self):
        db = CredentialDB()
        db._parse("ftp://user:pass@host/ ssl")
        cred = list(db.credentials.values())[0]
        assert cred.tls is False

    def test_parse_invalid_url(self):
        db = CredentialDB()
        db._parse("weird://host/")
        assert len(db.credentials) == 0

    def test_resolve_matching(self):
        db = CredentialDB()
        c = Credential("sftp://alice@herhost/")
        db.add("sftp://alice@herhost/", c)
        ok, details = db._resolve("sftp://alice@herhost/")
        assert ok is True
        assert details is c

    def test_resolve_no_match(self):
        db = CredentialDB()
        ok, details = db._resolve("sftp://bob@otherhost/")
        assert ok is False
        assert details is None

    def test_resolve_amqp_vhost_default(self):
        db = CredentialDB()
        c = Credential("amqps://user:pass@broker/")
        db.add("amqps://user:pass@broker/", c)
        ok, details = db._resolve("amqps://user:pass@broker")
        assert ok is True

    def test_resolve_scheme_mismatch(self):
        db = CredentialDB()
        c = Credential("ftp://user:pass@host/")
        db.add("ftp://user:pass@host/", c)
        ok, details = db._resolve("sftp://user:pass@host/")
        assert ok is False

    def test_resolve_wildcard_username(self):
        db = CredentialDB()
        c = Credential("sftp://alice@host/")
        db.add("sftp://alice@host/", c)
        ok, details = db._resolve("sftp://host/")
        assert ok is True

    def test_validate_urlstr_valid(self):
        db = CredentialDB()
        ok, cred = db.validate_urlstr("https://user:pass@host/")
        assert cred is not None
        assert cred.url is not None

    def test_validate_urlstr_invalid(self):
        db = CredentialDB()
        ok, cred = db.validate_urlstr("weird://host/")
        assert ok is False
        assert cred is not None
        assert cred.url is not None

    def test_read_nonexistent_file(self, tmp_path):
        db = CredentialDB()
        db.read(str(tmp_path / "nonexistent.conf"))
        assert len(db.credentials) == 0

    def test_read_real_file(self, tmp_path):
        cred_file = tmp_path / "creds.conf"
        cred_file.write_text("# comment\nhttps://user:pass@host/\nsftp://alice@sftphost/\n")
        db = CredentialDB()
        db.read(str(cred_file))
        assert len(db.credentials) == 2
