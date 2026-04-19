import pytest
import types
from unittest.mock import patch
import sarracenia
import sarracenia.config
import sarracenia.flowcb.accept.dateappend
from sarracenia.flowcb.accept.dateappend import Dateappend


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


def _make_msg(new_file):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'data/file.txt'
    m['new_file'] = new_file
    m['_deleteOnPost'] = set()
    return m


def test_date_appended_to_new_file():
    cb = Dateappend(_make_opts())
    msg = _make_msg('datafile.xml')
    with patch('sarracenia.flowcb.accept.dateappend.time') as mock_time:
        mock_time.strftime.return_value = '_20240101080000'
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg])
        cb.after_accept(wl)
    assert msg['new_file'] == 'datafile.xml_20240101080000'


def test_already_appended_file_not_double_appended():
    cb = Dateappend(_make_opts())
    date_suffix = '_20240101080000'
    msg = _make_msg('datafile.xml' + date_suffix)
    with patch('sarracenia.flowcb.accept.dateappend.time') as mock_time:
        mock_time.strftime.return_value = date_suffix
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg])
        cb.after_accept(wl)
    # Already ends with the date, should not double-append
    assert msg['new_file'] == 'datafile.xml' + date_suffix


def test_empty_worklist_ok():
    cb = Dateappend(_make_opts())
    wl = _make_worklist([])
    cb.after_accept(wl)
    assert wl.incoming == []


def test_multiple_messages_each_get_date():
    cb = Dateappend(_make_opts())
    msg1 = _make_msg('f1.txt')
    msg2 = _make_msg('f2.txt')
    with patch('sarracenia.flowcb.accept.dateappend.time') as mock_time:
        mock_time.strftime.return_value = '_20240615120000'
        mock_time.localtime.return_value = None
        wl = _make_worklist([msg1, msg2])
        cb.after_accept(wl)
    assert msg1['new_file'] == 'f1.txt_20240615120000'
    assert msg2['new_file'] == 'f2.txt_20240615120000'
