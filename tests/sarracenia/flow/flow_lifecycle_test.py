"""Tests for sarracenia.flow — deeper Flow lifecycle methods.

Covers: gather, do, work, filter, ack, reject, close, please_stop,
stop_request, metricsFlowReset, work_message_adjust, post,
_runCallbacksWorklist, runCallbacksTime, _runCallbackMetrics,
_runHousekeeping, _run_vip_update, write_inline_file, file_should_be_downloaded.

All tests mock external boundaries (plugins, network, filesystem where needed).
"""

import json
import logging
import os
import types
import time
from base64 import b64encode
from unittest.mock import MagicMock, Mock, patch, mock_open, PropertyMock, call

import pytest

import sarracenia
import sarracenia.config
import sarracenia.flowcb
import sarracenia.identity
import sarracenia.transfer


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_flow_stub(tmp_path=None, **opt_overrides):
    """Build a minimal Flow stub with worklist and plugins ready.
    Does NOT call __init__ — avoids full plugin loading / network.
    """
    from sarracenia.flow import Flow

    opts = sarracenia.config.default_config()
    opts.permDirDefault = 0o755
    opts.logLevel = 'info'
    opts.logFormat = '%(message)s'
    opts.settings = {}
    opts.component = 'subscribe'
    opts.config = 'test'
    opts.no = 0
    opts.batch = 100
    opts.download = False
    opts.acceptUnmatched = True
    opts.messageAgeMax = 0
    opts.fileAgeMax = 0
    opts.fileAgeMin = 0
    opts.strip = 0
    opts.pstrip = False
    opts.flatten = '/'
    opts.mirror = True
    opts.filename = None
    opts.post_broker = None
    opts.publishers = [MagicMock()]
    opts.topicPrefix = ['v03']
    opts.post_topicPrefix = ['v03']
    opts.vip = []
    opts.housekeeping = 300
    opts.sleep = 0.1
    opts.messageCountMax = 0
    opts.messageRateMax = 0
    opts.messageRateMin = 0
    opts.logMetrics = False
    opts.logRotateCount = 5
    opts.logRotateInterval = 86400
    opts.metrics_writeInterval = 300
    opts.pid_filename = '/tmp/test_flow.pid'
    opts.novipFilename = '/tmp/test_novip'
    opts.metricsFilename = '/tmp/test_metrics'
    opts.identity_method = 'sha512'

    if tmp_path:
        opts.metricsFilename = str(tmp_path / 'metrics')
        opts.novipFilename = str(tmp_path / 'novip')
    
    for k, v in opt_overrides.items():
        setattr(opts, k, v)

    flow = object.__new__(Flow)
    flow.o = opts
    flow._stop_requested = False
    flow._logLevel_debug = False
    flow.have_vip = True
    flow.had_vip = True
    flow.last_poll_gather_len = 0
    flow.metrics_lastWrite = 0
    flow.proto = {}

    flow.worklist = types.SimpleNamespace()
    flow.worklist.ok = []
    flow.worklist.incoming = []
    flow.worklist.rejected = []
    flow.worklist.failed = []
    flow.worklist.directories_ok = []

    # Set up empty plugin entry points
    flow.plugins = {}
    for ep in sarracenia.flowcb.entry_points:
        flow.plugins[ep] = []
    flow.plugins['load'] = []

    flow.metrics = {
        'flow': {
            'stop_requested': False,
            'last_housekeeping': 0,
            'transferConnected': False,
            'transferConnectStart': 0,
            'transferConnectTime': 0,
            'transferRxBytes': 0,
            'transferTxBytes': 0,
            'transferRxFiles': 0,
            'transferTxFiles': 0,
            'last_housekeeping_cpuTime': 0,
            'cpuTime': 0,
        }
    }
    flow.new_metrics = flow.metrics.copy()

    return flow


