import pytest
from tests.conftest import *

import types, re, time

from sarracenia.flowcb.accept.dateappend import Dateappend
from sarracenia import Message as SR3Message
import sarracenia.config

def make_message():
    m = SR3Message()
    m["new_file"] = './SK/s0000684_f.xml'

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
    dateappend = Dateappend(sarracenia.config.default_config())
    
    worklist = make_worklist()
    worklist.incoming = [make_message(), make_message()]

    dateappend.after_accept(worklist)

    assert len(worklist.incoming) == 2
    assert bool(re.match(r'./SK/s0000684_f.xml_\d{12}', worklist.incoming[0]['new_file'])) == True
    assert bool(re.match(r'./SK/s0000684_f.xml_\d{12}', worklist.incoming[1]['new_file'])) == True


def test_after_accept_empty_worklist():
    """Empty worklist should not cause errors."""
    dateappend = Dateappend(sarracenia.config.default_config())
    worklist = make_worklist()
    dateappend.after_accept(worklist)
    assert len(worklist.incoming) == 0


def test_after_accept_retry_appends_again():
    """On retry, a second timestamp is appended to an already-suffixed filename."""
    dateappend = Dateappend(sarracenia.config.default_config())

    worklist = make_worklist()
    msg = make_message()
    # Simulate an already-dated filename from a previous pass
    msg['new_file'] = './SK/s0000684_f.xml_20221022080652'
    worklist.incoming = [msg]

    dateappend.after_accept(worklist)

    # Should now have two date suffixes
    assert bool(re.match(r'./SK/s0000684_f.xml_20221022080652_\d{14}', worklist.incoming[0]['new_file'])) == True


def test_after_accept_does_not_duplicate_current_timestamp():
    """If the filename already ends with the current timestamp, no duplication."""
    dateappend = Dateappend(sarracenia.config.default_config())

    fixed_time = time.strftime('_%Y%m%d%H%M%S', time.localtime())

    worklist = make_worklist()
    msg = make_message()
    msg['new_file'] = './SK/s0000684_f.xml' + fixed_time
    worklist.incoming = [msg]

    dateappend.after_accept(worklist)

    # Should NOT re-append the same timestamp
    assert worklist.incoming[0]['new_file'] == './SK/s0000684_f.xml' + fixed_time