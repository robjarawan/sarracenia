import pytest
import types
import sarracenia
import sarracenia.config
from sarracenia.flowcb.shiftdir2baseurl import ShiftDir2baseUrl


def make_options():
    options = sarracenia.config.default_config()
    options.component = 'subscribe'
    options.config = 'test'
    options.logLevel = 'info'
    return options


def make_message(baseUrl='https://server', relPath='a/b/c/file.txt', subtopic=None):
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000'
    m['baseUrl'] = baseUrl
    m['relPath'] = relPath
    if subtopic is None:
        subtopic = relPath.split('/')[:-1]
    m['subtopic'] = subtopic
    return m


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def test_init_default():
    sd = ShiftDir2baseUrl(make_options())
    assert sd.o.shiftDir2baseUrl == 1


def test_shift_1_dir():
    sd = ShiftDir2baseUrl(make_options())
    sd.o.shiftDir2baseUrl = 1
    msg = make_message(baseUrl='https://host', relPath='a/b/c/file.txt', subtopic=['a', 'b', 'c'])
    wl = make_worklist()
    wl.ok = [msg]
    sd.after_work(wl)
    assert msg['baseUrl'] == 'https://host/a'
    assert msg['relPath'] == 'b/c/file.txt'
    assert msg['subtopic'] == ['b', 'c']


def test_shift_2_dirs():
    sd = ShiftDir2baseUrl(make_options())
    sd.o.shiftDir2baseUrl = 2
    msg = make_message(baseUrl='https://host', relPath='a/b/c/file.txt', subtopic=['a', 'b', 'c'])
    wl = make_worklist()
    wl.ok = [msg]
    sd.after_work(wl)
    assert msg['baseUrl'] == 'https://host/a/b'
    assert msg['relPath'] == 'c/file.txt'
    assert msg['subtopic'] == ['c']


def test_shift_3_dirs():
    sd = ShiftDir2baseUrl(make_options())
    sd.o.shiftDir2baseUrl = 3
    msg = make_message(baseUrl='https://host', relPath='a/b/c/file.txt', subtopic=['a', 'b', 'c'])
    wl = make_worklist()
    wl.ok = [msg]
    sd.after_work(wl)
    assert msg['baseUrl'] == 'https://host/a/b/c'
    assert msg['relPath'] == 'file.txt'
    assert msg['subtopic'] == []


def test_shift_preserves_existing_path():
    sd = ShiftDir2baseUrl(make_options())
    sd.o.shiftDir2baseUrl = 1
    msg = make_message(baseUrl='https://host/existing', relPath='x/y/file.txt', subtopic=['x', 'y'])
    wl = make_worklist()
    wl.ok = [msg]
    sd.after_work(wl)
    assert msg['baseUrl'] == 'https://host/existing/x'
    assert msg['relPath'] == 'y/file.txt'


def test_shift_empty_worklist():
    sd = ShiftDir2baseUrl(make_options())
    wl = make_worklist()
    sd.after_work(wl)


def test_shift_multiple_messages():
    sd = ShiftDir2baseUrl(make_options())
    sd.o.shiftDir2baseUrl = 1
    m1 = make_message(baseUrl='https://host', relPath='a/file1.txt', subtopic=['a'])
    m2 = make_message(baseUrl='https://host', relPath='b/file2.txt', subtopic=['b'])
    wl = make_worklist()
    wl.ok = [m1, m2]
    sd.after_work(wl)
    assert m1['baseUrl'] == 'https://host/a'
    assert m2['baseUrl'] == 'https://host/b'
    assert m1['relPath'] == 'file1.txt'
    assert m2['relPath'] == 'file2.txt'
