import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.tolocalfile
from sarracenia.flowcb.accept.tolocalfile import ToLocalFile


def _make_opts(baseDir=None):
    opts = sarracenia.config.default_config()
    opts.baseDir = baseDir
    return opts


def _make_worklist(msgs):
    wl = types.SimpleNamespace()
    wl.incoming = msgs
    wl.ok = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg(baseUrl, relPath='/data/file.txt'):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = baseUrl
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def test_already_file_url_passes_through():
    cb = ToLocalFile(_make_opts(baseDir='/base'))
    msg = _make_msg('file:', '/some/path/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg in wl.incoming
    assert len(wl.rejected) == 0


def test_http_with_basedir_converts_to_file_url():
    cb = ToLocalFile(_make_opts(baseDir='/var/www/html'))
    msg = _make_msg('http://example.com', '/product/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'file:'
    assert msg in wl.incoming


def test_original_url_saved_before_conversion():
    cb = ToLocalFile(_make_opts(baseDir='/base'))
    msg = _make_msg('http://example.com', '/data/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['saved_baseUrl'] == 'http://example.com'
    assert msg['saved_relPath'] == '/data/file.txt'


def test_saved_fields_added_to_deleteOnPost():
    cb = ToLocalFile(_make_opts(baseDir='/base'))
    msg = _make_msg('http://example.com', '/data/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert 'saved_baseUrl' in msg['_deleteOnPost']
    assert 'saved_relPath' in msg['_deleteOnPost']


def test_no_basedir_goes_to_rejected():
    # When baseDir is None/falsy and not file: url, message is rejected
    cb = ToLocalFile(_make_opts(baseDir=None))
    msg = _make_msg('http://example.com', '/data/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg in wl.rejected
    assert msg not in wl.incoming


def test_relpath_prefixed_with_basedir():
    cb = ToLocalFile(_make_opts(baseDir='/base'))
    msg = _make_msg('http://example.com', '/data/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['relPath'] == '/base//data/file.txt'
