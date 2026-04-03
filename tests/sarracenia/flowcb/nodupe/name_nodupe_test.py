import pytest
import types
import copy

from sarracenia.flowcb.nodupe.name import Name
from sarracenia import Message as SR3Message


class Options:
    def __init__(self):
        self.retry_ttl = 0
        self.logLevel = "DEBUG"
        self.logFormat = ""
        self.queueName = "TEST_QUEUE_NAME"
        self.component = "sarra"
        self.config = "foobar.conf"
        self.pid_filename = "/tmp/sarracenia/name_nodupe_test/pid"
        self.housekeeping = float(39)

    def add_option(self, option, type, default=None):
        if not hasattr(self, option):
            setattr(self, option, default)


def make_message(relPath='dir1/dir2/filename.txt'):
    m = SR3Message()
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def test_after_accept_sets_nodupe_override():
    """Name should extract just the filename from relPath for dedup."""
    name = Name(Options())
    wl = make_worklist()
    msg = make_message('some/deep/path/data.csv')
    wl.incoming = [msg]
    name.after_accept(wl)
    assert msg['nodupe_override']['path'] == 'data.csv'


def test_after_accept_adds_deleteOnPost():
    """nodupe_override should be added to _deleteOnPost."""
    name = Name(Options())
    wl = make_worklist()
    msg = make_message('a/b/c.txt')
    wl.incoming = [msg]
    name.after_accept(wl)
    assert 'nodupe_override' in msg['_deleteOnPost']


def test_after_accept_preserves_existing_nodupe_override():
    """If nodupe_override already exists, it should still set the path."""
    name = Name(Options())
    wl = make_worklist()
    msg = make_message('x/y/z.dat')
    msg['nodupe_override'] = {'key': 'existing_key'}
    wl.incoming = [msg]
    name.after_accept(wl)
    assert msg['nodupe_override']['path'] == 'z.dat'
    assert msg['nodupe_override']['key'] == 'existing_key'


def test_after_accept_root_level_file():
    """A relPath with no directory separators should just use the filename."""
    name = Name(Options())
    wl = make_worklist()
    msg = make_message('justfile.txt')
    wl.incoming = [msg]
    name.after_accept(wl)
    assert msg['nodupe_override']['path'] == 'justfile.txt'


def test_after_accept_multiple_messages():
    """Multiple messages should each get their own nodupe_override."""
    name = Name(Options())
    wl = make_worklist()
    msg1 = make_message('dir1/file1.txt')
    msg2 = make_message('dir2/file2.txt')
    wl.incoming = [msg1, msg2]
    name.after_accept(wl)
    assert msg1['nodupe_override']['path'] == 'file1.txt'
    assert msg2['nodupe_override']['path'] == 'file2.txt'
