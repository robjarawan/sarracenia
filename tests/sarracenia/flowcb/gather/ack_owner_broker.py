"""Opt-in acknowledgement proof against a disposable RabbitMQ on loopback.

Run with an installed checkout: python tests/sarracenia/flowcb/gather/ack_owner_broker.py
Only synthetic messages and uniquely named resources created here are used.
"""

import json
import time
import uuid

import amqp

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.flowcb.gather.message import Message as Gather


def run_case(backend, count):
    prefix = 'sr3-ack-owner-' + uuid.uuid4().hex
    queues = [prefix + '-{}'.format(index) for index in range(count)]
    options = no_file_config()
    options.component = 'subscribe'
    options.config = 'ack_owner'
    options.no = 1
    lines = [
        'broker {}://guest:guest@127.0.0.1/'.format(backend),
        'exchange ' + prefix, 'prefetch 1', 'durable True', 'auto_delete False',
    ]
    for index, queue in enumerate(queues):
        lines.extend(['queueName ' + queue, 'subtopic item{}'.format(index)])
    for number, line in enumerate(lines, 1):
        options.parse_line('subscribe', 'ack_owner', 'ack_owner.conf', number, line)

    connection = amqp.Connection('127.0.0.1', userid='guest', password='guest',
                                 virtual_host='/', connect_timeout=5)
    connection.connect()
    channel = connection.channel()
    gather = None
    try:
        channel.exchange_declare(prefix, type='topic', durable=False)
        gather = Gather(options)
        assert len(gather.consumers) == count
        for consumer in gather.consumers:
            consumer.getSetup()
            assert consumer.connection and consumer.connection.connected
        for index in range(count):
            body = {
                'baseUrl': 'file:/fixture', 'relPath': 'item{}.dat'.format(index),
                'pubTime': sarracenia.nowstr(), 'size': 7,
                'identity': {'method': 'arbitrary', 'value': 'fixture'},
            }
            channel.basic_publish(amqp.Message(json.dumps(body), content_type='application/json'),
                                  exchange=prefix, routing_key='v03.item{}'.format(index))
        channel.queue_declare(queues[-1], passive=True)
        messages = []
        deadline = time.monotonic() + 5
        while len(messages) < count and time.monotonic() < deadline:
            messages.extend(gather.gather(count)[1])
        assert len(messages) == count, 'fixture did not receive every published message'
        gather.ack(messages)
        for consumer, queue in zip(gather.consumers, queues):
            consumer.management_channel.queue_declare(queue, passive=True)
        gather.on_stop()
        gather = None

        redelivered = []
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for index, queue in enumerate(queues):
                raw = channel.basic_get(queue)
                if raw is not None:
                    redelivered.append(index)
                    channel.basic_ack(raw.delivery_info['delivery_tag'])
            time.sleep(0.02)
        return {'backend': backend, 'subscriptions': count,
                'gathered': len(messages), 'redelivered_after_ack_and_close': redelivered}
    finally:
        try:
            if gather is not None:
                gather.on_stop()
        finally:
            try:
                for queue in queues:
                    channel.queue_delete(queue)
                channel.exchange_delete(prefix)
            finally:
                connection.close()


if __name__ == '__main__':
    results = [run_case(backend, count)
               for backend in ('amqp', 'amqpconsumer') for count in (1, 2)]
    print(json.dumps({'imported_source': sarracenia.__file__, 'cases': results}, indent=2))
    assert all(not case['redelivered_after_ack_and_close'] for case in results), \
        'a successfully acknowledged delivery was redelivered'
