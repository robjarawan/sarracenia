import pytest
import types
import sarracenia
import sarracenia.config
from sarracenia.flowcb.mdelaylatest import MDelayLatest
from sarracenia import nowflt, timeflt2str, timestr2flt


def make_options():
    options = sarracenia.config.default_config()
    options.component = 'flow'
    options.config = 'test'
    options.logLevel = 'info'
    return options


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def make_message(relPath='a/b/file.txt', pub_age=0, fileOp=None):
    msg = sarracenia.Message()
    msg['pubTime'] = timeflt2str(nowflt() - pub_age)
    msg['baseUrl'] = 'https://example.com'
    msg['relPath'] = relPath
    if fileOp:
        msg['fileOp'] = fileOp
    return msg


def test_init_defaults():
    md = MDelayLatest(make_options())
    assert md.ok_delay == []
    assert md.suppressions == 0
    assert md.stop_requested is False
    assert md.o.mdelay == 30


def test_young_message_delayed():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    wl = make_worklist()
    msg = make_message(pub_age=5)
    wl.incoming = [msg]
    md.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(md.ok_delay) == 1


def test_old_message_passes_through():
    md = MDelayLatest(make_options())
    md.o.mdelay = 5
    wl = make_worklist()
    msg = make_message(pub_age=60)
    wl.incoming = [msg]
    md.after_accept(wl)
    assert len(wl.incoming) == 1
    assert wl.incoming[0] is msg
    assert len(md.ok_delay) == 0


def test_duplicate_suppressed():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    wl1 = make_worklist()
    msg1 = make_message(relPath='data/file.csv', pub_age=5)
    wl1.incoming = [msg1]
    md.after_accept(wl1)
    assert len(md.ok_delay) == 1

    wl2 = make_worklist()
    msg2 = make_message(relPath='data/file.csv', pub_age=3)
    wl2.incoming = [msg2]
    md.after_accept(wl2)
    assert len(md.ok_delay) == 1
    assert md.ok_delay[0] is msg2
    assert md.suppressions == 1
    assert len(wl2.rejected) == 1


def test_fileOp_forces_immediate_publish():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    wl1 = make_worklist()
    msg1 = make_message(relPath='data/file.csv', pub_age=5)
    wl1.incoming = [msg1]
    md.after_accept(wl1)
    assert len(md.ok_delay) == 1

    wl2 = make_worklist()
    msg2 = make_message(relPath='data/file.csv', pub_age=3, fileOp={'remove': ''})
    wl2.incoming = [msg2]
    md.after_accept(wl2)
    assert msg1 in wl2.incoming
    assert msg2 in md.ok_delay


def test_delayed_messages_age_out():
    md = MDelayLatest(make_options())
    md.o.mdelay = 1
    msg = make_message(relPath='data/file.csv', pub_age=5)
    md.ok_delay = [msg]
    wl = make_worklist()
    md.after_accept(wl)
    assert msg in wl.incoming
    assert len(md.ok_delay) == 0


def test_stop_requested_drains_queue():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    msg1 = make_message(relPath='a.txt', pub_age=5)
    msg2 = make_message(relPath='b.txt', pub_age=3)
    md.ok_delay = [msg1, msg2]
    md.stop_requested = True
    wl = make_worklist()
    md.after_accept(wl)
    assert msg1 in wl.incoming
    assert msg2 in wl.incoming
    assert len(md.ok_delay) == 0


def test_empty_worklist():
    md = MDelayLatest(make_options())
    wl = make_worklist()
    md.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(md.ok_delay) == 0


def test_multiple_different_files():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    wl = make_worklist()
    msg1 = make_message(relPath='a.txt', pub_age=5)
    msg2 = make_message(relPath='b.txt', pub_age=5)
    wl.incoming = [msg1, msg2]
    md.after_accept(wl)
    assert len(wl.incoming) == 0
    assert len(md.ok_delay) == 2


def test_housekeeping_resets_suppressions():
    md = MDelayLatest(make_options())
    md.suppressions = 5
    md.ok_delay = [make_message()]
    md.on_housekeeping()
    assert md.suppressions == 0


def test_fileOp_on_delayed_forces_publish():
    md = MDelayLatest(make_options())
    md.o.mdelay = 120
    wl1 = make_worklist()
    msg1 = make_message(relPath='data/file.csv', pub_age=5, fileOp={'rename': '/old'})
    wl1.incoming = [msg1]
    md.after_accept(wl1)
    assert len(md.ok_delay) == 1

    wl2 = make_worklist()
    msg2 = make_message(relPath='data/file.csv', pub_age=3)
    wl2.incoming = [msg2]
    md.after_accept(wl2)
    assert msg1 in wl2.incoming
