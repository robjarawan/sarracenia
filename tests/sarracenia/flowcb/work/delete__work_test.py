import pytest
import types
import os
import time
from unittest.mock import MagicMock, patch

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


# === __init__ tests ===

class Test_init:
    def test_defaults(self):
        options = make_options()
        deletecb = Delete(options)
        assert deletecb is not None
        assert options.delete_source is True
        assert options.delete_destination is False
        assert deletecb.dirsOfDeletion == set()

    def test_sacredDirs_from_baseDir(self):
        options = make_options()
        options.baseDir = '/some/base'
        options.post_baseDir = '/some/post_base'
        deletecb = Delete(options)
        assert '/some/base' in deletecb.sacredDirs
        assert '/some/post_base' in deletecb.sacredDirs

    def test_sacredDirs_empty_when_no_baseDirs(self):
        options = make_options()
        deletecb = Delete(options)
        assert isinstance(deletecb.sacredDirs, set)


# === after_accept tests ===

class Test_after_accept:
    def test_sets_delete_source_key(self):
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

    def test_url_with_trailing_slash(self):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        msg = make_message()
        msg['baseUrl'] = 'file:///data/'
        msg['relPath'] = 'subdir/file.txt'
        msg['_deleteOnPost'] = set(['_format'])
        worklist.incoming = [msg]
        deletecb.after_accept(worklist)
        assert '//subdir' not in msg['delete_source']
        assert msg['delete_source'] == '///data/subdir/file.txt'

    def test_url_without_trailing_slash(self):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        msg = make_message()
        msg['baseUrl'] = 'file:///incoming'
        msg['relPath'] = 'data/file.dat'
        msg['_deleteOnPost'] = set(['_format'])
        worklist.incoming = [msg]
        deletecb.after_accept(worklist)
        assert msg['delete_source'] == '///incoming/data/file.dat'

    def test_empty_incoming(self):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        worklist.incoming = []
        deletecb.after_accept(worklist)

    def test_delete_source_disabled(self):
        options = make_options()
        options.delete_source = False
        deletecb = Delete(options)
        worklist = make_worklist()
        msg = make_message()
        msg['baseUrl'] = 'file:///data'
        msg['relPath'] = 'file.txt'
        msg['_deleteOnPost'] = set()
        worklist.incoming = [msg]
        deletecb.after_accept(worklist)
        assert 'delete_source' not in msg


# === after_work tests ===

class Test_after_work:
    def test_deletes_source_file(self, tmp_path):
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

    def test_skips_remove_fileop(self):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        msg = make_message()
        msg['delete_source'] = '/nonexistent/file.txt'
        msg['fileOp'] = {'remove': True}
        worklist.ok = [msg]
        deletecb.after_work(worklist)

    def test_delete_destination(self, tmp_path):
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

    def test_handles_missing_file(self, caplog):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        msg = make_message('/nonexistent/dir', 'missing.txt')
        msg['delete_source'] = '/nonexistent/dir/missing.txt'
        worklist.ok = [msg]
        deletecb.after_work(worklist)
        assert any('could not unlink' in m for m in caplog.messages)

    def test_both_source_and_destination(self, tmp_path):
        options = make_options()
        options.delete_source = True
        options.delete_destination = True
        deletecb = Delete(options)
        src_file = tmp_path / 'source.txt'
        src_file.write_text('src')
        dest_file = tmp_path / 'dest.txt'
        dest_file.write_text('dest')
        worklist = make_worklist()
        msg = make_message(str(tmp_path), 'dest.txt')
        msg['delete_source'] = str(src_file)
        worklist.ok = [msg]
        deletecb.after_work(worklist)
        assert not src_file.exists()
        assert not dest_file.exists()

    def test_dirsOfDeletion_tracks_new_dir(self, tmp_path):
        options = make_options()
        options.delete_source = False
        options.delete_destination = True
        deletecb = Delete(options)
        dest_file = tmp_path / 'tracked.txt'
        dest_file.write_text('data')
        worklist = make_worklist()
        dir_path = str(tmp_path)
        msg = make_message(dir_path, 'tracked.txt')
        worklist.ok = [msg]
        deletecb.after_work(worklist)
        assert dir_path in deletecb.dirsOfDeletion

    def test_empty_ok_list(self):
        options = make_options()
        deletecb = Delete(options)
        worklist = make_worklist()
        worklist.ok = []
        deletecb.after_work(worklist)

    def test_multiple_messages(self, tmp_path):
        options = make_options()
        deletecb = Delete(options)
        files = []
        msgs = []
        for i in range(5):
            f = tmp_path / f'file_{i}.txt'
            f.write_text(f'data_{i}')
            files.append(f)
            msg = make_message(str(tmp_path), f'file_{i}.txt')
            msg['delete_source'] = str(f)
            msgs.append(msg)
        worklist = make_worklist()
        worklist.ok = msgs
        deletecb.after_work(worklist)
        for f in files:
            assert not f.exists()


# === on_housekeeping tests (bugs now fixed) ===

