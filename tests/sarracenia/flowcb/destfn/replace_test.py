import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.destfn.replace
from sarracenia.flowcb.destfn.replace import Replace


def _make_opts():
    opts = sarracenia.config.default_config()
    opts.destfn_replace = []
    return opts


def _make_msg(relPath):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def test_empty_replace_list_returns_original_filename():
    opts = _make_opts()
    cb = Replace(opts)
    msg = _make_msg('dir/subdir/data.txt')
    result = cb.destfn(msg)
    assert result == 'data.txt'


def test_single_replacement_rule_applied():
    opts = _make_opts()
    opts.destfn_replace = ['SRCN,sRCn']
    cb = Replace(opts)
    msg = _make_msg('dir/fileSRCNdata.txt')
    result = cb.destfn(msg)
    assert result == 'filesRCndata.txt'


def test_multiple_rules_applied_in_sequence():
    opts = _make_opts()
    opts.destfn_replace = ['AAA,bbb', 'CCC,ddd']
    cb = Replace(opts)
    msg = _make_msg('dir/AAAfileCCC.txt')
    result = cb.destfn(msg)
    assert result == 'bbbfileddd.txt'


def test_before_string_not_found_returns_unchanged():
    opts = _make_opts()
    opts.destfn_replace = ['NOTPRESENT,replacement']
    cb = Replace(opts)
    msg = _make_msg('dir/originalname.txt')
    result = cb.destfn(msg)
    assert result == 'originalname.txt'


def test_only_first_occurrence_replaced():
    opts = _make_opts()
    opts.destfn_replace = ['aa,XX']
    cb = Replace(opts)
    msg = _make_msg('dir/aafileaa.txt')
    result = cb.destfn(msg)
    assert result == 'XXfileaa.txt'


def test_deeply_nested_relpath_extracts_only_filename():
    opts = _make_opts()
    opts.destfn_replace = ['old,new']
    cb = Replace(opts)
    msg = _make_msg('/a/b/c/d/oldfile.txt')
    result = cb.destfn(msg)
    assert result == 'newfile.txt'


def test_replacement_does_not_touch_directory_part():
    opts = _make_opts()
    opts.destfn_replace = ['dir,XXX']
    cb = Replace(opts)
    msg = _make_msg('dir/dir/filename.txt')
    result = cb.destfn(msg)
    # only filename portion is operated on; 'dir' does NOT appear in 'filename.txt'
    assert result == 'filename.txt'