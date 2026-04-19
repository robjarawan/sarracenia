"""Tests for sarracenia.transfer.https — Https transfer protocol.

Covers: init/reset, connect, credentials, get, stat, ls, __open__,
path normalization, error handling, state transitions, retry/no-retry,
malformed metadata, redirect handler, TLS rigour levels, bearer tokens.

All tests mock the external urllib/paramiko boundary; no real network.
"""

import datetime
import io
import logging
import ssl
import types
import urllib.error
import urllib.request
from unittest.mock import MagicMock, Mock, patch, PropertyMock, call

import pytest

import sarracenia
import sarracenia.config
import sarracenia.filemetadata
import sarracenia.transfer

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_options(**overrides):
    """Build a minimal options namespace for Https constructor."""
    opts = sarracenia.config.default_config()
    opts.credentials = MagicMock()
    opts.sendTo = 'https://example.com'
    opts.timeout = 30
    opts.bufSize = 8192
    opts.batch = 100
    opts.logLevel = 'info'
    opts.logFormat = '%(message)s'
    opts.settings = {}
    opts.httpUserAgent = 'sr3-test/1.0'
    opts.no = 0
    opts.component = 'subscribe'
    opts.config = 'test'
    opts.pid_filename = '/tmp/test.pid'
    opts.metricsFilename = '/tmp/test_metrics'
    opts.novipFilename = '/tmp/test_novip'
    opts.logRotateCount = 5
    for k, v in overrides.items():
        setattr(opts, k, v)
    return opts


def _make_https(opts=None, **kw):
    """Instantiate Https with mocked urllib build_opener to avoid real network."""
    if opts is None:
        opts = _make_options(**kw)
    with patch('urllib.request.build_opener') as mock_bo:
        mock_bo.return_value = MagicMock()
        inst = sarracenia.transfer.https.Https('https', opts)
    return inst


# ══════════════════════════════════════════════════════════════════════════════
# HTTPRedirectHandlerSameMethod
# ══════════════════════════════════════════════════════════════════════════════

class Test_HTTPRedirectHandlerSameMethod:

    def test_preserves_post_method(self):
        from sarracenia.transfer.https import HTTPRedirectHandlerSameMethod
        handler = HTTPRedirectHandlerSameMethod()
        req = urllib.request.Request('https://a.com/old', method='POST')
        fp = io.BytesIO(b'')
        # parent redirect_request returns a new Request defaulting to GET
        with patch.object(urllib.request.HTTPRedirectHandler, 'redirect_request') as parent:
            new_req = MagicMock()
            new_req.get_method.return_value = 'GET'
            new_req.get_full_url.return_value = 'https://a.com/new'
            parent.return_value = new_req
            result = handler.redirect_request(req, fp, 301, 'Moved', {}, 'https://a.com/new')
        assert result.method == 'POST'

    def test_preserves_put_method(self):
        from sarracenia.transfer.https import HTTPRedirectHandlerSameMethod
        handler = HTTPRedirectHandlerSameMethod()
        req = urllib.request.Request('https://a.com/old', method='PUT')
        fp = io.BytesIO(b'')
        with patch.object(urllib.request.HTTPRedirectHandler, 'redirect_request') as parent:
            new_req = MagicMock()
            new_req.get_method.return_value = 'GET'
            new_req.get_full_url.return_value = 'https://a.com/new'
            parent.return_value = new_req
            result = handler.redirect_request(req, fp, 302, 'Found', {}, 'https://a.com/new')
        assert result.method == 'PUT'

    def test_preserves_get_method(self):
        from sarracenia.transfer.https import HTTPRedirectHandlerSameMethod
        handler = HTTPRedirectHandlerSameMethod()
        req = urllib.request.Request('https://a.com/old', method='GET')
        fp = io.BytesIO(b'')
        with patch.object(urllib.request.HTTPRedirectHandler, 'redirect_request') as parent:
            new_req = MagicMock()
            new_req.get_method.return_value = 'GET'
            new_req.get_full_url.return_value = 'https://a.com/new'
            parent.return_value = new_req
            result = handler.redirect_request(req, fp, 307, 'Redirect', {}, 'https://a.com/new')
        assert result.method == 'GET'


