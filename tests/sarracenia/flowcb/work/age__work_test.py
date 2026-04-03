import pytest
import types
import sarracenia
import sarracenia.config
import sarracenia.flowcb.work.age
from sarracenia.flowcb.work.age import Age


def _make_opts():
    return sarracenia.config.default_config()


def _make_worklist(ok_msgs=None):
    wl = types.SimpleNamespace()
    wl.ok = ok_msgs or []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg(pubTime, mtime, timeCompleted, new_dir='/tmp', new_file='f.txt'):
    m = sarracenia.Message()
    m['pubTime'] = pubTime
    m['mtime'] = mtime
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'data/file.txt'
    m['new_dir'] = new_dir
    m['new_file'] = new_file
    m['report'] = {'timeCompleted': timeCompleted}
    m['_deleteOnPost'] = set()
    return m


def test_reset_metrics_initializes_all_to_zero():
    cb = Age(_make_opts())
    cb.reset_metrics()
    assert cb.metrics['ageTotal'] == 0
    assert cb.metrics['ageCount'] == 0
    assert cb.metrics['ageMax'] == 0
    assert cb.metrics['copyTotal'] == 0
    assert cb.metrics['copyMax'] == 0


def test_on_start_calls_reset_metrics():
    cb = Age(_make_opts())
    cb.metrics = {'ageCount': 99}  # dirty state
    cb.on_start()
    assert cb.metrics['ageCount'] == 0


def test_metrics_report_zero_count_returns_zero_means():
    cb = Age(_make_opts())
    cb.on_start()
    r = cb.metricsReport()
    assert r['ageMean'] == 0
    assert r['copyMean'] == 0
    assert r['copyCount'] == 0


def test_after_work_no_mtime_returns_none():
    cb = Age(_make_opts())
    cb.on_start()
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'f.txt'
    m['_deleteOnPost'] = set()
    wl = _make_worklist([m])
    result = cb.after_work(wl)
    assert result is None
    assert cb.metrics['ageCount'] == 0


def test_after_work_increments_count():
    cb = Age(_make_opts())
    cb.on_start()
    # pubTime < mtime < completed => age > 0, copy > 0
    msg = _make_msg(
        pubTime='20240101T000000.000',
        mtime='20240101T000001.000',
        timeCompleted='20240101T000010.000'
    )
    wl = _make_worklist([msg])
    cb.after_work(wl)
    assert cb.metrics['ageCount'] == 1


def test_after_work_computes_correct_age():
    cb = Age(_make_opts())
    cb.on_start()
    msg = _make_msg(
        pubTime='20240101T000000.000',
        mtime='20240101T000001.000',   # mtime is 1s after epoch start
        timeCompleted='20240101T000011.000'  # completed is 11s after epoch start
    )
    wl = _make_worklist([msg])
    cb.after_work(wl)
    # age = completed - mtime = 10 seconds
    assert abs(cb.metrics['ageTotal'] - 10.0) < 0.1


def test_after_work_tracks_max_values():
    cb = Age(_make_opts())
    cb.on_start()
    msg1 = _make_msg('20240101T000000.000', '20240101T000001.000', '20240101T000006.000')
    msg2 = _make_msg('20240101T000000.000', '20240101T000001.000', '20240101T000021.000')
    wl = _make_worklist([msg1, msg2])
    cb.after_work(wl)
    assert cb.metrics['ageMax'] >= 20.0  # second msg's age is 20s


def test_metrics_report_correct_mean_after_one_item():
    cb = Age(_make_opts())
    cb.on_start()
    msg = _make_msg('20240101T000000.000', '20240101T000000.000', '20240101T000010.000')
    wl = _make_worklist([msg])
    cb.after_work(wl)
    r = cb.metricsReport()
    assert r['ageCount'] == 1
    assert r['copyCount'] == 1
    assert r['ageMean'] == r['ageTotal']
