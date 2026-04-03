import pytest
import types
import os
from unittest.mock import MagicMock

from sarracenia.flowcb.work.delete import Delete
from sarracenia import Message as SR3Message
import sarracenia.config


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_message(new_dir='/tmp/test', new_file='file.txt'):
    m = SR3Message()
    m['new_dir'] = new_dir
    m['new_file'] = new_file
    return m


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def test___init__():
    options = make_options()
    # Remove baseDir/post_baseDir so they don't pollute sacredDirs
    if hasattr(options, 'baseDir'):
        delattr(options, 'baseDir')
    if hasattr(options, 'post_baseDir'):
        delattr(options, 'post_baseDir')
    deletecb = Delete(options)
    assert deletecb is not None
    assert options.delete_source is True
    assert options.delete_destination is False
    assert deletecb.dirsOfDeletion == set()
    assert deletecb.sacredDirs == set()


def test___init___with_baseDir():
    options = make_options()
    options.baseDir = '/some/base'
    options.post_baseDir = '/some/post_base'
    deletecb = Delete(options)
    assert '/some/base' in deletecb.sacredDirs
    assert '/some/post_base' in deletecb.sacredDirs


def test_after_accept_sets_delete_source():
    options = make_options()
    deletecb = Delete(options)

    worklist = make_worklist()
    msg = make_message()
    msg['baseUrl'] = 'file:///data'
    msg['relPath'] = 'subdir/file.txt'
    msg['_deleteOnPost'] = set(['_format'])
    worklist.incoming = [msg]

    deletecb.after_accept(worklist)

    assert 'delete_source' in msg['_deleteOnPost']
    assert msg['delete_source'] == '///data/subdir/file.txt'


def test_after_accept_url_with_trailing_slash():
    options = make_options()
    deletecb = Delete(options)

    worklist = make_worklist()
    msg = make_message()
    msg['baseUrl'] = 'file:///data/'
    msg['relPath'] = 'subdir/file.txt'
    msg['_deleteOnPost'] = set(['_format'])
    worklist.incoming = [msg]

    deletecb.after_accept(worklist)

    # trailing slash on baseUrl should not produce double slash
    assert '//subdir' not in msg['delete_source']
    assert msg['delete_source'] == '///data/subdir/file.txt'


def test_after_work_deletes_source_file(tmp_path):
    options = make_options()
    deletecb = Delete(options)

    src_file = tmp_path / 'source.txt'
    src_file.write_text('hello')

    worklist = make_worklist()
    msg = make_message(str(tmp_path), 'source.txt')
    msg['delete_source'] = str(src_file)
    worklist.ok = [msg]

    deletecb.after_work(worklist)
    assert not src_file.exists()


def test_after_work_skips_remove_fileop():
    options = make_options()
    deletecb = Delete(options)

    worklist = make_worklist()
    msg = make_message()
    msg['delete_source'] = '/nonexistent/file.txt'
    msg['fileOp'] = {'remove': True}
    worklist.ok = [msg]

    # Should not attempt any deletion, so no error even though file doesn't exist
    deletecb.after_work(worklist)


def test_after_work_delete_destination(tmp_path):
    options = make_options()
    options.delete_source = False
    options.delete_destination = True
    deletecb = Delete(options)

    dest_file = tmp_path / 'dest.txt'
    dest_file.write_text('data')

    worklist = make_worklist()
    msg = make_message(str(tmp_path), 'dest.txt')
    worklist.ok = [msg]

    deletecb.after_work(worklist)
    assert not dest_file.exists()


def test_after_work_handles_missing_file(caplog):
    options = make_options()
    deletecb = Delete(options)

    worklist = make_worklist()
    msg = make_message('/nonexistent/dir', 'missing.txt')
    msg['delete_source'] = '/nonexistent/dir/missing.txt'
    worklist.ok = [msg]

    # Should not raise, just log an error
    deletecb.after_work(worklist)
    assert any('could not unlink' in m for m in caplog.messages)