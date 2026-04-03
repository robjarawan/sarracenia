import pytest
import types
import os
from unittest.mock import MagicMock

from sarracenia.flowcb.accept.delete import Delete
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
    deletecb = Delete(options)
    assert deletecb is not None


def test_after_accept_deletes_files(tmp_path):
    cfr_dir = tmp_path / 'cfr'
    cfile_dir = tmp_path / 'cfile'
    cfr_dir.mkdir()
    cfile_dir.mkdir()

    cfr_file = cfr_dir / 'file.txt'
    cfile_file = cfile_dir / 'file.txt'
    cfr_file.write_text('cfr content')
    cfile_file.write_text('cfile content')

    options = make_options()
    options.consumer = types.SimpleNamespace(sleep_now=0, sleep_min=0.1, msg_to_retry=MagicMock())
    deletecb = Delete(options)

    worklist = make_worklist()
    worklist.incoming = [make_message(str(tmp_path), 'cfr/file.txt')]
    deletecb.after_accept(worklist)

    assert len(worklist.incoming) == 1
    assert len(worklist.rejected) == 0
    assert not cfr_file.exists()
    assert not cfile_file.exists()


def test_after_accept_file_not_found(tmp_path):
    options = make_options()
    options.consumer = types.SimpleNamespace(sleep_now=0, sleep_min=0.1, msg_to_retry=MagicMock())
    deletecb = Delete(options)

    worklist = make_worklist()
    worklist.incoming = [make_message(str(tmp_path), 'cfr/file.txt')]
    deletecb.after_accept(worklist)

    assert len(worklist.incoming) == 0
    assert len(worklist.rejected) == 1
    assert options.consumer.sleep_now == options.consumer.sleep_min
    options.consumer.msg_to_retry.assert_called_once()


def test_after_accept_empty_worklist():
    options = make_options()
    options.consumer = types.SimpleNamespace(sleep_now=0, sleep_min=0.1, msg_to_retry=MagicMock())
    deletecb = Delete(options)

    worklist = make_worklist()
    deletecb.after_accept(worklist)

    assert len(worklist.incoming) == 0
    assert len(worklist.rejected) == 0
    options.consumer.msg_to_retry.assert_not_called()


def test_after_accept_mixed_success_and_failure(tmp_path):
    cfr_dir = tmp_path / 'cfr'
    cfile_dir = tmp_path / 'cfile'
    cfr_dir.mkdir()
    cfile_dir.mkdir()

    good_file = cfr_dir / 'good.txt'
    good_cfile = cfile_dir / 'good.txt'
    good_file.write_text('data')
    good_cfile.write_text('data')

    options = make_options()
    options.consumer = types.SimpleNamespace(sleep_now=0, sleep_min=0.1, msg_to_retry=MagicMock())
    deletecb = Delete(options)

    worklist = make_worklist()
    msg_good = make_message(str(tmp_path), 'cfr/good.txt')
    msg_bad = make_message(str(tmp_path), 'cfr/missing.txt')
    worklist.incoming = [msg_good, msg_bad]
    deletecb.after_accept(worklist)

    assert len(worklist.incoming) == 1
    assert worklist.incoming[0] is msg_good
    assert len(worklist.rejected) == 1
    assert worklist.rejected[0] is msg_bad
    assert not good_file.exists()
    assert not good_cfile.exists()
