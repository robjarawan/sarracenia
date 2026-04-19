import pytest
import types
from unittest.mock import patch
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.hourtree
from sarracenia.flowcb.accept.hourtree import HourTree


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


def _make_msg(new_dir, new_file):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'data/file.txt'
    m['new_dir'] = new_dir
    m['new_file'] = new_file
    m['_deleteOnPost'] = set()
    return m


def test_hour_appended_to_new_dir():
    cb = HourTree(_make_opts())
    msg = _make_msg('/output/dir', 'file.txt')
    with patch('sarracenia.flowcb.accept.hourtree.time') as mock_time:
        mock_time.strftime.return_value = '14'
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg])
        cb.after_accept(wl)
    assert msg['new_dir'] == '/output/dir/14'


def test_hour_inserted_into_new_file_path():
    cb = HourTree(_make_opts())
    msg = _make_msg('/output', 'file.txt')
    with patch('sarracenia.flowcb.accept.hourtree.time') as mock_time:
        mock_time.strftime.return_value = '09'
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg])
        cb.after_accept(wl)
    assert msg['new_file'].endswith('/09/file.txt')


def test_empty_worklist_ok():
    cb = HourTree(_make_opts())
    wl = _make_worklist([])
    cb.after_accept(wl)
    assert wl.incoming == []


def test_multiple_messages_processed():
    cb = HourTree(_make_opts())
    msg1 = _make_msg('/dir1', 'f1.txt')
    msg2 = _make_msg('/dir2', 'f2.txt')
    with patch('sarracenia.flowcb.accept.hourtree.time') as mock_time:
        mock_time.strftime.return_value = '23'
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg1, msg2])
        cb.after_accept(wl)
    assert msg1['new_dir'] == '/dir1/23'
    assert msg2['new_dir'] == '/dir2/23'