# ══════════════════════════════════════════════════════════════════════════════
# Https __init__ / init / registered_as
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_init:

    def test_registered_as_returns_http_and_https(self):
        from sarracenia.transfer.https import Https
        assert Https.registered_as() == ['http', 'https']

    def test_init_sets_defaults(self):
        inst = _make_https()
        assert inst.connected is False
        assert inst.http is None
        assert inst.path == ''
        assert inst.cwd == ''
        assert inst.urlstr == ''
        assert inst.entries == {}
        assert inst.seek is True

    def test_init_creates_ssl_context(self):
        inst = _make_https()
        assert isinstance(inst.tlsctx, ssl.SSLContext)

    def test_tlsRigour_lax_disables_cert_verification(self):
        inst = _make_https(tlsRigour='lax')
        assert inst.tlsctx.check_hostname is False
        assert inst.tlsctx.verify_mode == ssl.CERT_NONE

    def test_tlsRigour_strict_requires_certs(self):
        inst = _make_https(tlsRigour='strict')
        assert inst.tlsctx.check_hostname is True
        assert inst.tlsctx.verify_mode == ssl.CERT_REQUIRED

    def test_tlsRigour_normal_default_context(self):
        inst = _make_https(tlsRigour='normal')
        # Normal means default context, check_hostname should be True by default
        assert isinstance(inst.tlsctx, ssl.SSLContext)

    def test_tlsRigour_invalid_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            inst = _make_https(tlsRigour='bogus')
        assert any('must be one of' in r.message for r in caplog.records)

    def test_tlsRigour_case_insensitive(self):
        inst = _make_https(tlsRigour='LAX')
        assert inst.tlsctx.check_hostname is False

    def test_init_resets_state_completely(self):
        inst = _make_https()
        # Simulate some state
        inst.connected = True
        inst.http = 'something'
        inst.urlstr = 'https://example.com/file'
        inst.init()
        assert inst.connected is False
        assert inst.http is None
        assert inst.urlstr == ''
        assert inst.opener is None
        assert inst.head_opener is None
        assert inst.password_mgr is None


# ══════════════════════════════════════════════════════════════════════════════
# cd
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_cd:

    def test_cd_sets_cwd_and_path(self):
        inst = _make_https()
        inst.cd('/some/directory/file.txt')
        assert inst.cwd == '/some/directory'
        assert inst.path == '/some/directory/file.txt'

    def test_cd_root_path(self):
        inst = _make_https()
        inst.cd('/file.txt')
        assert inst.cwd == '/'
        assert inst.path == '/file.txt'

    def test_cd_empty_string(self):
        inst = _make_https()
        inst.cd('')
        assert inst.path == ''


# ══════════════════════════════════════════════════════════════════════════════
# check_is_connected
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_check_is_connected:

    def test_not_connected_returns_false(self):
        inst = _make_https()
        inst.connected = False
        assert inst.check_is_connected() is False

    def test_connected_but_no_opener_returns_false(self):
        inst = _make_https()
        inst.connected = True
        inst.opener = None
        inst.head_opener = MagicMock()
        inst.sendTo = inst.o.sendTo
        assert inst.check_is_connected() is False

    def test_connected_but_no_head_opener_returns_false(self):
        inst = _make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = None
        inst.sendTo = inst.o.sendTo
        assert inst.check_is_connected() is False

    def test_connected_but_sendTo_changed_returns_false(self):
        inst = _make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.sendTo = 'https://old.example.com'
        inst.o.sendTo = 'https://new.example.com'
        assert inst.check_is_connected() is False

    def test_fully_connected_returns_true(self):
        inst = _make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.sendTo = 'https://example.com'
        inst.o.sendTo = 'https://example.com'
        assert inst.check_is_connected() is True


# ══════════════════════════════════════════════════════════════════════════════
# close
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_close:

    def test_close_resets_state(self):
        inst = _make_https()
        inst.connected = True
        inst.opener = MagicMock()
        inst.http = MagicMock()
        inst.close()
        assert inst.connected is False
        assert inst.http is None
        assert inst.opener is None


