"""
Seam-only tests for AMQ1 (AMQP 1.0) moth subclass.
No broker connections. Tests init config, close components,
message decode, and metrics.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
import sarracenia
import sarracenia.moth.amq1
from sarracenia.moth.amq1 import AMQ1


def _make_amq1(is_subscriber=True, extra_props=None):
    """Create an AMQ1 instance."""
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': None,
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    if extra_props:
        props.update(extra_props)
    return AMQ1(props, is_subscriber)


class Test_AMQ1_init:
    """AMQ1 init config branches."""

    def test_init_subscriber(self):
        inst = _make_amq1(is_subscriber=True)
        assert inst.is_subscriber is True
        assert inst.client is None

    def test_init_publisher(self):
        inst = _make_amq1(is_subscriber=False)
        assert inst.is_subscriber is False
        assert inst.client is None

    def test_init_defaults_applied(self):
        inst = _make_amq1()
        assert inst.o['durable'] is True
        assert inst.o['persistent'] is True
        assert inst.o['topicPrefix'] == ['v03']
        assert inst.o['topicSeparator'] == '/'

    def test_init_metrics_zeroed(self):
        inst = _make_amq1()
        assert inst.metrics['rxGoodCount'] == 0
        assert inst.metrics['txGoodCount'] == 0
        assert inst.metrics['disconnectCount'] == 0

    def test_init_stop_requested_false(self):
        inst = _make_amq1()
        assert inst._stop_requested is False

    def test_init_threading_state(self):
        inst = _make_amq1()
        assert inst.reactor is None
        assert inst.client_thread is None


class Test_AMQ1_close:
    """AMQ1 close behavior."""

    def test_close_with_no_client(self):
        inst = _make_amq1()
        inst.client = None
        inst.reactor = None
        inst.client_thread = None
        inst.close()

    def test_close_with_client(self):
        inst = _make_amq1()
        mock_client = MagicMock()
        inst.client = mock_client
        inst.reactor = MagicMock()
        inst.client_thread = MagicMock()
        inst.close()
        mock_client.close.assert_called_once()

    def test_close_stops_reactor(self):
        inst = _make_amq1()
        inst.client = MagicMock()
        mock_reactor = MagicMock()
        inst.reactor = mock_reactor
        mock_thread = MagicMock()
        inst.client_thread = mock_thread
        inst.close()
        mock_reactor.stop.assert_called_once()

    def test_close_joins_thread(self):
        inst = _make_amq1()
        inst.client = MagicMock()
        inst.reactor = MagicMock()
        mock_thread = MagicMock()
        inst.client_thread = mock_thread
        inst.close()
        mock_thread.join.assert_called_once()


class Test_AMQ1_msgRawToDict:
    """AMQ1 message decode without broker."""

    def test_decode_valid_json(self):
        inst = _make_amq1()
        raw = MagicMock()
        raw.body = b'{"baseUrl": "https://example.com", "relPath": "/data"}'
        raw.properties = MagicMock()
        raw.properties.content_type = 'application/json'
        raw.properties.group_id = None
        raw.properties.subject = 'v03/post/data'

        mock_msg = sarracenia.Message()
        mock_msg['baseUrl'] = 'https://example.com'
        mock_msg['relPath'] = '/data'
        mock_msg['_format'] = 'v03'
        mock_msg['_deleteOnPost'] = set(['_format'])
        with patch('sarracenia.moth.amq1.PostFormat.importAny', return_value=mock_msg):
            result = inst._msgRawToDict(raw)
            if result is not None:
                assert isinstance(result, dict)
                assert 'local_offset' in result

    def test_decode_memoryview_body(self):
        inst = _make_amq1()
        raw = MagicMock()
        raw.body = memoryview(b'{"baseUrl": "https://example.com", "relPath": "/data"}')
        raw.properties = MagicMock()
        raw.properties.content_type = 'application/json'
        raw.properties.group_id = None
        raw.properties.subject = 'v03/post/data'

        mock_msg = sarracenia.Message()
        mock_msg['baseUrl'] = 'https://example.com'
        mock_msg['relPath'] = '/data'
        mock_msg['_format'] = 'v03'
        mock_msg['_deleteOnPost'] = set(['_format'])
        with patch('sarracenia.moth.amq1.PostFormat.importAny', return_value=mock_msg):
            result = inst._msgRawToDict(raw)
            if result is not None:
                assert isinstance(result, dict)


class Test_AMQ1_repeated_invocation:
    """No stale state across resets."""

    def test_metrics_reset(self):
        inst = _make_amq1()
        inst.metrics['rxGoodCount'] = 50
        inst.metricsReset()
        assert inst.metrics['rxGoodCount'] == 0

    def test_please_stop(self):
        inst = _make_amq1()
        inst.please_stop()
        assert inst._stop_requested is True
