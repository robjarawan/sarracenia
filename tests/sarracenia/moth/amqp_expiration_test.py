"""Regression tests for AMQP publication expiry."""

from types import SimpleNamespace
from unittest.mock import Mock

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.config.credentials import Credential
from sarracenia.config.publisher import Publisher
from sarracenia.flowcb.post.message import Message as Poster
from sarracenia.moth import default_options
from sarracenia.moth.amqp import AMQP


def _message():
    message = sarracenia.Message()
    message.update(
        baseUrl='file:/test',
        relPath='item.bin',
        pubTime=sarracenia.nowstr(),
        size=1,
        identity={'method': 'arbitrary', 'value': 'test'},
    )
    return message


def _publisher_options(tmp_path):
    options = no_file_config()
    options.component = 'post'
    options.config = 'expiry'
    options.post_broker = Credential('amqp://guest:guest@127.0.0.1/')
    options.post_exchange = 'sr-expiry-test'
    options.post_baseUrl = 'file:' + str(tmp_path)
    options.messageAgeMax = 0
    return options


def _published_message(message_age_max):
    broker = Credential('amqp://guest:guest@127.0.0.1/')
    configured_publisher = {
        'baseDir': '/test',
        'baseUrl': 'file:/test',
        'broker': broker,
        'exchange': ['sr-expiry-test'],
        'format': 'v03',
        'topicPrefix': ['v03'],
    }
    properties = default_options()
    properties.update({
        'broker': broker,
        'exchange': ['sr-expiry-test'],
        'messageAgeMax': message_age_max,
        'persistent': True,
        'publisher_index': 0,
        'publishers': [configured_publisher],
    })
    publisher = AMQP(properties, is_subscriber=False)
    publisher.connection = SimpleNamespace(connected=True)
    publisher.channel = SimpleNamespace(
        is_open=True,
        basic_publish=Mock(),
        tx_commit=Mock(),
    )

    assert publisher.putNewMessage(_message())
    return publisher.channel.basic_publish.call_args.args[0]


def test_post_message_age_max_reaches_publisher(tmp_path):
    options = _publisher_options(tmp_path)
    options.parse_line(
        'post',
        'expiry',
        'post/expiry.conf',
        1,
        'post_messageAgeMax 60',
    )

    options.publishers = [Publisher(options)]
    poster = Poster(options)

    assert poster.posters[0].o['messageAgeMax'] == 60


def test_positive_message_age_uses_amqp_expiration():
    wire_message = _published_message(60)

    assert wire_message.properties['expiration'] == '60000'
    assert 'expire' not in wire_message.properties


def test_zero_message_age_omits_amqp_expiration():
    wire_message = _published_message(0)

    assert 'expiration' not in wire_message.properties
    assert 'expire' not in wire_message.properties
