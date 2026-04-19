import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.housekeeping.resources
from sarracenia.flowcb.housekeeping.resources import Resources


def _make_opts():
    return sarracenia.config.default_config()


def _make_worklist(ok_msgs=None, incoming_msgs=None):
    wl = types.SimpleNamespace()
    wl.ok = ok_msgs or []
    wl.incoming = incoming_msgs or []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg():
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'file.txt'
    m['_deleteOnPost'] = set()
    return m


def test_init_sets_transfer_count_to_zero():
    cb = Resources(_make_opts())
    assert cb.transferCount == 0


def test_init_sets_msg_count_to_zero():
    cb = Resources(_make_opts())
    assert cb.msgCount == 0


def test_after_work_increments_transfer_count():
    cb = Resources(_make_opts())
    wl = _make_worklist(ok_msgs=[_make_msg(), _make_msg()])
    cb.after_work(wl)
    assert cb.transferCount == 2


def test_after_accept_increments_msg_count():
    cb = Resources(_make_opts())
    wl = _make_worklist(incoming_msgs=[_make_msg(), _make_msg(), _make_msg()])
    cb.after_accept(wl)
    assert cb.msgCount == 3


def test_after_work_empty_worklist_no_change():
    cb = Resources(_make_opts())
    wl = _make_worklist(ok_msgs=[])
    cb.after_work(wl)
    assert cb.transferCount == 0


def test_after_accept_empty_worklist_no_change():
    cb = Resources(_make_opts())
    wl = _make_worklist(incoming_msgs=[])
    cb.after_accept(wl)
    assert cb.msgCount == 0


def test_counts_accumulate_across_calls():
    cb = Resources(_make_opts())
    wl1 = _make_worklist(ok_msgs=[_make_msg()])
    wl2 = _make_worklist(ok_msgs=[_make_msg(), _make_msg()])
    cb.after_work(wl1)
    cb.after_work(wl2)
    assert cb.transferCount == 3


def test_memory_max_option_default_is_zero():
    opts = _make_opts()
    cb = Resources(opts)
    assert cb.o.MemoryMax == 0
