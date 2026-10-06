import collections
import logging
from unittest.mock import MagicMock, patch

import pytest

from sarracenia.moth.mqtt import MQTT


class Clock:

    def __init__(self):
        self.now = 100.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        if len(self.waits) > 2:
            raise AssertionError('MQTT.close exceeded its configured deadline')


def make_publisher(timeout, pending=(17,)):
    mqtt = MQTT.__new__(MQTT)
    mqtt.client = MagicMock()
    mqtt.client.is_connected.return_value = True
    mqtt.connected = True
    mqtt.is_subscriber = False
    mqtt.pending_publishes = collections.deque(pending)
    mqtt.unexpected_publishes = collections.deque()
    mqtt.metrics = {}
    mqtt.o = {'timeout': timeout}
    return mqtt


def test_close_stops_at_deadline_and_reports_unresolved_mid(caplog):
    mqtt = make_publisher(0.25)
    clock = Clock()

    with (
        caplog.at_level(logging.ERROR),
        patch('sarracenia.moth.mqtt.time.monotonic', side_effect=clock.monotonic),
        patch('sarracenia.moth.mqtt.time.sleep', side_effect=clock.sleep),
    ):
        mqtt.close()

    assert clock.waits == pytest.approx([0.1, 0.15])
    assert list(mqtt.pending_publishes) == [17]
    assert mqtt.metrics['txBadCount'] == 1
    assert 'unresolved MIDs: [17]' in caplog.text
    mqtt.client.disconnect.assert_called_once_with()
    mqtt.client.loop_stop.assert_called_once_with()
    assert mqtt.connected is False


def test_close_preserves_normal_completion_drain():
    mqtt = make_publisher(1)
    clock = Clock()

    def acknowledge(seconds):
        clock.sleep(seconds)
        mqtt.pending_publishes.clear()

    with (
        patch('sarracenia.moth.mqtt.time.monotonic', side_effect=clock.monotonic),
        patch('sarracenia.moth.mqtt.time.sleep', side_effect=acknowledge),
    ):
        mqtt.close()

    assert clock.waits == [0.1]
    assert mqtt.metrics == {}
    mqtt.client.disconnect.assert_called_once_with()
    mqtt.client.loop_stop.assert_called_once_with()
    assert mqtt.connected is False


def test_close_honours_zero_timeout_without_sleeping():
    mqtt = make_publisher(0)

    with (
        patch('sarracenia.moth.mqtt.time.monotonic', return_value=100.0),
        patch(
            'sarracenia.moth.mqtt.time.sleep',
            side_effect=AssertionError('timeout=0 must not sleep'),
        ) as sleep,
    ):
        mqtt.close()

    sleep.assert_not_called()
    assert list(mqtt.pending_publishes) == [17]
    assert mqtt.metrics['txBadCount'] == 1
    mqtt.client.disconnect.assert_called_once_with()
    mqtt.client.loop_stop.assert_called_once_with()
