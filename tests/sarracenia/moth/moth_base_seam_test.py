"""
Seam-only tests for the Moth base class.
No broker connections. Tests init branches, metrics lifecycle,
exponential backoff, splitPick distribution, please_stop, cleanup
delegation, factory validation, and abstract method defaults.
"""

import pytest
import time
from unittest.mock import MagicMock
import sarracenia.moth
from sarracenia.moth import Moth


def _make_base_moth(is_subscriber=True, props=None):
    """Create a Moth instance directly."""
    return Moth(props=props, is_subscriber=is_subscriber)


class Test_Moth_init:
    """Test Moth.__init__ branches."""

    def test_init_no_props(self):
        m = _make_base_moth(props=None)
        assert m.is_subscriber is True
        assert m.connected is False
        assert m._stop_requested is False
        assert isinstance(m.metrics, dict)
        assert m.metrics['connected'] is False
        assert m.o['broker'] is None

    def test_init_publisher(self):
        m = _make_base_moth(is_subscriber=False, props={'exchange': 'xtest'})
        assert m.is_subscriber is False
        assert m.o['exchange'] == 'xtest'

    def test_init_props_override_defaults(self):
        m = _make_base_moth(props={'batch': 999, 'expire': 600})
        assert m.o['batch'] == 999
        assert m.o['expire'] == 600

    def test_init_subscriber_index_branch(self):
        mock_broker = MagicMock()
        mock_broker.url = MagicMock()
        props = {
            'subscriber_index': 0,
            'subscription_index': 0,
            'subscriptions': [
                {'broker': mock_broker, 'exchange': 'xfrom_sub'}
            ],
        }
        m = _make_base_moth(is_subscriber=True, props=props)
        assert m.o['broker'] is mock_broker
        assert m.o['exchange'] == 'xfrom_sub'

    def test_init_publisher_index_branch(self):
        mock_broker = MagicMock()
        mock_broker.url = MagicMock()
        props = {
            'publisher_index': 0,
            'publishers': [
                {'broker': mock_broker, 'exchange': 'xfrom_pub', 'topicPrefix': ['v02']}
            ],
        }
        m = _make_base_moth(is_subscriber=False, props=props)
        assert m.o['broker'] is mock_broker
        assert m.o['exchange'] == 'xfrom_pub'
        assert m.o['topicPrefix'] == ['v02']

    def test_init_settings_override(self):
        props = {
            'settings': {
                'sarracenia.moth.Moth': {
                    'batch': 42,
                }
            }
        }
        m = _make_base_moth(props=props)
        assert m.o['batch'] == 42

    def test_init_next_connect_time_is_now(self):
        before = time.time()
        m = _make_base_moth()
        after = time.time()
        assert before <= m.next_connect_time <= after
        assert m.next_connect_failures == 0