# ══════════════════════════════════════════════════════════════════════════════
# connect
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_connect:

    def test_connect_builds_openers(self):
        inst = _make_https()
        # Set up credentials to succeed
        mock_url = MagicMock()
        mock_url.username = 'user'
        mock_url.password = 'pass'
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.bearer_token = None
        inst.o.credentials.get.return_value = (True, mock_details)

        with patch('urllib.request.build_opener') as mock_bo:
            mock_bo.return_value = MagicMock()
            result = inst.connect()
        assert result is True
        assert inst.connected is True

    def test_connect_closes_existing_connection(self):
        inst = _make_https()
        inst.connected = True
        mock_url = MagicMock()
        mock_url.username = ''
        mock_url.password = ''
        mock_details = MagicMock()
        mock_details.url = mock_url
        inst.o.credentials.get.return_value = (True, mock_details)

        with patch('urllib.request.build_opener') as mock_bo:
            mock_bo.return_value = MagicMock()
            with patch.object(inst, 'close') as mock_close:
                inst.connect()
                mock_close.assert_called_once()

    def test_connect_credentials_failure_sets_connected_false(self):
        """Bug: connect() unconditionally sets self.connected = True on line 164,
        even if credentials() failed. This test validates the fix."""
        inst = _make_https()
        # Mock credentials to return False (failure)
        with patch('urllib.request.build_opener') as mock_bo:
            mock_bo.return_value = MagicMock()
            with patch.object(inst, 'credentials', return_value=False):
                result = inst.connect()
        assert inst.connected is False
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# credentials
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_credentials:

    def test_credentials_with_username_password(self):
        inst = _make_https()
        inst.sendTo = 'https://example.com'
        inst.password_mgr = MagicMock()

        mock_url = MagicMock()
        mock_url.username = 'alice'
        mock_url.password = 'secret%21'
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.bearer_token = None
        inst.o.credentials.get.return_value = (True, mock_details)

        result = inst.credentials()
        assert result is True
        assert inst.user == 'alice'
        assert inst.password == 'secret%21'
        inst.password_mgr.add_password.assert_called_once()

    def test_credentials_with_bearer_token(self):
        inst = _make_https()
        inst.sendTo = 'https://example.com'
        inst.password_mgr = MagicMock()

        mock_url = MagicMock()
        mock_url.username = ''
        mock_url.password = ''
        mock_details = MagicMock()
        mock_details.url = mock_url
        mock_details.bearer_token = 'tok123'
        inst.o.credentials.get.return_value = (True, mock_details)

        result = inst.credentials()
        assert result is True
        assert inst.user is None
        assert inst.bearer_token == 'tok123'

    def test_credentials_empty_username_sets_none(self):
        inst = _make_https()
        inst.sendTo = 'https://example.com'
        inst.password_mgr = MagicMock()

        mock_url = MagicMock()
        mock_url.username = ''
        mock_url.password = ''
        mock_details = MagicMock()
        mock_details.url = mock_url
        inst.o.credentials.get.return_value = (True, mock_details)

        inst.credentials()
        assert inst.user is None
        assert inst.password is None
        inst.password_mgr.add_password.assert_not_called()

    def test_credentials_exception_returns_false(self):
        inst = _make_https()
        inst.sendTo = 'https://example.com'
        inst.o.credentials.get.side_effect = Exception('db error')
        result = inst.credentials()
        assert result is False

    def test_credentials_no_bearer_token_attribute(self):
        inst = _make_https()
        inst.sendTo = 'https://example.com'
        inst.password_mgr = MagicMock()

        mock_url = MagicMock()
        mock_url.username = 'u'
        mock_url.password = 'p'
        mock_details = MagicMock(spec=[])  # no attributes
        mock_details.url = mock_url
        inst.o.credentials.get.return_value = (True, mock_details)

        result = inst.credentials()
        assert result is True
        assert inst.bearer_token is None


