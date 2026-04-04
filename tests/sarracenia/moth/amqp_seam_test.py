"""
Seam-only tests for AMQP moth subclass.
No broker connections. Tests init config, close behavior,
ack validation branches, and metrics tracking.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
import sarracenia.moth.amqp
from sarracenia.moth.amqp import AMQP


def _make_amqp(is_subscriber=True, extra_props=None):
    """Create an AMQP instance with mocked amqp library."""
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    if extra_props:
        props.update(extra_props)
    with patch('sarracenia.moth.amqp.amqp'):
        return AMQP(props, is_subscriber)


class Test_AMQP_init:
    """AMQP init config branches."""

    def test_init_subscriber_defaults(self):
        inst = _make_amqp(is_subscriber=True)
        assert inst.is_subscriber is True
        assert inst.connection is None
        assert inst.first_setup is True

    def test_init_publisher_defaults(self):
        inst = _make_amqp(is_subscriber=False)
        assert inst.is_subscriber is False
        assert inst.connection is None

    def test_init_inherits_moth_defaults(self):
        inst = _make_amqp()
        assert 'tlsRigour' in inst.o
        assert 'batch' in inst.o

    def test_init_amqp_specific_defaults(self):
        inst = _make_amqp()
        assert inst.o['vhost'] == '/'
        assert inst.o['durable'] is True
        assert inst.o['persistent'] is True
        assert inst.o['prefetch'] == 25
        assert inst.o['queueDeclare'] is True
        assert inst.o['queueBind'] is True
        assert inst.o['exchangeDeclare'] is True

    def test_init_stop_requested_false(self):
        inst = _make_amqp()
        assert inst._stop_requested is False

    def test_init_metrics_initialized(self):
        inst = _make_amqp()
        assert inst.metrics['rxGoodCount'] == 0
        assert inst.metrics['txGoodCount'] == 0
        assert inst.metrics['connected'] is False


class Test_AMQP_close:
    """AMQP close behavior."""

    def test_close_with_no_connection(self):
        inst = _make_amqp()
        inst.connection = None
        inst.close()
        assert inst.metrics['disconnectCount'] == 1

    def test_close_with_connection(self):
        inst = _make_amqp()
        mock_conn = MagicMock()
        inst.connection = mock_conn
        inst.close()
        mock_conn.close.assert_called_once()
        assert inst.metrics['disconnectCount'] == 1

    def test_close_idempotent(self):
        inst = _make_amqp()
        inst.connection = MagicMock()
        inst.close()
        inst.connection = None
        inst.close()
        assert inst.metrics['disconnectCount'] == 2


class Test_AMQP_ack:
    """AMQP ack validation branches."""

    def test_ack_missing_ack_id(self):
        inst = _make_amqp()
        msg = {}
        inst.ack(msg)

    def test_ack_mismatched_connection_id(self):
        inst = _make_amqp()
        inst.connection = MagicMock()
        inst.connection_id = 'conn_abc_sub'
        mock_channel = MagicMock()
        mock_channel.channel_id = 'ch1'
        inst.channel = mock_channel
        inst.broker = MagicMock()
        msg = {
            'ack_id': {
                'delivery_tag': 1,
                'channel_id': 'ch1',
                'connection_id': 'conn_DIFFERENT_sub',
                'broker': inst.broker,
            },
            '_deleteOnPost': set(['ack_id']),
        }
        result = inst.ack(msg)
        assert result is False

    def test_ack_no_connection(self):
        inst = _make_amqp()
        inst.connection = None
        inst.connection_id = 'conn_x'
        mock_channel = MagicMock()
        mock_channel.channel_id = 'ch1'
        inst.channel = mock_channel
        inst.broker = MagicMock()
        msg = {
            'ack_id': {
                'delivery_tag': 1,
                'channel_id': 'ch1',
                'connection_id': 'conn_x',
                'broker': inst.broker,
            },
            '_deleteOnPost': set(['ack_id']),
        }
        result = inst.ack(msg)
        assert result is True


class Test_AMQP_msgRawToDict:
    """Test message decode without broker."""

    def test_raw_to_dict_none_message(self):
        inst = _make_amqp()
        result = inst._msgRawToDict(None)
        assert result is None

    def test_raw_to_dict_decode_failure_returns_none(self):
        """When PostFormat.importAny returns None, _msgRawToDict returns None."""
        inst = _make_amqp()
        mock_channel = MagicMock()
        inst.channel = mock_channel
        raw = MagicMock()
        raw.body = '{"invalid": true}'
        raw.properties = {'content_type': 'application/json'}
        raw.headers = {}
        raw.delivery_info = {'delivery_tag': 42, 'routing_key': 'v03.post.data', 'exchange': 'xpublic'}

        with patch('sarracenia.moth.amqp.PostFormat.importAny', return_value=None):
            result = inst._msgRawToDict(raw)
            assert result is None
            # Should ack the bad message
            mock_channel.basic_ack.assert_called_once_with(42)


class Test_AMQP_repeated_invocation:
    """Ensure no stale state across repeated calls."""

    def test_metrics_not_leaked_across_resets(self):
        inst = _make_amqp()
        inst.metrics['rxGoodCount'] = 100
        inst.metricsReset()
        assert inst.metrics['rxGoodCount'] == 0
        assert inst.metrics['txGoodCount'] == 0
