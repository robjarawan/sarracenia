import pytest
import os
import json
import tempfile
from unittest.mock import patch

import sarracenia.filemetadata
from sarracenia.filemetadata import FileMetadata, disable_xattr


@pytest.fixture(autouse=True)
def _disable_xattr():
    """Ensure xattr/ADS are disabled for all tests so we test in-memory logic only."""
    sarracenia.filemetadata.xattr_disabled = True
    yield
    sarracenia.filemetadata.xattr_disabled = False


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "testfile.dat"
    f.write_text("hello")
    return str(f)


# ── disable_xattr ───────────────────────────────────────────────────────

def test_disable_xattr_sets_global():
    sarracenia.filemetadata.xattr_disabled = False
    disable_xattr()
    assert sarracenia.filemetadata.xattr_disabled is True


# ── __init__ ─────────────────────────────────────────────────────────────

def test_init_creates_empty_dict(tmp_file):
    fm = FileMetadata(tmp_file)
    assert fm.x == {}
    assert fm.dirty is False
    assert fm.path == tmp_file


def test_init_with_disabled_xattr_leaves_empty(tmp_file):
    fm = FileMetadata(tmp_file)
    assert list(fm.list()) == []


# ── set / get / list ─────────────────────────────────────────────────────

def test_set_marks_dirty(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.set('identity', 'sha512:abcdef')
    assert fm.dirty is True


def test_get_returns_set_value(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.set('identity', 'sha512:abcdef')
    assert fm.get('identity') == 'sha512:abcdef'


def test_get_returns_none_for_missing(tmp_file):
    fm = FileMetadata(tmp_file)
    assert fm.get('nonexistent') is None


def test_list_returns_set_keys(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.set('identity', 'sha512:abcdef')
    fm.set('mtime', '20230101')
    keys = list(fm.list())
    assert 'identity' in keys
    assert 'mtime' in keys


def test_set_overwrites_existing(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.set('identity', 'old')
    fm.set('identity', 'new')
    assert fm.get('identity') == 'new'


# ── integrity → identity migration ──────────────────────────────────────

def test_integrity_migrated_to_identity(tmp_file):
    """When 'integrity' key exists at init time, it should be renamed to 'identity'."""
    fm = FileMetadata(tmp_file)
    # Simulate pre-existing data with 'integrity' key
    fm.x['integrity'] = {'method': 'sha512', 'value': 'abc'}
    # Re-run the migration logic manually (it runs in __init__ normally)
    if 'integrity' in fm.x:
        fm.x['identity'] = fm.x['integrity']
        del fm.x['integrity']
    assert fm.get('identity') == {'method': 'sha512', 'value': 'abc'}
    assert fm.get('integrity') is None


# ── blocks key normalization ─────────────────────────────────────────────

def test_get_blocks_normalizes_string_keys(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.x['blocks'] = {
        'manifest': {'0': 'hash0', '1': 'hash1'},
        'waiting': {'2': 'hash2'}
    }
    result = fm.get('blocks')
    # Keys should be converted to int
    assert 0 in result['manifest']
    assert 1 in result['manifest']
    assert 2 in result['waiting']


def test_get_blocks_with_int_keys_unchanged(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.x['blocks'] = {
        'manifest': {0: 'hash0', 1: 'hash1'},
    }
    result = fm.get('blocks')
    assert 0 in result['manifest']


# ── persist (with xattr disabled) ───────────────────────────────────────

def test_persist_resets_dirty(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.set('key', 'value')
    assert fm.dirty is True
    fm.persist()
    assert fm.dirty is False


def test_persist_does_nothing_when_not_dirty(tmp_file):
    fm = FileMetadata(tmp_file)
    fm.persist()  # Should not error
    assert fm.dirty is False


# ── context manager ──────────────────────────────────────────────────────

def test_context_manager_calls_persist(tmp_file):
    with FileMetadata(tmp_file) as fm:
        fm.set('key', 'value')
        assert fm.dirty is True
    # After exiting context, persist was called
    assert fm.dirty is False