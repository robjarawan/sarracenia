import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.authenticate
from sarracenia.flowcb.authenticate import BearerToken


def _make_opts():
    return sarracenia.config.default_config()


def _make_worklist(msgs):
    wl = types.SimpleNamespace()
    wl.incoming = msgs
    wl.ok = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg(baseUrl='https://example.com/file.txt'):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = baseUrl
    m['relPath'] = 'file.txt'
    m['_deleteOnPost'] = set()
    return m


class FixedToken(BearerToken):
    def __init__(self, options, token):
        super().__init__(options)
        self._fixed_token = token

    def get_token(self):
        return self._fixed_token


def test_base_get_token_returns_none():
    opts = _make_opts()
    cb = BearerToken(opts)
    assert cb.get_token() is None


def test_token_added_to_credentials():
    opts = _make_opts()
    cb = FixedToken(opts, 'mytoken123')
    msg = _make_msg('https://data.example.com/file.bin')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    ok, details = opts.credentials.get(msg['baseUrl'])
    assert ok
    assert details.bearer_token == 'mytoken123'


def test_same_token_not_error_on_second_call():
    opts = _make_opts()
    cb = FixedToken(opts, 'token-abc')
    msg = _make_msg('https://example.com/f.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    wl2 = _make_worklist([msg])
    cb.after_accept(wl2)  # should not error
    ok, details = opts.credentials.get(msg['baseUrl'])
    assert ok and details.bearer_token == 'token-abc'


def test_empty_worklist_ok():
    opts = _make_opts()
    cb = FixedToken(opts, 'token')
    wl = _make_worklist([])
    cb.after_accept(wl)


def test_multiple_messages_all_get_credential():
    opts = _make_opts()
    cb = FixedToken(opts, 'tok')
    msg1 = _make_msg('https://a.com/f1.txt')
    msg2 = _make_msg('https://b.com/f2.txt')
    wl = _make_worklist([msg1, msg2])
    cb.after_accept(wl)
    ok1, d1 = opts.credentials.get(msg1['baseUrl'])
    ok2, d2 = opts.credentials.get(msg2['baseUrl'])
    assert ok1 and d1.bearer_token == 'tok'
    assert ok2 and d2.bearer_token == 'tok'
