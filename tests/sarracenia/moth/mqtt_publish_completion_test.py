import collections
import inspect
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client
import pytest

import sarracenia
from sarracenia.config.credentials import Credential
from sarracenia.moth import default_options
from sarracenia.moth.mqtt import MQTT


def _message():
    message = sarracenia.Message()
    message.update(
        baseUrl='file:/local-fixture',
        relPath='data.bin',
        pubTime=sarracenia.nowstr(),
        size=4,
        identity={'method': 'sha512', 'value': 'fixture'},
    )
    return message


def _publisher():
    options = default_options()
    broker = Credential('mqtt://localhost/')
    destination = {
        'broker': broker,
        'exchange': ['fixture'],
        'format': 'v03',
        'topicPrefix': ['v03'],
    }
    options.update({
        'broker': broker,
        'exchange': destination['exchange'],
        'format': 'v03',
        'publisher_index': 0,
        'publishers': [destination],
        'qos': 1,
        'topicPrefix': ['v03'],
    })
    publisher = MQTT(options, False)
    publisher.connected = True
    publisher.pending_publishes = collections.deque()
    publisher.unexpected_publishes = collections.deque()
    publisher.client = Mock()
    return publisher


def _registration_line():
    lines, start = inspect.getsourcelines(MQTT.putNewMessage)
    for text_to_find in (
        'with self.publish_lock',
        'self.pending_publishes.append(info.mid)',
    ):
        for offset, line in enumerate(lines):
            if text_to_find in line:
                return start + offset
    raise AssertionError('publish registration line was not found')


@pytest.mark.parametrize('callback_timing', ['before', 'during', 'after'])
def test_publish_completion_is_reconciled_atomically(callback_timing):
    publisher = _publisher()
    callback = MQTT._MQTT__pub_on_publish
    callback_ready = threading.Event()
    callback_finished = threading.Event()

    def publish(**kwargs):
        if callback_timing == 'before':
            callback(publisher.client, publisher, 7, 0)
        return SimpleNamespace(
            rc=paho.mqtt.client.MQTT_ERR_SUCCESS,
            mid=7,
        )

    publisher.client.publish.side_effect = publish

    def completion_thread():
        assert callback_ready.wait(3)
        callback(publisher.client, publisher, 7, 0)
        callback_finished.set()

    thread = None
    target_line = _registration_line()

    def trace(frame, event, arg):
        if (
            frame.f_code is MQTT.putNewMessage.__code__
            and event == 'line'
            and frame.f_lineno == target_line
        ):
            callback_ready.set()
            assert callback_finished.wait(3)
        return trace

    if callback_timing == 'during':
        thread = threading.Thread(target=completion_thread)
        thread.start()
        sys.settrace(trace)

    try:
        assert publisher.putNewMessage(_message())
    finally:
        sys.settrace(None)
        if thread:
            thread.join(3)
            assert not thread.is_alive()

    if callback_timing == 'after':
        callback(publisher.client, publisher, 7, 0)

    assert not publisher.pending_publishes
    assert not publisher.unexpected_publishes
