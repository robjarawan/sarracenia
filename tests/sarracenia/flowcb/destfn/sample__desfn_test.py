import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.destfn.sample
from sarracenia.flowcb.destfn.sample import Sample


def _make_opts():
    return sarracenia.config.default_config()


def _make_msg(relPath):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def test_prefix_added_to_filename():
    cb = Sample(_make_opts())
    msg = _make_msg('dir/data.txt')
    result = cb.destfn(msg)
    assert result == 'renamed_data.txt'


def test_deeply_nested_path_only_last_segment_prefixed():
    cb = Sample(_make_opts())
    msg = _make_msg('/a/b/c/d/file.bin')
    result = cb.destfn(msg)
    assert result == 'renamed_file.bin'


def test_message_field_set_by_destfn():
    cb = Sample(_make_opts())
    msg = _make_msg('dir/file.txt')
    cb.destfn(msg)
    assert msg['destfn_added_prefix'] == 'renamed_'


def test_single_segment_relpath():
    cb = Sample(_make_opts())
    msg = _make_msg('justfilename.txt')
    result = cb.destfn(msg)
    assert result == 'renamed_justfilename.txt'