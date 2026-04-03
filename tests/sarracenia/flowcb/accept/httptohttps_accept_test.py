import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.httptohttps
from sarracenia.flowcb.accept.httptohttps import HttpToHttps


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


def _make_msg(baseUrl):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = baseUrl
    m['relPath'] = 'data/file.txt'
    m['_deleteOnPost'] = set()
    return m


def test_http_converted_to_https():
    cb = HttpToHttps(_make_opts())
    msg = _make_msg('http://example.com')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'https://example.com'


def test_https_unchanged():
    cb = HttpToHttps(_make_opts())
    msg = _make_msg('https://example.com')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'https://example.com'


def test_ftp_scheme_unchanged():
    cb = HttpToHttps(_make_opts())
    msg = _make_msg('ftp://example.com')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'ftp://example.com'


def test_empty_worklist_ok():
    cb = HttpToHttps(_make_opts())
    wl = _make_worklist([])
    cb.after_accept(wl)
    assert wl.incoming == []


def test_mixed_messages_only_http_changed():
    cb = HttpToHttps(_make_opts())
    http_msg = _make_msg('http://example.com')
    https_msg = _make_msg('https://secure.example.com')
    wl = _make_worklist([http_msg, https_msg])
    cb.after_accept(wl)
    assert http_msg['baseUrl'] == 'https://example.com'
    assert https_msg['baseUrl'] == 'https://secure.example.com'
