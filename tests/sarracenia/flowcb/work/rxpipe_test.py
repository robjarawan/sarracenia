import pytest
import types
import os

import sarracenia.config
from sarracenia.flowcb.work.rxpipe import Rxpipe
from sarracenia import Message as SR3Message


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def make_message(new_dir='/data/test', new_file='file.txt'):
    m = SR3Message()
    m['new_dir'] = new_dir
    m['new_file'] = new_file
    return m


def test___init__():
    options = make_options()
    rxpipe = Rxpipe(options)
    assert rxpipe is not None
    assert hasattr(options, 'rxpipe_name')


def test_on_start_opens_pipe(tmp_path):
    options = make_options()
    rxpipe = Rxpipe(options)
    pipe_path = str(tmp_path / 'test.pipe')
    options.rxpipe_name = pipe_path

    rxpipe.on_start()

    assert hasattr(rxpipe, 'rxpipe')
    assert not rxpipe.rxpipe.closed
    rxpipe.rxpipe.close()


def test_after_work_writes_filenames(tmp_path):
    options = make_options()
    rxpipe = Rxpipe(options)
    pipe_path = str(tmp_path / 'test.pipe')
    options.rxpipe_name = pipe_path
    rxpipe.on_start()

    worklist = make_worklist()
    msg = make_message('/data/test', 'file.txt')
    worklist.ok = [msg]

    rxpipe.after_work(worklist)
    rxpipe.rxpipe.close()

    content = open(pipe_path).read()
    expected = '/data/test' + os.sep + 'file.txt' + '\n'
    assert content == expected


def test_after_work_empty_worklist(tmp_path):
    options = make_options()
    rxpipe = Rxpipe(options)
    pipe_path = str(tmp_path / 'test.pipe')
    options.rxpipe_name = pipe_path
    rxpipe.on_start()

    worklist = make_worklist()
    worklist.ok = []

    result = rxpipe.after_work(worklist)
    assert result is None
    rxpipe.rxpipe.close()

    content = open(pipe_path).read()
    assert content == ''


def test_after_work_multiple_messages(tmp_path):
    options = make_options()
    rxpipe = Rxpipe(options)
    pipe_path = str(tmp_path / 'test.pipe')
    options.rxpipe_name = pipe_path
    rxpipe.on_start()

    worklist = make_worklist()
    msg1 = make_message('/data/dir1', 'a.txt')
    msg2 = make_message('/data/dir2', 'b.txt')
    msg3 = make_message('/data/dir3', 'c.txt')
    worklist.ok = [msg1, msg2, msg3]

    rxpipe.after_work(worklist)
    rxpipe.rxpipe.close()

    content = open(pipe_path).read()
    lines = content.strip().split('\n')
    assert len(lines) == 3
    assert lines[0] == '/data/dir1' + os.sep + 'a.txt'
    assert lines[1] == '/data/dir2' + os.sep + 'b.txt'
    assert lines[2] == '/data/dir3' + os.sep + 'c.txt'


def test_on_start_missing_rxpipe_name(caplog):
    """When rxpipe_name is not set and file_rxpipe_name is truthy,
    on_start should log an error and not open a pipe."""
    import logging
    options = make_options()
    rxpipe = Rxpipe(options)
    # Remove rxpipe_name set by add_option to trigger the error path
    delattr(options, 'rxpipe_name')
    options.file_rxpipe_name = '/some/pipe'

    with caplog.at_level(logging.ERROR):
        rxpipe.on_start()

    assert 'Missing rxpipe_name parameter' in caplog.text
    assert not hasattr(rxpipe, 'rxpipe')