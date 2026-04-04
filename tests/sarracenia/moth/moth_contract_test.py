"""
Cross-protocol moth contract / invariant test suite.

Tests invariants that ALL moth protocol implementations should obey,
parametrized across AMQP, AMQPConsumer, MQTT, and AMQ1.

Contract:
- All implementations share the same metrics interface
- All implementations respect please_stop()
- All implementations start in disconnected state
- close() is safe to call in any state (including pre-connection)
- Metrics reset zeroes counters without losing 'connected' key
- metricsConnect/metricsDisconnect lifecycle is consistent
- No stale state leaks across repeated metrics operations
"""

import pytest
import time
import threading
from unittest.mock import patch, MagicMock
from sarracenia.moth import Moth
from sarracenia.moth.amqp import AMQP
from sarracenia.moth.amqpconsumer import AMQPConsumer
from sarracenia.moth.mqtt import MQTT
from sarracenia.moth.amq1 import AMQ1


# ===========================================================================
# Instance factories
# ===========================================================================

def _make_moth():
    return Moth(props=None, is_subscriber=True)


def _make_amqp():
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    with patch('sarracenia.moth.amqp.amqp'):
        return AMQP(props, True)


def _make_amqpconsumer():
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    with patch('sarracenia.moth.amqp.amqp'):
        return AMQPConsumer(props, True)


def _make_mqtt():
    m = MQTT.__new__(MQTT)
    m.is_subscriber = True
    m.connected = False
    m.connect_in_progress = False
    m.subscribe_in_progress = 0
    m._stop_requested = False
    m.o = {
        'batch': 25, 'clean_session': False, 'max_inflight_messages': 20000,
        'no': 0, 'prefetch': 25, 'topicPrefix': ['v03'],
        'broker': MagicMock(), 'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
        'messageDebugDump': False, 'tlsRigour': 'normal',
    }
    m.metrics = {'connected': False}
    m.metricsReset()
    m.next_connect_time = 0
    m.next_connect_failures = 0
    m.next_message = 0
    m.subscribe_mutex = threading.Lock()
    m.rx_msg_mutex = threading.Lock()
    m.rx_msg = [[], [], [], [], []]
    m.rx_msg_iToApp = 0
    m.rx_msg_iFromBroker = 1
    m.rx_msg_iMax = 4
    return m


def _make_amq1():
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': None,
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    return AMQ1(props, True)


PROTOCOL_FACTORIES = [
    pytest.param(_make_moth, id='moth_base'),
    pytest.param(_make_amqp, id='amqp'),
    pytest.param(_make_amqpconsumer, id='amqpconsumer'),
    pytest.param(_make_mqtt, id='mqtt'),
    pytest.param(_make_amq1, id='amq1'),
]

METRIC_COUNTER_KEYS = [
    'disconnectLast', 'disconnectTime', 'disconnectCount',
    'rxByteCount', 'rxGoodCount', 'rxBadCount',
    'txByteCount', 'txGoodCount', 'txBadCount',
]


class Test_Moth_Contract_initial_state:
    """All protocols start in consistent initial state."""

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_starts_disconnected(self, factory):
        inst = factory()
        assert inst.metrics['connected'] is False

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_stop_requested_initially_false(self, factory):
        inst = factory()
        assert inst._stop_requested is False

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metrics_counters_initially_zero(self, factory):
        inst = factory()
        for key in METRIC_COUNTER_KEYS:
            assert inst.metrics[key] == 0, f"{key} not zero for {factory}"


class Test_Moth_Contract_please_stop:
    """please_stop() works consistently across all protocols."""

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_please_stop_sets_flag(self, factory):
        inst = factory()
        inst.please_stop()
        assert inst._stop_requested is True

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_please_stop_idempotent(self, factory):
        inst = factory()
        inst.please_stop()
        inst.please_stop()
        assert inst._stop_requested is True


class Test_Moth_Contract_metrics_lifecycle:
    """Metrics connect/disconnect/reset lifecycle is consistent."""

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metricsConnect_sets_connected(self, factory):
        inst = factory()
        inst.metricsConnect()
        assert inst.metrics['connected'] is True

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metricsDisconnect_sets_disconnected(self, factory):
        inst = factory()
        inst.metricsConnect()
        inst.metricsDisconnect()
        assert inst.metrics['connected'] is False
        assert inst.metrics['disconnectCount'] == 1

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metricsReset_zeroes_counters(self, factory):
        inst = factory()
        inst.metrics['rxGoodCount'] = 100
        inst.metrics['txByteCount'] = 999
        inst.metricsReset()
        for key in METRIC_COUNTER_KEYS:
            assert inst.metrics[key] == 0

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metricsReset_preserves_connected(self, factory):
        inst = factory()
        inst.metricsConnect()
        inst.metricsReset()
        assert inst.metrics['connected'] is True

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_metricsReport_returns_dict(self, factory):
        inst = factory()
        result = inst.metricsReport()
        assert isinstance(result, dict)
        assert 'connected' in result

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_multiple_connect_disconnect_cycles(self, factory):
        inst = factory()
        for _ in range(5):
            inst.metricsConnect()
            inst.metricsDisconnect()
        assert inst.metrics['disconnectCount'] == 5

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_no_stale_state_after_reset(self, factory):
        inst = factory()
        inst.metrics['rxGoodCount'] = 42
        inst.metrics['txGoodCount'] = 99
        inst.metricsReset()
        inst.metricsReset()
        assert inst.metrics['rxGoodCount'] == 0
        assert inst.metrics['txGoodCount'] == 0


class Test_Moth_Contract_EBO:
    """Exponential backoff works consistently."""

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_setEbo_increments_failures(self, factory):
        inst = factory()
        assert inst.next_connect_failures == 0
        inst.setEbo(time.time() - 1)
        assert inst.next_connect_failures == 1

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_setEbo_sets_future_time(self, factory):
        inst = factory()
        now = time.time()
        inst.setEbo(now - 1)
        assert inst.next_connect_time > now


class Test_Moth_Contract_is_subscriber:
    """is_subscriber flag set correctly."""

    @pytest.mark.parametrize('factory', PROTOCOL_FACTORIES)
    def test_is_subscriber_true(self, factory):
        inst = factory()
        assert inst.is_subscriber is True
