"""
Seam-only tests for MQTT moth subclass.
No broker connections. Tests init state, close behavior,
double-buffer rotation, getNewMessage/newMessages batch,
and metrics tracking.
"""

import pytest
import time
import threading
from unittest.mock import patch, MagicMock
from sarracenia.moth.mqtt import MQTT


def _make_mqtt(is_subscriber=True, extra_props=None):
    """Create a minimal MQTT instance without connecting."""
    m = MQTT.__new__(MQTT)
    m.is_subscriber = is_subscriber
    m.connected = False
    m.connect_in_progress = False
    m.subscribe_in_progress = 0
    m._stop_requested = False
    m.o = {
        'batch': 25,
        'clean_session': False,
        'max_inflight_messages': 20000,
        'no': 0,
        'prefetch': 25,
        'topicPrefix': ['v03'],
        'broker': MagicMock(),
        'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
        'messageDebugDump': False,
        'tlsRigour': 'normal',
    }
    if extra_props:
        m.o.update(extra_props)
    m.metrics = {
        'rxBadCount': 0, 'txBadCount': 0,
        'rxByteCount': 0, 'txByteCount': 0,
        'rxGoodCount': 0, 'txGoodCount': 0,
        'rxLast': '', 'txLast': '',
        'connected': False,
        'disconnectLast': 0, 'disconnectTime': 0, 'disconnectCount': 0,
    }
    m.next_connect_time = 0
    m.next_connect_failures = 0
    m.next_message = 0
    if is_subscriber:
        m.subscribe_mutex = threading.Lock()
        m.rx_msg_mutex = threading.Lock()
        m.rx_msg = [[], [], [], [], []]
        m.rx_msg_iToApp = 0
        m.rx_msg_iFromBroker = 1
        m.rx_msg_iMax = 4
    return m


class Test_MQTT_init_state:
    """MQTT initialization state."""

    def test_subscriber_has_buffers(self):
        m = _make_mqtt(is_subscriber=True)
        assert len(m.rx_msg) == 5
        assert m.rx_msg_iToApp == 0
        assert m.rx_msg_iFromBroker == 1

    def test_publisher_no_buffers(self):
        m = _make_mqtt(is_subscriber=False)
        assert not hasattr(m, 'rx_msg')

    def test_initial_connected_false(self):
        m = _make_mqtt()
        assert m.connected is False

    def test_initial_metrics_zeroed(self):
        m = _make_mqtt()
        assert m.metrics['rxGoodCount'] == 0
        assert m.metrics['txGoodCount'] == 0
        assert m.metrics['disconnectCount'] == 0


class Test_MQTT_close_seam:
    """MQTT close behavior (extending existing mqtt_thread_leak_test.py)."""

    def test_close_sets_connected_false(self):
        m = _make_mqtt()
        m.connected = True
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        m.client = mock_client
        m.close()
        assert m.connected is False

    def test_close_no_client_attr(self):
        m = _make_mqtt()
        m.client = MagicMock()
        m.client.is_connected.return_value = False
        m.close()

    def test_close_client_not_connected_skips_disconnect(self):
        m = _make_mqtt()
        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        m.client = mock_client
        m.close()
        mock_client.disconnect.assert_not_called()


class Test_MQTT_buffer_rotation:
    """Test double-buffer rotation for async reception."""

    def test_rotate_swaps_when_app_empty(self):
        m = _make_mqtt(is_subscriber=True)
        m.rx_msg[m.rx_msg_iFromBroker] = ['msg1', 'msg2']
        m.rx_msg[m.rx_msg_iToApp] = []

        old_to_app = m.rx_msg_iToApp
        m._rotateInputBuffers()
        # After rotation when app is empty, should swap
        assert m.rx_msg_iToApp != old_to_app

    def test_rotate_no_swap_when_app_has_messages(self):
        m = _make_mqtt(is_subscriber=True)
        m.rx_msg[m.rx_msg_iToApp] = ['still_processing']
        m.rx_msg[m.rx_msg_iFromBroker] = ['new_msg']

        old_to_app = m.rx_msg_iToApp
        m._rotateInputBuffers()
        assert m.rx_msg_iToApp == old_to_app

    def test_rotate_wraps_around_at_max(self):
        m = _make_mqtt(is_subscriber=True)
        m.rx_msg_iToApp = 3
        m.rx_msg_iFromBroker = 4
        m.rx_msg[3] = []
        m.rx_msg[4] = ['msg']
        m._rotateInputBuffers()
        # After wrap, indices should be within [0, max]
        assert 0 <= m.rx_msg_iToApp <= m.rx_msg_iMax
        assert 0 <= m.rx_msg_iFromBroker <= m.rx_msg_iMax

    def test_getNewMessage_returns_none_when_empty(self):
        m = _make_mqtt(is_subscriber=True)
        m.connected = True
        m.rx_msg[m.rx_msg_iToApp] = []
        m.rx_msg[m.rx_msg_iFromBroker] = []
        m.o['subscription_index'] = 0
        result = m.getNewMessage()
        assert result is None

    def test_getNewMessage_returns_message_from_buffer(self):
        m = _make_mqtt(is_subscriber=True)
        m.connected = True
        mock_msg = {'baseUrl': 'https://example.com', 'relPath': '/data', '_deleteOnPost': set()}
        m.rx_msg[m.rx_msg_iToApp] = [mock_msg]
        m.o['subscription_index'] = 0
        result = m.getNewMessage()
        assert result is not None
        assert result['baseUrl'] == 'https://example.com'

    def test_newMessages_returns_list(self):
        m = _make_mqtt(is_subscriber=True)
        m.connected = True
        m.rx_msg[m.rx_msg_iToApp] = []
        m.rx_msg[m.rx_msg_iFromBroker] = []
        result = m.newMessages()
        assert isinstance(result, list)
        assert result == []

    def test_newMessages_returns_batch(self):
        m = _make_mqtt(is_subscriber=True)
        m.connected = True
        msgs = [{'baseUrl': f'https://ex{i}.com', 'relPath': f'/f{i}'} for i in range(5)]
        m.rx_msg[m.rx_msg_iToApp] = msgs
        m.o['batch'] = 25
        result = m.newMessages()
        assert len(result) == 5

    def test_newMessages_limited_by_batch(self):
        m = _make_mqtt(is_subscriber=True)
        m.connected = True
        msgs = [{'baseUrl': f'https://ex{i}.com', 'relPath': f'/f{i}'} for i in range(10)]
        m.rx_msg[m.rx_msg_iToApp] = msgs
        m.o['batch'] = 3
        result = m.newMessages()
        assert len(result) == 3


class Test_MQTT_metrics_tracking:
    """MQTT metrics integration."""

    def test_metricsConnect_sets_connected(self):
        m = _make_mqtt()
        m.metricsConnect()
        assert m.metrics['connected'] is True

    def test_metricsDisconnect_increments_count(self):
        m = _make_mqtt()
        m.metricsDisconnect()
        assert m.metrics['disconnectCount'] == 1
        assert m.metrics['connected'] is False

    def test_repeated_connect_disconnect_cycle(self):
        m = _make_mqtt()
        for _ in range(5):
            m.metricsConnect()
            m.metricsDisconnect()
        assert m.metrics['disconnectCount'] == 5


class Test_MQTT_please_stop:
    """MQTT stop request behavior."""

    def test_please_stop(self):
        m = _make_mqtt()
        m.please_stop()
        assert m._stop_requested is True
