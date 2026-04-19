import pytest
import types
import os
import tempfile
from unittest.mock import patch, MagicMock

from sarracenia.flowcb.filter.fdelay import Fdelay
from sarracenia import Message as SR3Message, timeflt2str, nowflt
import sarracenia.config


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_message(pub_age=0, new_dir='/tmp/test', new_file='file.txt', remove=False):
    """Create a message with pubTime set to `pub_age` seconds ago."""
    m = SR3Message()
    m['pubTime'] = timeflt2str(nowflt() - pub_age)
    m['new_dir'] = new_dir
    m['new_file'] = new_file
    m['relPath'] = f'{new_dir}/{new_file}'
    if remove:
        m['fileOp'] = {'remove': True}
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
    fdelay = Fdelay(options)
    assert hasattr(fdelay.o, 'fdelay')


def test_remove_message_is_rejected():
    """Messages with fileOp 'remove' should go to rejected."""
    fdelay = Fdelay(make_options())
    wl = make_worklist()
    wl.incoming = [make_message(pub_age=200, remove=True)]
    fdelay.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 1


def test_young_message_goes_to_failed():
    """Messages younger than fdelay should go to failed."""
    options = make_options()
    fdelay = Fdelay(options)
    fdelay.o.fdelay = 120  # 120 seconds

    wl = make_worklist()
    wl.incoming = [make_message(pub_age=10)]  # only 10 seconds old
    fdelay.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(wl.failed) == 1


def test_old_message_with_existing_file_passes(tmp_path):
    """Messages older than fdelay with existing files should pass through."""
    options = make_options()
    fdelay = Fdelay(options)
    fdelay.o.fdelay = 5  # 5 seconds

    # Create a temp file that we can reference
    test_file = tmp_path / "testfile.txt"
    test_file.write_text("content")
    # Make file's mtime old enough
    old_time = nowflt() - 60
    os.utime(str(test_file), (old_time, old_time))

    msg = make_message(pub_age=60)
    msg['new_dir'] = '/cfr/' + str(tmp_path)
    msg['new_file'] = 'testfile.txt'
    msg['relPath'] = str(test_file)

    wl = make_worklist()
    wl.incoming = [msg]

    # The fdelay checks /cfr/ path prefix
    with patch('os.path.exists', return_value=True), \
         patch('os.path.getmtime', return_value=old_time):
        fdelay.after_accept(wl)

    assert len(wl.incoming) == 1


def test_file_not_found_goes_to_failed():
    """When the file doesn't exist, message goes to failed."""
    options = make_options()
    fdelay = Fdelay(options)
    fdelay.o.fdelay = 5

    msg = make_message(pub_age=60, new_dir='/nonexistent')
    wl = make_worklist()
    wl.incoming = [msg]
    fdelay.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(wl.failed) == 1


def test_file_too_young_goes_to_failed():
    """When the file exists but is too young, message goes to failed."""
    options = make_options()
    fdelay = Fdelay(options)
    fdelay.o.fdelay = 120

    msg = make_message(pub_age=200)
    wl = make_worklist()
    wl.incoming = [msg]

    with patch('os.path.exists', return_value=True), \
         patch('os.path.getmtime', return_value=nowflt() - 10):
        fdelay.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.failed) == 1


def test_empty_worklist_no_error():
    """Empty worklist should be handled gracefully."""
    fdelay = Fdelay(make_options())
    wl = make_worklist()
    fdelay.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(wl.failed) == 0


def test_mixed_messages():
    """A mix of old/young/remove messages are sorted properly."""
    options = make_options()
    fdelay = Fdelay(options)
    fdelay.o.fdelay = 30

    msg_remove = make_message(pub_age=100, remove=True)
    msg_young = make_message(pub_age=5)
    msg_no_file = make_message(pub_age=100, new_dir='/nonexistent')

    wl = make_worklist()
    wl.incoming = [msg_remove, msg_young, msg_no_file]
    fdelay.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 1  # remove message
    assert len(wl.failed) == 2   # young + no file