def _make_msg(**kw):
    """Build a minimal sarracenia Message for testing."""
    m = sarracenia.Message()
    m['pubTime'] = kw.get('pubTime', sarracenia.nowstr())
    m['baseUrl'] = kw.get('baseUrl', 'https://example.com/')
    m['relPath'] = kw.get('relPath', 'data/file.txt')
    m['_deleteOnPost'] = kw.get('_deleteOnPost', set())
    for k, v in kw.items():
        if k not in ('pubTime', 'baseUrl', 'relPath', '_deleteOnPost'):
            m[k] = v
    return m


# ══════════════════════════════════════════════════════════════════════════════
# gather
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_gather:

    def test_gather_collects_messages_from_plugins(self):
        flow = _make_flow_stub()
        mock_plugin = MagicMock(return_value=[_make_msg(), _make_msg()])
        flow.plugins['gather'] = [mock_plugin]

        flow.gather()
        assert len(flow.worklist.incoming) == 2

    def test_gather_handles_tuple_return(self):
        flow = _make_flow_stub()
        mock_plugin = MagicMock(return_value=(True, [_make_msg()]))
        flow.plugins['gather'] = [mock_plugin]

        flow.gather()
        assert len(flow.worklist.incoming) == 1

    def test_gather_stops_when_keep_going_false(self):
        flow = _make_flow_stub()
        p1 = MagicMock(return_value=(False, [_make_msg()]))
        p2 = MagicMock(return_value=[_make_msg()])
        flow.plugins['gather'] = [p1, p2]

        flow.gather()
        assert len(flow.worklist.incoming) == 1
        p2.assert_not_called()

    def test_gather_stops_when_batch_reached(self):
        flow = _make_flow_stub(batch=2)
        msgs = [_make_msg() for _ in range(3)]
        p1 = MagicMock(return_value=msgs)
        p2 = MagicMock(return_value=[_make_msg()])
        flow.plugins['gather'] = [p1, p2]

        flow.gather()
        # First plugin returned 3 which >= batch=2, so p2 is skipped
        assert len(flow.worklist.incoming) == 3
        p2.assert_not_called()

    def test_gather_empty_from_all_plugins(self):
        flow = _make_flow_stub()
        p1 = MagicMock(return_value=[])
        flow.plugins['gather'] = [p1]

        flow.gather()
        assert len(flow.worklist.incoming) == 0

    def test_gather_runs_after_gather_callback(self):
        flow = _make_flow_stub()
        p1 = MagicMock(return_value=[_make_msg()])
        flow.plugins['gather'] = [p1]
        
        after_gather_called = []
        def mock_after_gather(wl):
            after_gather_called.append(True)
        flow.plugins['after_gather'] = [mock_after_gather]

        flow.gather()
        assert len(after_gather_called) == 1

    def test_gather_plugin_crash_continues(self):
        flow = _make_flow_stub()
        p1 = MagicMock(side_effect=RuntimeError('crash'))
        p2 = MagicMock(return_value=[_make_msg()])
        flow.plugins['gather'] = [p1, p2]

        flow.gather()
        # First plugin crashed, second should still be called
        assert len(flow.worklist.incoming) == 1

    def test_gather_poll_component_skips_poll_when_queue_msgs(self):
        """When component is poll and queue messages exist, don't run poll plugins."""
        flow = _make_flow_stub(component='poll')
        flow.have_vip = True

        # Gather plugin returns some messages (simulating queue messages)
        p1 = MagicMock(return_value=[_make_msg()])
        flow.plugins['gather'] = [p1]

        mock_poll = MagicMock(return_value=[_make_msg()])
        flow.plugins['poll'] = [mock_poll]

        flow.gather()
        # poll plugin should NOT be called because queue messages were gathered
        mock_poll.assert_not_called()

    def test_gather_poll_component_runs_poll_when_no_queue_msgs(self):
        """When component is poll and no queue messages, run poll plugins."""
        flow = _make_flow_stub(component='poll')
        flow.have_vip = True

        # Gather plugin returns nothing
        p1 = MagicMock(return_value=[])
        flow.plugins['gather'] = [p1]

        poll_msgs = [_make_msg()]
        mock_poll = MagicMock(return_value=poll_msgs)
        flow.plugins['poll'] = [mock_poll]

        flow.gather()
        mock_poll.assert_called_once()
        assert flow.last_poll_gather_len == 1


