"""
Seam-only tests for AMQPConsumer moth subclass.
No broker connections. Tests init inheritance, consumer tag state,
and close ordering with cancel.
"""

import pytest
from unittest.mock import patch, MagicMock
import sarracenia.moth.amqpconsumer
from sarracenia.moth.amqpconsumer import AMQPConsumer


def _make_consumer(extra_props=None):
    """Create an AMQPConsumer instance."""
    props = {
        'broker': MagicMock(),
        'dry_run': False,
        'exchange': 'xpublic',
        'message_strategy': {'stubborn': True, 'reset': True, 'failure_duration': '5m'},
    }
    if extra_props:
        props.update(extra_props)
    with patch('sarracenia.moth.amqp.amqp'):
        return AMQPConsumer(props, True)


class Test_AMQPConsumer_init:
    """AMQPConsumer init state."""

    def test_init_has_raw_msg_q_none(self):
        inst = _make_consumer()
        assert inst._raw_msg_q is None

    def test_init_consumer_tag_empty(self):
        inst = _make_consumer()
        assert inst._request_consumer_tag == ''
        assert inst._active_consumer_tag is None

    def test_init_inherits_amqp_defaults(self):
        inst = _make_consumer()
        assert inst.o['vhost'] == '/'
        assert inst.o['durable'] is True

    def test_init_is_subscriber(self):
        inst = _make_consumer()
        assert inst.is_subscriber is True

    def test_init_connection_none(self):
        inst = _make_consumer()
        assert inst.connection is None


class Test_AMQPConsumer_close:
    """AMQPConsumer close should cancel consumer first."""

    def test_close_without_active_consumer(self):
        inst = _make_consumer()
        inst.connection = None
        inst._active_consumer_tag = None
        inst.close()

    def test_close_with_active_consumer(self):
        inst = _make_consumer()
        mock_conn = MagicMock()
        mock_channel = MagicMock()
        inst.connection = mock_conn
        inst.channel = mock_channel
        inst._active_consumer_tag = 'ctag_123'
        inst.close()
        mock_channel.basic_cancel.assert_called_once_with('ctag_123')
        mock_conn.close.assert_called_once()

    def test_close_cancel_failure_still_calls_super_close(self):
        inst = _make_consumer()
        mock_conn = MagicMock()
        mock_channel = MagicMock()
        mock_channel.basic_cancel.side_effect = Exception("cancel failed")
        inst.connection = mock_conn
        inst.channel = mock_channel
        inst._active_consumer_tag = 'ctag_bad'
        inst.close()
        mock_conn.close.assert_called_once()
