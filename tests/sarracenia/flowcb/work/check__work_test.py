import pytest
import types
import os
import sarracenia
import sarracenia.config
import sarracenia.flowcb.work.check
from sarracenia.flowcb.work.check import Check


def _make_opts():
    opts = sarracenia.config.default_config()
    opts.post_baseUrl = 'http://example.com'
    opts.identity_method = 'sha512'
    return opts


def test_reset_metrics_sets_all_to_zero():
    opts = _make_opts()
    cb = Check(opts)
    cb.checksum_mismatches = 5
    cb.size_mismatches = 3
    cb.ok = 10
    cb.bad_content = 1
    cb.reset_metrics()
    assert cb.checksum_mismatches == 0
    assert cb.size_mismatches == 0
    assert cb.ok == 0
    assert cb.bad_content == 0


def test_on_start_resets_metrics():
    opts = _make_opts()
    cb = Check(opts)
    cb.ok = 99
    cb.on_start()
    assert cb.ok == 0


def test_content_check_default_returns_true():
    opts = _make_opts()
    cb = Check(opts)
    cb.reset_metrics()
    assert cb.content_check('/any/path') is True


def test_metrics_report_structure():
    opts = _make_opts()
    cb = Check(opts)
    cb.reset_metrics()
    r = cb.metricsReport()
    assert 'checked_ok' in r
    assert 'checked_size_mismatches' in r
    assert 'checked_checksum_mismatches' in r
    assert 'checked_bad_content' in r


def test_metrics_report_reflects_counts():
    opts = _make_opts()
    cb = Check(opts)
    cb.reset_metrics()
    cb.ok = 3
    cb.size_mismatches = 1
    r = cb.metricsReport()
    assert r['checked_ok'] == 3
    assert r['checked_size_mismatches'] == 1


def test_on_housekeeping_resets_metrics():
    opts = _make_opts()
    cb = Check(opts)
    cb.ok = 5
    cb.on_housekeeping()
    assert cb.ok == 0


def test_after_work_with_fileop_calls_unlink(tmp_path):
    """Messages with fileOp cause os.unlink to be called."""
    opts = _make_opts()
    cb = Check(opts)
    cb.reset_metrics()
    # Create a real temp file
    f = tmp_path / 'test.txt'
    f.write_text('content')
    m = sarracenia.Message()
    m['pubTime'] = '20240101T000000.000'
    m['baseUrl'] = 'http://example.com'
    m['relPath'] = 'data/test.txt'
    m['new_dir'] = str(tmp_path)
    m['new_file'] = 'test.txt'
    m['fileOp'] = {'directory': True}
    m['_deleteOnPost'] = set()
    wl = types.SimpleNamespace()
    wl.ok = [m]
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    cb.after_work(wl)
    # File was unlinked by after_work
    assert not f.exists()