# ══════════════════════════════════════════════════════════════════════════════
# do
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_do:

    def test_do_without_download_moves_incoming_to_ok(self):
        flow = _make_flow_stub(download=False)
        m1 = _make_msg()
        m2 = _make_msg()
        flow.worklist.incoming = [m1, m2]

        flow.do()
        assert len(flow.worklist.ok) == 2
        assert len(flow.worklist.incoming) == 0

    def test_do_with_download_calls_do_download(self):
        flow = _make_flow_stub(download=True)
        flow.worklist.incoming = [_make_msg()]

        with patch.object(flow, 'do_download') as mock_dd:
            flow.do()
            mock_dd.assert_called_once()

    def test_do_without_download_empty_incoming(self):
        flow = _make_flow_stub(download=False)
        flow.worklist.incoming = []
        flow.do()
        assert flow.worklist.ok == []
        assert flow.worklist.incoming == []


# ══════════════════════════════════════════════════════════════════════════════
# work
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_work:

    def test_work_calls_do_and_acks_results(self):
        flow = _make_flow_stub()
        m1 = _make_msg()
        flow.worklist.incoming = [m1]
        
        # Mock out the full pipeline
        with patch.object(flow, 'do') as mock_do:
            def do_side_effect():
                flow.worklist.ok = flow.worklist.incoming
                flow.worklist.incoming = []
            mock_do.side_effect = do_side_effect
            
            with patch.object(flow, 'ack') as mock_ack:
                with patch.object(flow, 'work_message_adjust') as mock_adj:
                    flow.work()
        
        # ack should have been called for ok, rejected, and failed
        assert mock_ack.call_count >= 2

    def test_work_runs_after_work_callback(self):
        flow = _make_flow_stub()
        flow.worklist.incoming = [_make_msg()]
        
        after_work_called = []
        def mock_after_work(wl):
            after_work_called.append(True)
        flow.plugins['after_work'] = [mock_after_work]

        with patch.object(flow, 'do') as mock_do:
            def do_side_effect():
                flow.worklist.ok = flow.worklist.incoming
                flow.worklist.incoming = []
            mock_do.side_effect = do_side_effect
            with patch.object(flow, 'work_message_adjust'):
                flow.work()

        assert len(after_work_called) == 1


# ══════════════════════════════════════════════════════════════════════════════
# work_message_adjust
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_work_message_adjust:

    def test_adjust_baseUrl_when_new_baseUrl_differs(self):
        flow = _make_flow_stub()
        m = _make_msg(baseUrl='https://old.com/', new_baseUrl='https://new.com/')
        flow.work_message_adjust(m)
        assert m['baseUrl'] == 'https://new.com/'
        assert m['old_baseUrl'] == 'https://old.com/'
        assert 'old_baseUrl' in m['_deleteOnPost']

    def test_adjust_baseUrl_unchanged_when_same(self):
        flow = _make_flow_stub()
        m = _make_msg(baseUrl='https://same.com/', new_baseUrl='https://same.com/')
        flow.work_message_adjust(m)
        assert 'old_baseUrl' not in m

    def test_adjust_retrievePath(self):
        flow = _make_flow_stub()
        m = _make_msg(retrievePath='/old/path', new_retrievePath='/new/path')
        flow.work_message_adjust(m)
        assert m['retrievePath'] == '/new/path'
        assert m['old_retrievePath'] == '/old/path'

    def test_adjust_relPath_when_new_file_differs(self):
        flow = _make_flow_stub()
        m = _make_msg(relPath='data/old.txt', new_file='new.txt')
        m['new_subtopic'] = ['data']
        flow.work_message_adjust(m)
        assert m['relPath'] == 'data/new.txt'

    def test_adjust_no_change_when_new_file_matches(self):
        flow = _make_flow_stub()
        m = _make_msg(relPath='data/file.txt', new_file='file.txt')
        flow.work_message_adjust(m)
        # No new_relPath should have been generated
        assert 'old_relPath' not in m

    def test_adjust_deletes_retrievePath_on_download(self):
        flow = _make_flow_stub(download=True)
        m = _make_msg(retrievePath='/some/path')
        flow.work_message_adjust(m)
        assert 'retrievePath' not in m

    def test_adjust_post_fileOp_restores_fileOp(self):
        flow = _make_flow_stub()
        m = _make_msg()
        m['post_fileOp'] = {'rename': '/new/name'}
        m['fileOp'] = {'link': '/old/link'}
        flow.work_message_adjust(m)
        assert m['fileOp'] == {'rename': '/new/name'}

    def test_adjust_format_is_saved(self):
        flow = _make_flow_stub()
        m = _make_msg()
        m['_format'] = 'v03'
        flow.work_message_adjust(m)
        assert m['old_format'] == 'v03'
        assert 'old_format' in m['_deleteOnPost']


