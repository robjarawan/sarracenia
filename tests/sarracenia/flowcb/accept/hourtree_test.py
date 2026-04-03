import pytest
import types, re

#useful for debugging tests
def pretty(*things, **named_things):
    import pprint
    for t in things:
        pprint.PrettyPrinter(indent=2, width=200).pprint(t)
    for k,v in named_things.items():
        print(str(k) + ":")
        pprint.PrettyPrinter(indent=2, width=200).pprint(v)

from sarracenia.flowcb.accept.hourtree import HourTree
from sarracenia import Message as SR3Message
import sarracenia.config

def make_message():
    m = SR3Message()
    m['new_file'] = '/foo/bar/NewFile.txt'
    m['new_dir'] = '/foo/bar'

    return m

def make_worklist():
    WorkList = types.SimpleNamespace()
    WorkList.ok = []
    WorkList.incoming = []
    WorkList.rejected = []
    WorkList.failed = []
    WorkList.directories_ok = []
    return WorkList

def test_after_accept():
    hourtree = HourTree(sarracenia.config.default_config())
    
    worklist = make_worklist()
    worklist.incoming = [make_message(), make_message()]

    hourtree.after_accept(worklist)
    assert len(worklist.incoming) == 2
    assert bool(re.match(r"/foo/bar/\d{2}", worklist.incoming[0]['new_dir'])) == True
    assert bool(re.match(r"/foo/bar/\d{2}/NewFile.txt", worklist.incoming[1]['new_file'])) == True


def test_after_accept_empty_worklist():
    """Empty worklist should not cause errors."""
    hourtree = HourTree(sarracenia.config.default_config())
    worklist = make_worklist()
    hourtree.after_accept(worklist)
    assert len(worklist.incoming) == 0


def test_after_accept_inserts_hour_into_nested_path():
    """Hour should be inserted into deeply nested file paths."""
    hourtree = HourTree(sarracenia.config.default_config())
    worklist = make_worklist()
    msg = make_message()
    msg['new_dir'] = '/data/archive'
    msg['new_file'] = '/data/archive/report.csv'
    worklist.incoming = [msg]
    hourtree.after_accept(worklist)
    assert bool(re.match(r"/data/archive/\d{2}", worklist.incoming[0]['new_dir']))
    assert bool(re.match(r"/data/archive/\d{2}/report.csv", worklist.incoming[0]['new_file']))