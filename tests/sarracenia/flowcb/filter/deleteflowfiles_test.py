import pytest
import types
import os
import tempfile
from unittest.mock import patch

from sarracenia.flowcb.filter.deleteflowfiles import DeleteFlowFiles
from sarracenia import Message as SR3Message
import sarracenia.config


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_message(new_dir, new_file):
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
    d = DeleteFlowFiles(options)
    assert d is not None


def test_deletes_both_cfr_and_cfile(tmp_path):
    """Should delete both cfr and cfile copies, move message to ok."""
    cfr_dir = tmp_path / "cfr"
    cfile_dir = tmp_path / "cfile"
    cfr_dir.mkdir()
    cfile_dir.mkdir()

    cfr_file = cfr_dir / "test.txt"
    cfile_file = cfile_dir / "test.txt"
    cfr_file.write_text("data")
    cfile_file.write_text("data")

    msg = make_message(str(cfr_dir), 'test.txt')
    wl = make_worklist()
    wl.incoming = [msg]

    d = DeleteFlowFiles(make_options())
    d.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.ok) == 1
    assert not cfr_file.exists()
    assert not cfile_file.exists()


def test_failure_when_files_missing(tmp_path):
    """Should move to failed when files don't exist."""
    msg = make_message(str(tmp_path / "nonexistent"), 'test.txt')
    wl = make_worklist()
    wl.incoming = [msg]

    d = DeleteFlowFiles(make_options())
    d.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.failed) == 1


def test_clears_incoming_after_processing():
    """worklist.incoming should always be empty after after_accept."""
    d = DeleteFlowFiles(make_options())
    wl = make_worklist()
    wl.incoming = [make_message('/fake/cfr', 'file.txt')]
    d.after_accept(wl)
    assert wl.incoming == []


def test_empty_worklist():
    """Empty incoming should work without error."""
    d = DeleteFlowFiles(make_options())
    wl = make_worklist()
    d.after_accept(wl)
    assert wl.incoming == []
    assert wl.ok == []
    assert wl.failed == []