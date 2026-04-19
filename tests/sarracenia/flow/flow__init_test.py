import pytest
import os
import types
from unittest.mock import MagicMock, patch
import sarracenia
import sarracenia.config


def _make_flow_stub(tmp_path):
    """Return a minimal stub with only the methods being tested, no network."""
    from sarracenia.flow import Flow
    # Create a minimal Flow-like object by patching __init__
    opts = sarracenia.config.default_config()
    opts.permDirDefault = 0o755

    flow = object.__new__(Flow)
    flow.o = opts
    flow.worklist = types.SimpleNamespace()
    flow.worklist.directories_ok = []
    return flow


# ── removeOneFile ─────────────────────────────────────────────────────────────

def test_remove_existing_file_returns_true(tmp_path):
    flow = _make_flow_stub(tmp_path)
    f = tmp_path / 'to_delete.txt'
    f.write_text('data')
    result = flow.removeOneFile(str(f))
    assert result is True
    assert not f.exists()


def test_remove_nonexistent_path_returns_true(tmp_path):
    flow = _make_flow_stub(tmp_path)
    path = str(tmp_path / 'ghost.txt')
    result = flow.removeOneFile(path)
    assert result is True


def test_remove_directory_returns_true(tmp_path):
    flow = _make_flow_stub(tmp_path)
    d = tmp_path / 'emptydir'
    d.mkdir()
    result = flow.removeOneFile(str(d))
    assert result is True
    assert not d.exists()


def test_remove_symlink_returns_true(tmp_path):
    flow = _make_flow_stub(tmp_path)
    target = tmp_path / 'target.txt'
    target.write_text('hello')
    link = tmp_path / 'mylink'
    link.symlink_to(target)
    result = flow.removeOneFile(str(link))
    assert result is True
    assert not link.exists()
    assert target.exists()  # only link removed, not target


def test_remove_when_unlink_fails_returns_false(tmp_path):
    flow = _make_flow_stub(tmp_path)
    f = tmp_path / 'file.txt'
    f.write_text('data')
    with patch('os.unlink', side_effect=OSError('denied')):
        result = flow.removeOneFile(str(f))
    assert result is False


# ── link1file ─────────────────────────────────────────────────────────────────

def test_link1file_creates_symlink(tmp_path):
    flow = _make_flow_stub(tmp_path)
    target = tmp_path / 'target.txt'
    target.write_text('hello')
    link_dir = tmp_path / 'linkdir'
    link_dir.mkdir()

    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'file:'
    m['relPath'] = 'target.txt'
    m['new_dir'] = str(link_dir)
    m['new_file'] = 'mylink.txt'
    m['fileOp'] = {'link': str(target)}
    m['_deleteOnPost'] = set()

    result = flow.link1file(m)
    assert result is True
    link_path = link_dir / 'mylink.txt'
    assert link_path.is_symlink()


def test_link1file_creates_directory_if_needed(tmp_path):
    flow = _make_flow_stub(tmp_path)
    target = tmp_path / 'tgt.txt'
    target.write_text('data')
    new_dir = tmp_path / 'newsubdir'

    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'file:'
    m['relPath'] = 'tgt.txt'
    m['new_dir'] = str(new_dir)
    m['new_file'] = 'link.txt'
    m['fileOp'] = {'link': str(target)}
    m['_deleteOnPost'] = set()

    result = flow.link1file(m)
    assert result is True
    assert new_dir.exists()
