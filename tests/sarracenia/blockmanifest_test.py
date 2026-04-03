import pytest
import os
import json

# BlockManifest requires flufl.lock; skip if not installed
try:
    import flufl.lock
    import sarracenia.blockmanifest
    from sarracenia.blockmanifest import BlockManifest
    HAVE_FLUFL = True
except ImportError:
    HAVE_FLUFL = False

pytestmark = pytest.mark.skipif(not HAVE_FLUFL, reason="flufl.lock not installed")


def test_context_manager_creates_file(tmp_path):
    path = str(tmp_path / 'testfile.bin')
    with BlockManifest(path) as bm:
        bm.set({'manifest': {0: 'abc'}, 'waiting': {}})
    manifest_path = path + '§block_manifest§'
    assert os.path.exists(manifest_path)


def test_set_removes_number_key(tmp_path):
    path = str(tmp_path / 'testfile.bin')
    with BlockManifest(path) as bm:
        bm.set({'number': 42, 'manifest': {}, 'waiting': {}})
        data = bm.get()
    assert 'number' not in data


def test_get_returns_set_data(tmp_path):
    path = str(tmp_path / 'testfile.bin')
    with BlockManifest(path) as bm:
        bm.set({'manifest': {1: 'xyz'}, 'waiting': {}})
        data = bm.get()
    assert data['manifest'][1] == 'xyz'


def test_persist_writes_json_to_disk(tmp_path):
    path = str(tmp_path / 'testfile.bin')
    with BlockManifest(path) as bm:
        bm.set({'key': 'value', 'waiting': {}})
    manifest_path = path + '§block_manifest§'
    with open(manifest_path) as f:
        on_disk = json.load(f)
    assert on_disk['key'] == 'value'


def test_round_trip_data_preserved(tmp_path):
    path = str(tmp_path / 'testfile.bin')
    original = {'manifest': {0: 'block0', 1: 'block1'}, 'waiting': {}}
    with BlockManifest(path) as bm:
        bm.set(original)

    # Read back from disk
    manifest_path = path + '§block_manifest§'
    with open(manifest_path) as f:
        on_disk = json.load(f)
    assert on_disk['manifest']['0'] == 'block0'  # JSON stringifies int keys


def test_get_none_when_nothing_set(tmp_path):
    path = str(tmp_path / 'empty.bin')
    with BlockManifest(path) as bm:
        result = bm.get()
    # Nothing was set; new_x and x are both empty → returns None
    assert result is None


def test_set_does_not_mutate_input(tmp_path):
    path = str(tmp_path / 'file.bin')
    original = {'number': 5, 'data': 'hello'}
    with BlockManifest(path) as bm:
        bm.set(original)
    # Original dict should be unchanged
    assert original['number'] == 5
