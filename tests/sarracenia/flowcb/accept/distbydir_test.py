import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.distbydir
from sarracenia.flowcb.accept.distbydir import Distbydir


def _make_opts():
    opts = sarracenia.config.default_config()
    return opts


def _make_worklist(msgs):
    wl = types.SimpleNamespace()
    wl.incoming = msgs
    wl.ok = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg(relPath):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def test_empty_worklist_no_change():
    cb = Distbydir(_make_opts())
    wl = _make_worklist([])
    cb.after_accept(wl)
    assert wl.incoming == []


def test_exchangesplitoverride_set_on_message():
    cb = Distbydir(_make_opts())
    msg = _make_msg('a/b/c/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert 'exchangeSplitOverride' in msg


def test_exchangesplitoverride_added_to_deleteOnPost():
    cb = Distbydir(_make_opts())
    msg = _make_msg('a/b/c/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    assert 'exchangeSplitOverride' in msg['_deleteOnPost']


def test_default_offset_minus_2_hashes_second_to_last_dir():
    import hashlib
    cb = Distbydir(_make_opts())
    msg = _make_msg('top/second/third/file.txt')
    wl = _make_worklist([msg])
    cb.after_accept(wl)
    # offset -2 on ['top','second','third','file.txt'] => 'third'
    expected = int(hashlib.md5('third'.encode()).hexdigest()[0], 16)
    assert msg['exchangeSplitOverride'] == expected


def test_consistent_hash_for_same_dir():
    cb = Distbydir(_make_opts())
    msg1 = _make_msg('a/b/c/file1.txt')
    msg2 = _make_msg('a/b/c/file2.txt')
    wl = _make_worklist([msg1, msg2])
    cb.after_accept(wl)
    assert msg1['exchangeSplitOverride'] == msg2['exchangeSplitOverride']


def test_different_dirs_may_produce_different_hash():
    cb = Distbydir(_make_opts())
    msg1 = _make_msg('root/dirA/sub/file.txt')
    msg2 = _make_msg('root/dirB/sub/file.txt')
    wl = _make_worklist([msg1, msg2])
    cb.after_accept(wl)
    # They are hashed from 'sub' (offset -2), same directory, so should be equal
    assert msg1['exchangeSplitOverride'] == msg2['exchangeSplitOverride']