class Test_Moth_metrics:
    """Test metrics lifecycle."""

    def test_metricsReset_zeroes_all_counters(self):
        m = _make_base_moth()
        m.metrics['rxGoodCount'] = 100
        m.metrics['txByteCount'] = 999
        m.metrics['disconnectCount'] = 5
        m.metricsReset()
        for key in ['disconnectLast', 'disconnectTime', 'disconnectCount',
                     'rxByteCount', 'rxGoodCount', 'rxBadCount',
                     'txByteCount', 'txGoodCount', 'txBadCount']:
            assert m.metrics[key] == 0

    def test_metricsReset_preserves_connected_key(self):
        m = _make_base_moth()
        m.metrics['connected'] = True
        m.metricsReset()
        assert m.metrics['connected'] is True

    def test_metricsConnect_sets_connected_true(self):
        m = _make_base_moth()
        assert m.metrics['connected'] is False
        m.metricsConnect()
        assert m.metrics['connected'] is True

    def test_metricsConnect_accumulates_downtime(self):
        m = _make_base_moth()
        past = time.time() - 10.0
        m.metrics['disconnectLast'] = past
        m.metricsConnect()
        assert m.metrics['disconnectTime'] >= 9.0

    def test_metricsConnect_no_downtime_if_never_disconnected(self):
        m = _make_base_moth()
        m.metrics['disconnectLast'] = 0
        m.metricsConnect()
        assert m.metrics['disconnectTime'] == 0

    def test_metricsDisconnect_sets_connected_false(self):
        m = _make_base_moth()
        m.metricsConnect()
        m.metricsDisconnect()
        assert m.metrics['connected'] is False

    def test_metricsDisconnect_increments_count(self):
        m = _make_base_moth()
        m.metricsDisconnect()
        m.metricsDisconnect()
        m.metricsDisconnect()
        assert m.metrics['disconnectCount'] == 3

    def test_metricsDisconnect_sets_last_timestamp(self):
        before = time.time()
        m = _make_base_moth()
        m.metricsDisconnect()
        after = time.time()
        assert before <= m.metrics['disconnectLast'] <= after

    def test_metricsReport_returns_dict(self):
        m = _make_base_moth()
        result = m.metricsReport()
        assert isinstance(result, dict)
        assert result is m.metrics

    def test_metricsReport_accumulates_downtime_when_disconnected(self):
        m = _make_base_moth()
        m.metrics['connected'] = False
        m.metrics['disconnectLast'] = time.time() - 5.0
        m.metricsReport()
        assert m.metrics['disconnectTime'] >= 4.0

    def test_metrics_full_lifecycle(self):
        m = _make_base_moth()
        assert m.metrics['connected'] is False
        m.metricsConnect()
        assert m.metrics['connected'] is True
        assert m.metrics['disconnectCount'] == 0
        m.metricsDisconnect()
        assert m.metrics['connected'] is False
        assert m.metrics['disconnectCount'] == 1
        m.metricsConnect()
        assert m.metrics['connected'] is True

    def test_repeated_reset_idempotent(self):
        m = _make_base_moth()
        m.metrics['rxGoodCount'] = 50
        m.metricsReset()
        m.metricsReset()
        assert m.metrics['rxGoodCount'] == 0


class Test_Moth_EBO:
    """Test exponential backoff calculation."""

    def test_setEbo_increments_failures(self):
        m = _make_base_moth()
        assert m.next_connect_failures == 0
        m.setEbo(time.time() - 1)
        assert m.next_connect_failures == 1
        m.setEbo(time.time() - 1)
        assert m.next_connect_failures == 2

    def test_setEbo_sets_future_connect_time(self):
        m = _make_base_moth()
        now = time.time()
        m.setEbo(now - 1)
        assert m.next_connect_time > now

    def test_setEbo_grows_with_failures(self):
        m = _make_base_moth()
        m.setEbo(time.time() - 1)
        t1 = m.next_connect_time
        m.setEbo(time.time() - 1)
        t2 = m.next_connect_time
        assert t2 >= t1

    def test_setEbo_bounded_by_maximum(self):
        m = _make_base_moth()
        for i in range(100):
            m.setEbo(time.time() - 0.001)
        max_interval = sarracenia.moth.eboIntervalMaximum
        now = time.time()
        assert m.next_connect_time <= now + max_interval + 1

    def test_setEbo_minimum_wait_one_second(self):
        m = _make_base_moth()
        now = time.time()
        m.setEbo(now)
        assert m.next_connect_time >= now


