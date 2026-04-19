import pytest
from tests.conftest import *
from unittest.mock import MagicMock, patch, call

import sarracenia
import sarracenia.config
import sarracenia.flowcb.gather.message
from sarracenia.flowcb.gather.message import Message


def _make_options(subscriptions=None):
    """Return a minimal options object with optional subscriptions."""
    options = MagicMock()
    options.logFormat = '%(message)s'
    options.logLevel = 'INFO'
    if subscriptions is not None:
        options.subscriptions = subscriptions
    else:
        del options.subscriptions
    options.dictify.return_value = {}
    options.broker = MagicMock()
    return options


def _make_consumer(broker=None):
    """Return a mock consumer with the standard Moth interface."""
    consumer = MagicMock()
    consumer.broker = broker or MagicMock()
    consumer.newMessages = MagicMock(return_value=[])
    consumer.ack = MagicMock()
    consumer.close = MagicMock()
    consumer.metricsReport = MagicMock(return_value={
        'rxGoodCount': 0,
        'rxBadCount': 0,
        'rxByteCount': 0,
    })
    consumer.metricsReset = MagicMock()
    consumer.please_stop = MagicMock()
    return consumer


# ── __init__ ─────────────────────────────────────────────────────────────

class Test_init:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_init_with_subscriptions(self, mock_defaults, mock_factory):
        mock_consumer = _make_consumer()
        mock_factory.return_value = mock_consumer
        broker = MagicMock()
        subs = [{'broker': broker, 'exchange': 'xs_test'}]
        options = _make_options(subscriptions=subs)

        msg = Message(options)

        assert len(msg.consumers) == 1
        assert mock_factory.call_count == 1
        call_args = mock_factory.call_args[0][0]
        assert call_args['subscription_index'] == 0

    def test_init_no_subscriptions(self, caplog):
        options = _make_options(subscriptions=None)
        import logging
        with caplog.at_level(logging.CRITICAL):
            msg = Message(options)
        assert len(msg.consumers) == 0
        assert 'missing required subscription' in caplog.text


# ── gather ───────────────────────────────────────────────────────────────

class Test_gather:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_gather_returns_messages(self, mock_defaults, mock_factory):
        m1 = sarracenia.Message()
        m1['baseUrl'] = 'https://example.com'
        consumer = _make_consumer()
        consumer.newMessages.return_value = [m1]
        mock_factory.return_value = consumer

        subs = [{'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        msg = Message(options)

        ok, messages = msg.gather(100)
        assert ok is True
        assert len(messages) == 1
        assert messages[0]['baseUrl'] == 'https://example.com'

    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_gather_no_consumers(self, mock_defaults, mock_factory):
        """Returns (True, []) when the consumers attribute is absent."""
        subs = [{'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        mock_factory.return_value = _make_consumer()
        msg = Message(options)
        del msg.consumers

        ok, messages = msg.gather(100)
        assert ok is True
        assert messages == []

    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_gather_reconnects_disconnected_consumer(self, mock_defaults, mock_factory):
        """When a consumer lacks newMessages, gather tries to reconnect."""
        broken_consumer = MagicMock(spec=[])  # no newMessages attribute
        mock_factory.return_value = broken_consumer

        subs = [{'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        msg = Message(options)
        msg.consumers = [broken_consumer]

        mock_factory.reset_mock()
        new_consumer = _make_consumer()
        mock_factory.return_value = new_consumer

        ok, messages = msg.gather(100)
        assert ok is True
        assert mock_factory.call_count == 1


# ── ack ──────────────────────────────────────────────────────────────────

class Test_ack:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_ack_routes_to_correct_consumer(self, mock_defaults, mock_factory):
        broker_a = MagicMock()
        broker_b = MagicMock()
        consumer_a = _make_consumer(broker=broker_a)
        consumer_b = _make_consumer(broker=broker_b)

        subs = [{'broker': broker_a}, {'broker': broker_b}]
        options = _make_options(subscriptions=subs)
        mock_factory.side_effect = [consumer_a, consumer_b]
        msg_obj = Message(options)

        m = sarracenia.Message()
        m['ack_id'] = {'broker': broker_a}

        msg_obj.ack([m])

        consumer_a.ack.assert_called_once_with(m)
        consumer_b.ack.assert_not_called()

    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_ack_no_consumers(self, mock_defaults, mock_factory):
        """No error when consumers attribute is absent."""
        subs = [{'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        mock_factory.return_value = _make_consumer()
        msg_obj = Message(options)
        del msg_obj.consumers

        msg_obj.ack([sarracenia.Message()])


# ── metricsReport ────────────────────────────────────────────────────────

class Test_metricsReport:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_metrics_report_aggregates(self, mock_defaults, mock_factory):
        broker_a = MagicMock()
        broker_b = MagicMock()
        metrics_a = {'rxGoodCount': 10, 'rxBadCount': 1, 'rxByteCount': 5000}
        metrics_b = {'rxGoodCount': 20, 'rxBadCount': 2, 'rxByteCount': 8000}
        consumer_a = _make_consumer(broker=broker_a)
        consumer_a.metricsReport.return_value = metrics_a
        consumer_b = _make_consumer(broker=broker_b)
        consumer_b.metricsReport.return_value = metrics_b

        subs = [{'broker': broker_a}, {'broker': broker_b}]
        options = _make_options(subscriptions=subs)
        mock_factory.side_effect = [consumer_a, consumer_b]
        msg_obj = Message(options)

        report = msg_obj.metricsReport()
        assert str(broker_a) in report
        assert str(broker_b) in report
        assert report[str(broker_a)] == metrics_a
        assert report[str(broker_b)] == metrics_b


# ── on_stop ──────────────────────────────────────────────────────────────

class Test_on_stop:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_on_stop_closes_consumers(self, mock_defaults, mock_factory):
        consumer = _make_consumer()
        mock_factory.return_value = consumer
        subs = [{'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        msg_obj = Message(options)

        msg_obj.on_stop()
        consumer.close.assert_called_once()


# ── please_stop ──────────────────────────────────────────────────────────

class Test_please_stop:
    @patch('sarracenia.moth.Moth.subFactory')
    @patch('sarracenia.moth.default_options', return_value={})
    def test_please_stop_propagates(self, mock_defaults, mock_factory):
        consumer_a = _make_consumer()
        consumer_b = _make_consumer()
        subs = [{'broker': MagicMock()}, {'broker': MagicMock()}]
        options = _make_options(subscriptions=subs)
        mock_factory.side_effect = [consumer_a, consumer_b]
        msg_obj = Message(options)

        msg_obj.please_stop()

        consumer_a.please_stop.assert_called_once()
        consumer_b.please_stop.assert_called_once()
        assert msg_obj.stop_requested is True