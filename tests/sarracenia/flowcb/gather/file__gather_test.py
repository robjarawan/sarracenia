import pytest
import os
import sys
from collections import OrderedDict
from unittest.mock import patch, MagicMock, PropertyMock

import sarracenia.config
import sarracenia.flowcb.gather.file as gather_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeGatherFile:
    """Lightweight stand-in so we can call set_blockSize without a full File."""
    set_blockSize = gather_file.File.set_blockSize


def _make_options(tmp_path):
    """Return a minimal options object that satisfies File.__init__."""
    options = sarracenia.config.default_config()
    options.post_baseDir = str(tmp_path)
    options.blockSize = 0
    options.follow_symlinks = False
    options.realpathPost = False
    options.recursive = True
    options.force_polling = True
    options.post_baseUrl = 'file:/'
    options.identity_method = 'sha512'
    options.identity_arbitrary_value = None
    options.permDefault = 0o644
    options.permDirDefault = 0o755
    options.nodupe_ttl = 0
    options.fileEvents = set(
        ['create', 'modify', 'delete', 'link', 'mkdir', 'rmdir'])
    options.component = 'watch'
    options.pid_filename = str(tmp_path / 'test.pid')
    return options


def _make_file_instance(tmp_path):
    """Create a gather_file.File instance for post1file testing."""
    options = _make_options(tmp_path)
    instance = gather_file.File(options)
    return instance


# ===================================================================
# Tests for set_blockSize
# ===================================================================

class Test_set_blockSize:

    def setup_method(self):
        self.obj = FakeGatherFile()

    def test_blockSize_zero_returns_50MB(self):
        result = self.obj.set_blockSize(0, 99999)
        assert result == 50 * 1024 * 1024

    def test_blockSize_one_returns_filesize(self):
        result = self.obj.set_blockSize(1, 12345)
        assert result == 12345

    def test_blockSize_custom_value(self):
        result = self.obj.set_blockSize(8192, 99999)
        assert result == 8192

    def test_blockSize_large_custom(self):
        big = 100 * 1024 * 1024
        result = self.obj.set_blockSize(big, 99999)
        assert result == big

    def test_blockSize_one_zero_filesize(self):
        result = self.obj.set_blockSize(1, 0)
        assert result == 0

    def test_blockSize_negative_value(self):
        result = self.obj.set_blockSize(-1, 99999)
        assert result == -1


# ===================================================================
# Tests for post1file
# ===================================================================

class Test_post1file:

    def _instance(self, tmp_path, **overrides):
        inst = _make_file_instance(tmp_path)
        for k, v in overrides.items():
            setattr(inst.o, k, v)
        return inst

    # ------------------------------------------------------------------
    # 1. Regular file  → post_file is called
    # ------------------------------------------------------------------
    def test_post1file_regular_file(self, tmp_path):
        inst = self._instance(tmp_path)

        # Create a real file
        real_file = tmp_path / 'hello.txt'
        real_file.write_text('hello world')
        lstat = sarracenia.stat(str(real_file))

        sentinel = MagicMock()
        sentinel.return_value = [{'_tag': 'file_msg'}]
        inst.post_file = sentinel

        msgs = inst.post1file(str(real_file), lstat)

        sentinel.assert_called_once_with(str(real_file), lstat)
        assert msgs == [{'_tag': 'file_msg'}]

    # ------------------------------------------------------------------
    # 2. Deleted file (lstat=None) → post_delete is called
    # ------------------------------------------------------------------
    def test_post1file_deleted_file(self, tmp_path):
        inst = self._instance(tmp_path)

        sentinel = MagicMock()
        sentinel.return_value = [{'_tag': 'delete_msg'}]
        inst.post_delete = sentinel

        fake_path = str(tmp_path / 'gone.txt')
        msgs = inst.post1file(fake_path, None)

        sentinel.assert_called_once_with(fake_path, key=None, value=None,
                                         is_directory=False)
        assert msgs == [{'_tag': 'delete_msg'}]

    # ------------------------------------------------------------------
    # 3. Symlink with follow_symlinks=False → post_link only
    # ------------------------------------------------------------------
    def test_post1file_symlink_no_follow(self, tmp_path):
        inst = self._instance(tmp_path, follow_symlinks=False)

        target = tmp_path / 'target.txt'
        target.write_text('data')
        link = tmp_path / 'mylink'
        link.symlink_to(target)

        link_sentinel = MagicMock(return_value=[{'_tag': 'link_msg'}])
        file_sentinel = MagicMock(return_value=[])
        inst.post_link = link_sentinel
        inst.post_file = file_sentinel

        msgs = inst.post1file(str(link), sarracenia.stat(str(target)))

        link_sentinel.assert_called_once_with(str(link))
        file_sentinel.assert_not_called()
        assert msgs == [{'_tag': 'link_msg'}]

    # ------------------------------------------------------------------
    # 4. Path normalisation: /./  is removed
    # ------------------------------------------------------------------
    def test_post1file_path_normalization(self, tmp_path):
        inst = self._instance(tmp_path)

        real_file = tmp_path / 'norm.txt'
        real_file.write_text('x')
        lstat = sarracenia.stat(str(real_file))

        sentinel = MagicMock(return_value=[{'_tag': 'norm_msg'}])
        inst.post_file = sentinel

        # Inject /./ into the path – post1file should strip it
        dirty_path = str(real_file).replace('/norm.txt', '/./norm.txt')
        msgs = inst.post1file(dirty_path, lstat)

        called_path = sentinel.call_args[0][0]
        assert '/./' not in called_path
        assert msgs == [{'_tag': 'norm_msg'}]

    # ------------------------------------------------------------------
    # 5. Non-existent path with non-None lstat → returns empty
    #    (path is neither file, dir, nor link on disk)
    # ------------------------------------------------------------------
    def test_post1file_nonexistent_path(self, tmp_path):
        inst = self._instance(tmp_path)

        fake_path = str(tmp_path / 'no_such_file.dat')
        fake_lstat = MagicMock()

        link_sentinel = MagicMock(return_value=[])
        delete_sentinel = MagicMock(return_value=[])
        file_sentinel = MagicMock(return_value=[])
        inst.post_link = link_sentinel
        inst.post_delete = delete_sentinel
        inst.post_file = file_sentinel

        msgs = inst.post1file(fake_path, fake_lstat)

        link_sentinel.assert_not_called()
        delete_sentinel.assert_not_called()
        file_sentinel.assert_not_called()
        assert msgs == []

    # ------------------------------------------------------------------
    # 6. Directory with lstat → post_file is called
    # ------------------------------------------------------------------
    def test_post1file_directory(self, tmp_path):
        inst = self._instance(tmp_path)

        sub = tmp_path / 'subdir'
        sub.mkdir()
        lstat = sarracenia.stat(str(sub))

        sentinel = MagicMock(return_value=[{'_tag': 'dir_msg'}])
        inst.post_file = sentinel

        msgs = inst.post1file(str(sub), lstat)

        sentinel.assert_called_once_with(str(sub), lstat)
        assert msgs == [{'_tag': 'dir_msg'}]