class Test_Moth_splitPick:
    """Test exchange split distribution."""

    def test_splitPick_with_relPath(self):
        m = _make_base_moth()
        m.o['exchange'] = ['x0', 'x1', 'x2']
        msg = {'relPath': 'data/obs/file.dat'}
        idx = m.splitPick(msg)
        assert 0 <= idx < 3

    def test_splitPick_with_retrievePath(self):
        m = _make_base_moth()
        m.o['exchange'] = ['x0', 'x1']
        msg = {'retrievePath': '/some/path'}
        idx = m.splitPick(msg)
        assert 0 <= idx < 2

    def test_splitPick_with_override(self):
        m = _make_base_moth()
        m.o['exchange'] = ['x0', 'x1', 'x2']
        msg = {'exchangeSplitOverride': 7}
        idx = m.splitPick(msg)
        assert idx == 7 % 3

    def test_splitPick_missing_fields_returns_zero(self):
        m = _make_base_moth()
        m.o['exchange'] = ['x0', 'x1']
        msg = {}
        idx = m.splitPick(msg)
        assert idx == 0

    def test_splitPick_deterministic(self):
        m = _make_base_moth()
        m.o['exchange'] = ['x0', 'x1', 'x2', 'x3']
        msg = {'relPath': 'stable/path/file.txt'}
        results = [m.splitPick(msg) for _ in range(10)]
        assert len(set(results)) == 1


class Test_Moth_please_stop:
    """Test stop request behavior."""

    def test_please_stop_sets_flag(self):
        m = _make_base_moth()
        assert m._stop_requested is False
        m.please_stop()
        assert m._stop_requested is True

    def test_please_stop_idempotent(self):
        m = _make_base_moth()
        m.please_stop()
        m.please_stop()
        assert m._stop_requested is True


class Test_Moth_base_abstract:
    """Base class abstract methods return safe defaults."""

    def test_ack_returns_none(self):
        m = _make_base_moth()
        result = m.ack(MagicMock())
        assert result is None

    def test_getNewMessage_returns_none(self):
        m = _make_base_moth()
        result = m.getNewMessage()
        assert result is None

    def test_newMessages_returns_empty_list(self):
        m = _make_base_moth()
        result = m.newMessages()
        assert result == []

    def test_putNewMessage_returns_false(self):
        m = _make_base_moth()
        result = m.putNewMessage(MagicMock())
        assert result is False

    def test_close_no_error(self):
        m = _make_base_moth()
        m.close()


class Test_Moth_cleanup:
    """Test cleanup delegation."""

    def test_cleanup_subscriber_calls_getCleanUp(self):
        m = _make_base_moth(is_subscriber=True)
        m.getCleanUp = MagicMock()
        m.cleanup()
        m.getCleanUp.assert_called_once()

    def test_cleanup_publisher_calls_putCleanUp(self):
        m = _make_base_moth(is_subscriber=False)
        m.putCleanUp = MagicMock()
        m.cleanup()
        m.putCleanUp.assert_called_once()


class Test_Moth_factory:
    """Test factory method validation."""

    def test_subFactory_no_broker_returns_none(self):
        props = sarracenia.moth.default_options()
        props['broker'] = None
        result = Moth.subFactory(props)
        assert result is None

    def test_subFactory_broker_no_url_attr_returns_none(self):
        props = sarracenia.moth.default_options()
        props['broker'] = "not_a_broker_object"
        result = Moth.subFactory(props)
        assert result is None

    def test_subFactory_unknown_scheme_returns_none(self):
        props = sarracenia.moth.default_options()
        broker = MagicMock()
        broker.url = MagicMock()
        broker.url.scheme = 'unknownproto'
        props['broker'] = broker
        result = Moth.subFactory(props)
        assert result is None

    def test_pubFactory_no_broker_returns_none(self):
        props = sarracenia.moth.default_options()
        props['broker'] = None
        result = Moth.pubFactory(props)
        assert result is None

    def test_pubFactory_broker_no_url_returns_none(self):
        props = sarracenia.moth.default_options()
        props['broker'] = "string_not_broker"
        result = Moth.pubFactory(props)
        assert result is None

    def test_pubFactory_unknown_scheme_returns_none(self):
        props = sarracenia.moth.default_options()
        broker = MagicMock()
        broker.url = MagicMock()
        broker.url.scheme = 'badscheme'
        props['broker'] = broker
        result = Moth.pubFactory(props)
        assert result is None

    def test_findAllSubclasses_returns_set(self):
        result = Moth.findAllSubclasses(Moth)
        assert isinstance(result, set)
        assert len(result) >= 1