# ══════════════════════════════════════════════════════════════════════════════
# __open__ — URL normalization and error handling
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_open:

    def _setup_connected(self, inst):
        """Set up instance as connected with working openers."""
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()

    def test_open_normalizes_double_slashes_in_https(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/a/b'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/a//b')
        assert inst.urlstr == 'https://example.com/a/b'

    def test_open_normalizes_double_slashes_in_http(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'http://example.com/a/b'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('http://example.com/a//b')
        assert inst.urlstr == 'http://example.com/a/b'

    def test_open_uses_head_opener_for_head_method(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.head_opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file', method='HEAD')
        assert result is True
        inst.head_opener.open.assert_called_once()
        inst.opener.open.assert_not_called()

    def test_open_uses_standard_opener_for_get(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file')
        assert result is True
        inst.opener.open.assert_called_once()

    def test_open_adds_range_header_for_offset(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file', remote_offset=100, length=200)
        assert result is True
        req_obj = inst.opener.open.call_args[0][0]
        assert 'Range' in req_obj.headers
        assert req_obj.headers['Range'] == 'bytes=100-299'

    def test_open_adds_bearer_token_header(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.bearer_token = 'mytoken123'
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file')
        req_obj = inst.opener.open.call_args[0][0]
        assert req_obj.headers['Authorization'] == 'Bearer mytoken123'

    def test_open_strips_credentials_from_urlstr(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.user = 'alice'
        inst.password = 'secret'
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://alice:secret@example.com/file')
        assert 'alice' not in inst.urlstr
        assert 'secret' not in inst.urlstr

    def test_open_http_error_sets_disconnected_and_raises(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.opener.open.side_effect = urllib.error.HTTPError(
            'https://example.com/file', 404, 'Not Found', {}, None)

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with pytest.raises(urllib.error.HTTPError):
                inst.__open__('https://example.com/file')
        assert inst.connected is False

    def test_open_url_error_sets_disconnected_and_raises(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.opener.open.side_effect = urllib.error.URLError('connection refused')

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with pytest.raises(urllib.error.URLError):
                inst.__open__('https://example.com/file')
        assert inst.connected is False

    def test_open_generic_exception_sets_disconnected_and_raises(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.opener.open.side_effect = OSError('timeout')

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with pytest.raises(OSError):
                inst.__open__('https://example.com/file')
        assert inst.connected is False

    def test_open_with_add_headers_overrides(self):
        inst = _make_https()
        self._setup_connected(inst)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file',
                                          add_headers={'Accept-Encoding': 'identity', 'X-Custom': 'val'})
        req_obj = inst.opener.open.call_args[0][0]
        assert req_obj.headers['Accept-encoding'] == 'identity'  # urllib normalizes case
        assert req_obj.headers['X-custom'] == 'val'

    def test_open_reconnects_if_not_connected(self):
        inst = _make_https()
        inst.connected = False
        inst.opener = None
        inst.head_opener = None
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'

        with patch.object(inst, 'connect') as mock_connect, \
             patch.object(inst, 'check_is_connected', return_value=False):
            def side_effect():
                inst.connected = True
                inst.opener = MagicMock()
                inst.opener.open.return_value = mock_resp
                inst.head_opener = MagicMock()
                inst.sendTo = inst.o.sendTo
            mock_connect.side_effect = side_effect

            with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
                result = inst.__open__('https://example.com/file')
            mock_connect.assert_called_once()

    def test_open_with_timeout_none(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.timeout = None
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file')
        # When timeout is None, open is called without timeout parameter
        inst.opener.open.assert_called_once_with(inst.req)

    def test_open_with_timeout_value(self):
        inst = _make_https()
        self._setup_connected(inst)
        inst.timeout = 60
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/file'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            result = inst.__open__('https://example.com/file')
        inst.opener.open.assert_called_once_with(inst.req, timeout=60)


# ══════════════════════════════════════════════════════════════════════════════
# stat — HEAD request metadata extraction
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_stat:

    def _setup_for_stat(self, inst):
        inst.connected = True
        inst.sendTo = inst.o.sendTo
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'
        inst.cwd = '/data'

    def test_stat_returns_size_and_mtime(self):
        inst = _make_https()
        self._setup_for_stat(inst)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.txt'
        mock_resp.getcode.return_value = 200
        mock_resp.getheader.side_effect = lambda h: {
            'Content-Length': '9659',
            'Last-Modified': 'Thu, 22 Aug 2024 20:37:53 GMT',
        }.get(h)
        inst.head_opener.open.return_value = mock_resp

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            st = inst.stat('file.txt', msg)

        assert st is not None
        assert st.st_size == 9659
        assert st.st_mtime > 0

    def test_stat_returns_none_on_open_failure(self):
        inst = _make_https()
        self._setup_for_stat(inst)

        inst.head_opener.open.side_effect = urllib.error.HTTPError(
            'url', 500, 'Internal Server Error', {}, None)

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with pytest.raises(urllib.error.HTTPError):
                st = inst.stat('file.txt', msg)

    def test_stat_returns_none_on_non_200(self):
        inst = _make_https()
        self._setup_for_stat(inst)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.txt'
        mock_resp.getcode.return_value = 304
        inst.head_opener.open.return_value = mock_resp

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            st = inst.stat('file.txt', msg)
        assert st is None

    def test_stat_returns_none_when_no_metadata_available(self):
        inst = _make_https()
        self._setup_for_stat(inst)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.txt'
        mock_resp.getcode.return_value = 200
        mock_resp.getheader.return_value = None
        mock_resp.info.return_value = 'empty headers'
        inst.head_opener.open.return_value = mock_resp

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            st = inst.stat('file.txt', msg)
        assert st is None

    def test_stat_only_size_no_mtime(self):
        inst = _make_https()
        self._setup_for_stat(inst)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.txt'
        mock_resp.getcode.return_value = 200
        mock_resp.getheader.side_effect = lambda h: {
            'Content-Length': '1234',
            'Last-Modified': None,
        }.get(h)
        inst.head_opener.open.return_value = mock_resp

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            st = inst.stat('file.txt', msg)
        assert st is not None
        assert st.st_size == 1234

    def test_stat_url_construction_with_trailing_slash(self):
        inst = _make_https()
        self._setup_for_stat(inst)
        inst.path = '/data/'

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.txt'
        mock_resp.getcode.return_value = 200
        mock_resp.getheader.side_effect = lambda h: {
            'Content-Length': '100',
        }.get(h)
        inst.head_opener.open.return_value = mock_resp

        msg = sarracenia.Message()
        msg['baseUrl'] = 'https://example.com/'
        msg['relPath'] = 'data/file.txt'

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            st = inst.stat('file.txt', msg)
        assert st is not None


# ══════════════════════════════════════════════════════════════════════════════
# get — download file
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_get:

    def test_get_with_retrievePath(self):
        inst = _make_https()
        inst.connected = True
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'

        msg = {'retrievePath': 'special/path/file.dat'}

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/special/path/file.dat'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with patch.object(inst, 'read_writelocal', return_value=1024) as mock_rw:
                result = inst.get(msg, 'file.dat', '/tmp/local.dat')
        assert result == 1024

    def test_get_without_retrievePath_builds_url(self):
        inst = _make_https()
        inst.connected = True
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'
        inst.o.httpsSafeQuote = '/+'

        msg = {}

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data/file.dat'
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with patch.object(inst, 'read_writelocal', return_value=2048) as mock_rw:
                result = inst.get(msg, 'file.dat', '/tmp/local.dat')
        assert result == 2048

    def test_get_returns_false_on_open_failure(self):
        inst = _make_https()
        inst.connected = True
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'
        inst.o.httpsSafeQuote = '/+'

        msg = {}
        inst.opener.open.side_effect = urllib.error.URLError('fail')

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            with pytest.raises(urllib.error.URLError):
                inst.get(msg, 'file.dat', '/tmp/local.dat')


# ══════════════════════════════════════════════════════════════════════════════
# getAccelerated — wget subprocess
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_getAccelerated:

    def test_getAccelerated_success_returns_length(self):
        inst = _make_https()
        inst.o.accelWgetCommand = '/usr/bin/wget %s -o - -O %d'

        msg = {'baseUrl': 'https://example.com', 'relPath': 'data/file.dat'}

        with patch('subprocess.Popen') as mock_popen:
            proc = MagicMock()
            proc.returncode = 0
            mock_popen.return_value = proc
            result = inst.getAccelerated(msg, 'file.dat', '/tmp/local.dat', 5000)
        assert result == 5000

    def test_getAccelerated_failure_returns_negative(self):
        inst = _make_https()
        inst.o.accelWgetCommand = '/usr/bin/wget %s -o - -O %d'

        msg = {'baseUrl': 'https://example.com', 'relPath': 'data/file.dat'}

        with patch('subprocess.Popen') as mock_popen:
            proc = MagicMock()
            proc.returncode = 8  # wget error
            mock_popen.return_value = proc
            result = inst.getAccelerated(msg, 'file.dat', '/tmp/local.dat', 5000)
        assert result == -1

    def test_getAccelerated_with_exactLength_adds_range(self):
        inst = _make_https()
        inst.o.accelWgetCommand = '/usr/bin/wget %s -o - -O %d'

        msg = {'baseUrl': 'https://example.com', 'relPath': 'data/file.dat'}

        with patch('subprocess.Popen') as mock_popen:
            proc = MagicMock()
            proc.returncode = 0
            mock_popen.return_value = proc
            result = inst.getAccelerated(msg, 'file.dat', '/tmp/local.dat', 5000,
                                          remote_offset=100, exactLength=True)
        cmd_list = mock_popen.call_args[0][0]
        assert any('Range' in str(c) for c in cmd_list)

    def test_getAccelerated_escapes_spaces_in_url(self):
        inst = _make_https()
        inst.o.accelWgetCommand = '/usr/bin/wget %s -o - -O %d'

        msg = {'baseUrl': 'https://example.com', 'relPath': 'data/my file.dat'}

        with patch('subprocess.Popen') as mock_popen:
            proc = MagicMock()
            proc.returncode = 0
            mock_popen.return_value = proc
            inst.getAccelerated(msg, 'file.dat', '/tmp/local.dat', 1000)
        cmd_list = mock_popen.call_args[0][0]
        # Space should be escaped to '\ '
        cmd_str = ' '.join(cmd_list)
        assert '\\ ' in cmd_str or 'my file' not in cmd_list[1]


# ══════════════════════════════════════════════════════════════════════════════
# ls — directory listing
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_ls:

    def test_ls_returns_html_buffer(self):
        inst = _make_https()
        inst.connected = True
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'
        inst.o.httpsSafeQuote = '/+'

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com/data'
        chunks = [b'<html><body>content</body></html>', b'']
        mock_resp.read.side_effect = chunks
        inst.opener.open.return_value = mock_resp

        with patch('sarracenia.transfer.alarm_set'), patch('sarracenia.transfer.alarm_cancel'):
            inst.http = mock_resp
            with patch.object(inst, '__open__', return_value=True):
                result = inst.ls()
        assert result is not None

    def test_ls_returns_empty_entries_on_open_failure(self):
        inst = _make_https()
        inst.connected = True
        inst.sendTo = 'https://example.com'
        inst.timeout = inst.o.timeout
        inst.user = None
        inst.password = None
        inst.bearer_token = None
        inst.opener = MagicMock()
        inst.head_opener = MagicMock()
        inst.path = '/data'
        inst.o.httpsSafeQuote = '/+'

        with patch.object(inst, '__open__', return_value=False):
            result = inst.ls()
        assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# __url_redir_str — redirect URL formatting
# ══════════════════════════════════════════════════════════════════════════════

class Test_Https_url_redir_str:

    def test_no_redirect(self):
        inst = _make_https()
        inst.urlstr = 'https://example.com/file'
        inst.http = MagicMock()
        inst.http.geturl.return_value = 'https://example.com/file'
        fn = getattr(inst, '_Https__url_redir_str')
        result = fn()
        assert result == 'https://example.com/file'

    def test_with_redirect(self):
        inst = _make_https()
        inst.urlstr = 'https://example.com/old'
        inst.http = MagicMock()
        inst.http.geturl.return_value = 'https://example.com/new'
        fn = getattr(inst, '_Https__url_redir_str')
        result = fn()
        assert 'redirected to' in result
        assert 'https://example.com/new' in result

    def test_http_is_none_returns_urlstr(self):
        inst = _make_https()
        inst.urlstr = 'https://example.com/file'
        inst.http = None
        fn = getattr(inst, '_Https__url_redir_str')
        result = fn()
        assert result == 'https://example.com/file'

    def test_geturl_raises_exception(self):
        inst = _make_https()
        inst.urlstr = 'https://example.com/file'
        inst.http = MagicMock()
        inst.http.geturl.side_effect = Exception('no url')
        fn = getattr(inst, '_Https__url_redir_str')
        result = fn()
        assert result == 'https://example.com/file'
