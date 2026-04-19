import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.sftp_absolute
from sarracenia.flowcb.accept.sftp_absolute import Sftp_absolute


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


def _make_msg(baseUrl, relPath='data/file.txt'):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = baseUrl
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def test_sftp_url_no_path_gets_slash_appended():
    cb = Sftp_absolute(_make_opts())
    msg = _make_msg('sftp://user@host')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'sftp://user@host/'


def test_sftp_url_relative_path_gets_slash_prepended():
    cb = Sftp_absolute(_make_opts())
    msg = _make_msg('sftp://user@host/relative/path')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    # path starts with '/', no change needed
    assert msg['baseUrl'] == 'sftp://user@host/relative/path'


def test_sftp_url_already_absolute_unchanged():
    cb = Sftp_absolute(_make_opts())
    msg = _make_msg('sftp://user@host//absolute/path')
    # This has path starting with / after parse, no change
    original = msg['baseUrl']
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == original


def test_non_sftp_url_not_modified():
    cb = Sftp_absolute(_make_opts())
    msg = _make_msg('http://example.com')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert msg['baseUrl'] == 'http://example.com'


def test_empty_worklist_ok():
    cb = Sftp_absolute(_make_opts())
    wl = _make_worklist([])
    cb.after_accept(wl)
    assert wl.incoming == []


def test_mixed_messages_only_sftp_modified():
    cb = Sftp_absolute(_make_opts())
    sftp_msg = _make_msg('sftp://host')
    http_msg = _make_msg('http://example.com')
    wl = _make_worklist([sftp_msg, http_msg])
    cb.after_accept(wl)
    assert sftp_msg['baseUrl'] == 'sftp://host/'
    assert http_msg['baseUrl'] == 'http://example.com'