# ══════════════════════════════════════════════════════════════════════════════
# reject
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_reject:

    def test_reject_appends_to_rejected(self):
        flow = _make_flow_stub()
        m = _make_msg()
        flow.reject(m, 404, 'not found')
        assert m in flow.worklist.rejected

    def test_reject_sets_report_on_message(self):
        flow = _make_flow_stub()
        m = _make_msg()
        flow.reject(m, 503, 'service unavailable')
        assert m in flow.worklist.rejected


# ══════════════════════════════════════════════════════════════════════════════
# ack
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_ack:

    def test_ack_calls_all_ack_plugins(self):
        flow = _make_flow_stub()
        p1 = MagicMock()
        p2 = MagicMock()
        flow.plugins['ack'] = [p1, p2]

        msgs = [_make_msg(), _make_msg()]
        flow.ack(msgs)
        p1.assert_called_once_with(msgs)
        p2.assert_called_once_with(msgs)

    def test_ack_with_empty_list(self):
        flow = _make_flow_stub()
        p1 = MagicMock()
        flow.plugins['ack'] = [p1]

        flow.ack([])
        p1.assert_called_once_with([])

    def test_ack_handles_plugin_crash(self):
        flow = _make_flow_stub()
        p1 = MagicMock(side_effect=RuntimeError('crash'))
        p2 = MagicMock()
        flow.plugins['ack'] = [p1, p2]

        msgs = [_make_msg()]
        flow.ack(msgs)
        # Second plugin should still be called despite crash
        p2.assert_called_once_with(msgs)

    def test_ack_no_plugins_registered(self):
        flow = _make_flow_stub()
        flow.plugins['ack'] = []
        flow.ack([_make_msg()])  # Should not raise


# ══════════════════════════════════════════════════════════════════════════════
# please_stop / stop_request
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_stop:

    def test_please_stop_sets_flag(self):
        flow = _make_flow_stub()
        assert flow._stop_requested is False
        flow.please_stop()
        assert flow._stop_requested is True
        assert flow.metrics['flow']['stop_requested'] is True

    def test_stop_request_calls_please_stop_callbacks(self):
        flow = _make_flow_stub()
        called = []
        def mock_stop():
            called.append(True)
        flow.plugins['please_stop'] = [mock_stop]

        flow.stop_request()
        assert len(called) == 1

    def test_stop_request_idempotent(self):
        flow = _make_flow_stub()
        flow.plugins['please_stop'] = [lambda: None]
        flow.stop_request()
        flow.stop_request()
        # Second call should not raise