class Test_on_housekeeping:
    def test_skips_sacred_dirs(self, tmp_path):
        """After fix: sacred dirs are removed from dirsOfDeletion without RuntimeError."""
        options = make_options()
        deletecb = Delete(options)
        sacred = str(tmp_path / 'sacred')
        os.makedirs(sacred)
        deletecb.sacredDirs.add(sacred)
        deletecb.dirsOfDeletion.add(sacred)
        # Should not raise after fix (previously RuntimeError from set mutation during iteration)
        deletecb.on_housekeeping()
        assert sacred not in deletecb.dirsOfDeletion

    def test_removes_old_empty_dir(self, tmp_path):
        """After fix: old empty dirs are removed and parent is tracked."""
        options = make_options()
        options.housekeeping = 0  # any age qualifies
        deletecb = Delete(options)
        d = tmp_path / 'empty_dir'
        d.mkdir()
        old_time = time.time() - 1000
        os.utime(str(d), (old_time, old_time))
        deletecb.dirsOfDeletion = {str(d)}
        # After fix: should succeed without NameError/AttributeError
        deletecb.on_housekeeping()
        assert not d.exists()
        # Parent should be tracked for future cleanup
        assert str(tmp_path) in deletecb.dirsOfDeletion

    def test_empty_dirsOfDeletion(self):
        options = make_options()
        deletecb = Delete(options)
        deletecb.dirsOfDeletion = set()
        deletecb.on_housekeeping()

    def test_nonexistent_dir_skipped(self, tmp_path):
        options = make_options()
        deletecb = Delete(options)
        deletecb.dirsOfDeletion = {str(tmp_path / 'nonexistent')}
        deletecb.on_housekeeping()

    def test_nonempty_dir_not_removed(self, tmp_path):
        """Non-empty directories should not be removed."""
        options = make_options()
        options.housekeeping = 0
        deletecb = Delete(options)
        d = tmp_path / 'nonempty_dir'
        d.mkdir()
        (d / 'file.txt').write_text('content')
        old_time = time.time() - 1000
        os.utime(str(d), (old_time, old_time))
        deletecb.dirsOfDeletion = {str(d)}
        deletecb.on_housekeeping()
        assert d.exists()

    def test_young_empty_dir_not_removed(self, tmp_path):
        """Empty directories younger than housekeeping threshold should not be removed."""
        options = make_options()
        options.housekeeping = 99999  # very high threshold
        deletecb = Delete(options)
        d = tmp_path / 'young_dir'
        d.mkdir()
        deletecb.dirsOfDeletion = {str(d)}
        deletecb.on_housekeeping()
        assert d.exists()

    def test_multiple_sacred_dirs(self, tmp_path):
        """Multiple sacred dirs are all filtered."""
        options = make_options()
        deletecb = Delete(options)
        dirs = []
        for i in range(3):
            d = tmp_path / f'sacred_{i}'
            d.mkdir()
            dirs.append(str(d))
            deletecb.sacredDirs.add(str(d))
            deletecb.dirsOfDeletion.add(str(d))
        deletecb.on_housekeeping()
        for d in dirs:
            assert d not in deletecb.dirsOfDeletion

    def test_rmdir_failure_logs_error(self, tmp_path, caplog):
        """If rmdir fails, error is logged with correct variable (d, not f)."""
        options = make_options()
        options.housekeeping = 0
        deletecb = Delete(options)
        d = tmp_path / 'cant_delete'
        d.mkdir()
        old_time = time.time() - 1000
        os.utime(str(d), (old_time, old_time))
        deletecb.dirsOfDeletion = {str(d)}
        with patch('os.rmdir', side_effect=OSError("permission denied")):
            deletecb.on_housekeeping()
        assert any('could not unlink' in m for m in caplog.messages)
        # Verify the correct path is in the error message (d, not undefined f)
        assert any(str(d) in m for m in caplog.messages)

    def test_repeated_invocation(self, tmp_path):
        """Calling on_housekeeping multiple times doesn't crash."""
        options = make_options()
        options.housekeeping = 0
        deletecb = Delete(options)
        d = tmp_path / 'repeat_dir'
        d.mkdir()
        old_time = time.time() - 1000
        os.utime(str(d), (old_time, old_time))
        deletecb.dirsOfDeletion = {str(d)}
        deletecb.on_housekeeping()
        assert not d.exists()
        # Second call with parent dir — which now exists but may be non-empty
        deletecb.on_housekeeping()

    def test_mixed_sacred_and_deletable(self, tmp_path):
        """Mix of sacred and deletable dirs works correctly."""
        options = make_options()
        options.housekeeping = 0
        deletecb = Delete(options)
        sacred = tmp_path / 'sacred'
        sacred.mkdir()
        deletable = tmp_path / 'deletable'
        deletable.mkdir()
        old_time = time.time() - 1000
        os.utime(str(deletable), (old_time, old_time))
        deletecb.sacredDirs.add(str(sacred))
        deletecb.dirsOfDeletion = {str(sacred), str(deletable)}
        deletecb.on_housekeeping()
        assert str(sacred) not in deletecb.dirsOfDeletion
        assert not deletable.exists()