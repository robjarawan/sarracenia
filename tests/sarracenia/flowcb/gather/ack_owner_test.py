import json
from unittest.mock import Mock

import amqp
import pytest

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.flowcb.gather.message import Message as Gather


def make_gather(backend, count):
    options = no_file_config()
    options.component = 'subscribe'
    options.config = 'ack_owner'
    options.no = 1
    lines = ['broker {}://guest:guest@127.0.0.1/'.format(backend), 'exchange fixture']
    for index in range(count):
        lines.extend(['queueName ack-owner-{}'.format(index), 'subtopic item{}'.format(index)])
    for number, line in enumerate(lines, 1):
        options.parse_line('subscribe', 'ack_owner', 'ack_owner.conf', number, line)
    gather = Gather(options)
    assert len(gather.consumers) == count

    for index, consumer in enumerate(gather.consumers):
        # Only the transport boundary is replaced. Each real decoder and ack
        # method uses its own connection and the same channel/delivery numbers.
        consumer.broker = '127.0.0.1:5672//'
        consumer.connection_id = 'connection-{}'.format(index)
        consumer.channel = Mock(channel_id=1)
        raw = amqp.Message(json.dumps({
            'baseUrl': 'file:/fixture',
            'relPath': 'item{}.dat'.format(index),
            'pubTime': sarracenia.nowstr(),
            'size': 7,
            'identity': {'method': 'arbitrary', 'value': 'fixture'},
        }), content_type='application/json')
        raw.channel = consumer.channel
        raw.delivery_info = {
            'delivery_tag': 1,
            'exchange': 'fixture',
            'routing_key': 'v03.item{}'.format(index),
        }
        message = consumer._msgRawToDict(raw)
        assert message is not None
        consumer.newMessages = Mock(return_value=[message])
    return gather


@pytest.mark.parametrize('backend', ['amqp', 'amqpconsumer'])
@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('reverse', [False, True])
def test_ack_reaches_each_owning_connection(backend, count, reverse):
    """Same-broker consumers must not consume each other's acknowledgement IDs."""
    gather = make_gather(backend, count)

    if reverse:
        gather.consumers.reverse()
    _, messages = gather.gather(10)
    assert len(messages) == count
    gather.ack(messages)

    for consumer in gather.consumers:
        consumer.channel.basic_ack.assert_called_once_with(1)
    assert all('ack_id' not in message for message in messages)


@pytest.mark.parametrize('changed', ['connection', 'channel'])
def test_stale_delivery_is_not_acked_on_a_replacement(changed):
    gather = make_gather('amqp', 1)
    _, messages = gather.gather(10)
    consumer = gather.consumers[0]
    if changed == 'connection':
        consumer.connection_id = 'replacement-connection'
    else:
        consumer.channel.channel_id = 2
    gather.ack(messages)
    consumer.channel.basic_ack.assert_not_called()


def test_ack_transport_error_is_not_retried():
    gather = make_gather('amqp', 1)
    _, messages = gather.gather(10)
    consumer = gather.consumers[0]
    consumer.channel.basic_ack.side_effect = OSError('synthetic connection failure')
    consumer.close = Mock()
    gather.ack(messages)
    consumer.channel.basic_ack.assert_called_once_with(1)
    consumer.close.assert_called_once_with()
    assert 'ack_id' not in messages[0]