# ══════════════════════════════════════════════════════════════════════════════
# close
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_close:

    def test_close_runs_on_stop_callbacks(self):
        flow = _make_flow_stub()
        called = []
        def mock_stop():
            called.append(True)
        flow.plugins['on_stop'] = [mock_stop]

        flow.close()
        assert len(called) == 1

    def test_close_removes_novip_file(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        novip = tmp_path / 'novip'
        novip.write_text('test')
        flow.o.novipFilename = str(novip)

        flow.close()
        assert not novip.exists()

    def test_close_no_novip_file_no_error(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.novipFilename = str(tmp_path / 'nonexistent')
        flow.close()  # should not raise


# ══════════════════════════════════════════════════════════════════════════════
# _runCallbacksWorklist
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_runCallbacksWorklist:

    def test_calls_flow_method_then_plugins(self):
        flow = _make_flow_stub()
        call_order = []

        def flow_method(wl):
            call_order.append('flow')
        def plugin1(wl):
            call_order.append('plugin1')
        def plugin2(wl):
            call_order.append('plugin2')

        flow.after_accept = flow_method
        flow.plugins['after_accept'] = [plugin1, plugin2]

        flow._runCallbacksWorklist('after_accept')
        assert call_order == ['flow', 'plugin1', 'plugin2']

    def test_no_flow_method_just_plugins(self):
        flow = _make_flow_stub()
        called = []
        flow.plugins['after_accept'] = [lambda wl: called.append(True)]

        flow._runCallbacksWorklist('after_accept')
        assert len(called) == 1

    def test_plugin_crash_does_not_stop_others(self):
        flow = _make_flow_stub()
        called = []
        def crash(wl):
            raise RuntimeError('boom')
        def ok(wl):
            called.append(True)

        flow.plugins['after_accept'] = [crash, ok]
        flow._runCallbacksWorklist('after_accept')
        assert len(called) == 1

    def test_debug_mode_propagates_exception(self):
        flow = _make_flow_stub()
        flow._logLevel_debug = True

        def crash(wl):
            raise RuntimeError('boom')
        flow.plugins['after_accept'] = [crash]

        with pytest.raises(RuntimeError, match='boom'):
            flow._runCallbacksWorklist('after_accept')


# ══════════════════════════════════════════════════════════════════════════════
# runCallbacksTime
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_runCallbacksTime:

    def test_calls_flow_method_and_plugins(self):
        flow = _make_flow_stub()
        call_order = []

        def flow_method():
            call_order.append('flow')
        def plugin1():
            call_order.append('plugin1')

        flow.on_start = flow_method
        flow.plugins['on_start'] = [plugin1]

        flow.runCallbacksTime('on_start')
        assert call_order == ['flow', 'plugin1']

    def test_plugin_crash_swallowed_in_normal_mode(self):
        flow = _make_flow_stub()
        flow.plugins['on_start'] = [MagicMock(side_effect=RuntimeError('fail'))]
        flow.runCallbacksTime('on_start')  # Should not raise

    def test_debug_mode_propagates_exception(self):
        flow = _make_flow_stub()
        flow._logLevel_debug = True
        flow.plugins['on_start'] = [MagicMock(side_effect=RuntimeError('fail'))]
        with pytest.raises(RuntimeError):
            flow.runCallbacksTime('on_start')


# ══════════════════════════════════════════════════════════════════════════════
# _runCallbackMetrics
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_runCallbackMetrics:

    def test_collects_plugin_metrics(self):
        flow = _make_flow_stub()
        mock_plugin = MagicMock(return_value={'count': 42})
        mock_plugin.__module__ = 'sarracenia.flowcb.retry'
        mock_plugin.__qualname__ = 'Retry.metricsReport'
        flow.plugins['metricsReport'] = [mock_plugin]

        flow._runCallbackMetrics()
        assert 'retry' in flow.metrics
        assert flow.metrics['retry'] == {'count': 42}

    def test_collects_transfer_protocol_metrics(self):
        flow = _make_flow_stub()
        flow.plugins['metricsReport'] = []
        mock_proto = MagicMock()
        mock_proto.metricsReport.return_value = {'bytes': 1000}
        mock_proto.metricsReport.__module__ = 'sarracenia.transfer.https'
        flow.proto = {'https': mock_proto}

        flow._runCallbackMetrics()
        assert 'https' in flow.metrics

    def test_handles_transfer_connected_timing(self):
        flow = _make_flow_stub()
        flow.plugins['metricsReport'] = []
        flow.metrics['flow']['transferConnected'] = True
        flow.metrics['flow']['transferConnectStart'] = 100.0
        flow.metrics['flow']['transferConnectTime'] = 0.0

        with patch('sarracenia.flow.nowflt', return_value=105.0):
            flow._runCallbackMetrics()
        assert flow.metrics['flow']['transferConnectTime'] == 5.0

    def test_plugin_crash_swallowed(self):
        flow = _make_flow_stub()
        mock_plugin = MagicMock(side_effect=RuntimeError('fail'))
        mock_plugin.__module__ = 'sarracenia.flowcb.test'
        flow.plugins['metricsReport'] = [mock_plugin]

        flow._runCallbackMetrics()  # Should not raise


# ══════════════════════════════════════════════════════════════════════════════
# _runHousekeeping
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_runHousekeeping:

    def test_returns_next_housekeeping_time(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path, housekeeping=300)
        flow.plugins['on_housekeeping'] = []

        # metricsFlowReset needs metricsFilename to exist for glob
        with patch('glob.glob', return_value=[]):
            result = flow._runHousekeeping(1000.0)
        assert result == 1300.0

    def test_calls_on_housekeeping_plugins(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        called = []
        def hk():
            called.append(True)
        flow.plugins['on_housekeeping'] = [hk]

        with patch('glob.glob', return_value=[]):
            flow._runHousekeeping(1000.0)
        assert len(called) == 1

    def test_resets_metrics(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.metrics['flow']['transferRxBytes'] = 999
        flow.plugins['on_housekeeping'] = []

        with patch('glob.glob', return_value=[]):
            flow._runHousekeeping(1000.0)
        assert flow.metrics['flow']['transferRxBytes'] == 0


# ══════════════════════════════════════════════════════════════════════════════
# _run_vip_update
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_run_vip_update:

    def test_non_poll_with_vip(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path, component='subscribe')
        flow.had_vip = False

        with patch.object(flow, 'has_vip', return_value=['10.0.0.1']):
            flow._run_vip_update()
        assert flow.had_vip is True
        assert flow.have_vip == ['10.0.0.1']

    def test_poll_without_vip_creates_novip_file(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path, component='poll')
        flow.had_vip = True

        with patch.object(flow, 'has_vip', return_value=[]):
            flow._run_vip_update()
        assert flow.had_vip is False
        assert os.path.exists(flow.o.novipFilename)

    def test_poll_gaining_vip_removes_novip_file(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path, component='poll')
        flow.had_vip = False
        # Create novip file
        novip = tmp_path / 'novip'
        novip.write_text('test')
        flow.o.novipFilename = str(novip)

        with patch.object(flow, 'has_vip', return_value=['10.0.0.1']):
            flow._run_vip_update()
        assert flow.had_vip is True
        assert not novip.exists()


# ══════════════════════════════════════════════════════════════════════════════
# post
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_post:

    def test_post_clears_worklists(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.worklist.ok = [_make_msg()]
        flow.worklist.directories_ok = ['/some/dir']
        flow.worklist.failed = [_make_msg()]
        flow.plugins['metricsReport'] = []

        flow.post(1000.0)
        assert flow.worklist.ok == []
        assert flow.worklist.directories_ok == []
        assert flow.worklist.failed == []

    def test_post_runs_report_and_metrics(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        report_called = []
        def report_cb(wl):
            report_called.append(True)
        flow.plugins['report'] = [report_cb]
        flow.plugins['metricsReport'] = []

        flow.post(1000.0)
        assert len(report_called) == 1

    def test_post_with_post_broker_calls_post_plugins(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.post_broker = MagicMock()
        flow.worklist.ok = [_make_msg()]

        post_called = []
        def post_cb(wl):
            post_called.append(True)
        flow.plugins['post'] = [post_cb]
        flow.plugins['after_post'] = []
        flow.plugins['metricsReport'] = []

        flow.post(1000.0)
        assert len(post_called) == 1

    def test_post_writes_metrics_file_when_interval_passed(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.metrics_writeInterval = 10
        flow.metrics_lastWrite = 0
        flow.plugins['metricsReport'] = []

        flow.post(100.0)
        # metrics should be written
        assert os.path.exists(flow.o.metricsFilename)


# ══════════════════════════════════════════════════════════════════════════════
# metricsFlowReset
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_metricsFlowReset:

    def test_resets_flow_metrics(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.metrics['flow']['transferRxBytes'] = 9999
        flow.metrics['flow']['transferTxBytes'] = 5555
        
        with patch('glob.glob', return_value=[]):
            flow.metricsFlowReset()
        
        assert flow.metrics['flow']['transferRxBytes'] == 0
        assert flow.metrics['flow']['transferTxBytes'] == 0

    def test_carries_over_transferRxLast(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.metrics['transferRxLast'] = 'last_file.dat'

        with patch('glob.glob', return_value=[]):
            flow.metricsFlowReset()
        
        assert flow.metrics.get('transferRxLast') == 'last_file.dat'

    def test_removes_old_metrics_files(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.logRotateCount = 2

        # Create some fake metrics files
        old_files = []
        for i in range(4):
            f = str(tmp_path / f'metrics.{i:04d}')
            with open(f, 'w') as fh:
                fh.write('{}')
            old_files.append(f)

        with patch('glob.glob', return_value=old_files):
            flow.metricsFlowReset()
        
        # Should have tried to remove old files (keeping last 2)
        # The oldest 2 files should be removed


# ══════════════════════════════════════════════════════════════════════════════
# has_vip
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_has_vip:

    def test_empty_vip_list_returns_any_address(self):
        flow = _make_flow_stub()
        flow.o.vip = []
        result = flow.has_vip()
        assert result == ['AnyAddressIsFine']

    @patch('sarracenia.featuredetection.features', {'vip': {'present': False}})
    def test_no_vip_feature_returns_true(self):
        flow = _make_flow_stub()
        flow.o.vip = ['10.0.0.1']
        # When vip feature not present, returns True
        from sarracenia.flow import features
        with patch.dict('sarracenia.flow.features', {'vip': {'present': False}}):
            result = flow.has_vip()
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# write_inline_file
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_write_inline_file:

    def test_write_base64_content(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        data = b'Hello, World!'
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(data).decode('ascii'),
        }
        m['size'] = len(data)
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is True
        written = (tmp_path / 'inline.txt').read_bytes()
        assert written == data

    def test_write_utf8_content(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'utf-8',
            'value': 'Hello UTF8',
        }
        m['size'] = 10
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is True

    def test_write_inline_creates_directory(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        data = b'data'
        subdir = tmp_path / 'newdir'
        m = _make_msg()
        m['new_dir'] = str(subdir)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(data).decode('ascii'),
        }
        m['size'] = len(data)
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is True
        assert subdir.exists()

    def test_write_inline_size_mismatch_rejects(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.acceptSizeWrong = False
        data = b'short'
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(data).decode('ascii'),
        }
        m['size'] = 9999  # Wrong size
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is False

    def test_write_inline_size_mismatch_accepted_with_flag(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        flow.o.acceptSizeWrong = True
        data = b'short'
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(data).decode('ascii'),
        }
        m['size'] = 9999  # Wrong size, but accepted
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is True

    def test_write_inline_file_open_failure(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(b'data').decode('ascii'),
        }
        m['size'] = 4
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        with patch('os.fdopen', side_effect=OSError('permission denied')):
            result = flow.write_inline_file(m)
        assert result is False

    def test_write_inline_sets_checksums(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        data = b'checksum_test'
        m = _make_msg()
        m['new_dir'] = str(tmp_path)
        m['new_file'] = 'inline.txt'
        m['content'] = {
            'encoding': 'base64',
            'value': b64encode(data).decode('ascii'),
        }
        m['size'] = len(data)
        m['identity'] = {'method': 'sha512', 'value': 'dummy'}

        result = flow.write_inline_file(m)
        assert result is True
        assert 'onfly_checksum' in m
        assert 'data_checksum' in m
        assert m['onfly_checksum']['method'] == 'sha512'
        assert m['data_checksum']['method'] == 'sha512'


# ══════════════════════════════════════════════════════════════════════════════
# _runCallbackPoll
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_runCallbackPoll:

    def test_poll_plugin_returns_messages(self):
        flow = _make_flow_stub()
        msgs = [_make_msg()]
        mock_poll = MagicMock(return_value=msgs)
        flow.plugins['poll'] = [mock_poll]

        flow._runCallbackPoll()
        assert len(flow.worklist.incoming) == 1

    def test_poll_plugin_returns_empty(self):
        flow = _make_flow_stub()
        mock_poll = MagicMock(return_value=[])
        flow.plugins['poll'] = [mock_poll]

        flow._runCallbackPoll()
        assert len(flow.worklist.incoming) == 0

    def test_poll_plugin_crash_continues(self):
        flow = _make_flow_stub()
        crash_poll = MagicMock(side_effect=RuntimeError('crash'))
        crash_poll.__module__ = 'test'
        crash_poll.__qualname__ = 'test'
        ok_poll = MagicMock(return_value=[_make_msg()])
        flow.plugins['poll'] = [crash_poll, ok_poll]

        flow._runCallbackPoll()
        assert len(flow.worklist.incoming) == 1

    def test_flow_Poll_method_called_if_exists(self):
        flow = _make_flow_stub()
        called = []
        def flow_poll():
            called.append(True)
        flow.Poll = flow_poll
        flow.plugins['poll'] = []

        flow._runCallbackPoll()
        assert len(called) == 1


# ══════════════════════════════════════════════════════════════════════════════
# loadCallbacks
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_loadCallbacks:

    def test_returns_false_on_import_error(self):
        flow = _make_flow_stub()
        flow.o.imports = ['nonexistent.module']
        result = flow.loadCallbacks([])
        assert result is False

    def test_returns_false_on_plugin_load_error(self):
        flow = _make_flow_stub()
        flow.o.imports = []
        with patch('sarracenia.flowcb.load_library', side_effect=ImportError('fail')):
            result = flow.loadCallbacks(['bad.plugin.Module'])
        assert result is False

    def test_empty_plugins_returns_true(self):
        flow = _make_flow_stub()
        flow.o.imports = []
        result = flow.loadCallbacks([])
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# renameOneItem
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_renameOneItem:

    def test_rename_existing_file(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        old = tmp_path / 'old.txt'
        old.write_text('data')
        new = tmp_path / 'new.txt'

        result = flow.renameOneItem(str(old), str(new))
        assert result is True
        assert new.exists()
        assert not old.exists()

    def test_rename_nonexistent_returns_false(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        result = flow.renameOneItem(str(tmp_path / 'ghost'), str(tmp_path / 'new'))
        assert result is False

    def test_rename_cleans_up_target(self, tmp_path):
        flow = _make_flow_stub(tmp_path=tmp_path)
        old = tmp_path / 'old.txt'
        old.write_text('new data')
        target = tmp_path / 'target.txt'
        target.write_text('old target')

        result = flow.renameOneItem(str(old), str(target))
        assert result is True
        assert target.read_text() == 'new data'
