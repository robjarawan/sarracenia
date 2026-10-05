"""Installed-package maintenance/API checks using only a disposable local RabbitMQ."""

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


EXPECTED_COUNT = 5
BROKER = 'amqp://guest:guest@127.0.0.1/'  # Disposable RabbitMQ's built-in local test account.


def isolate(root):
    """Keep both Python API and CLI calls away from the invoking user's configuration."""
    for kind in ('CONFIG', 'CACHE', 'DATA'):
        path = root / kind.lower()
        path.mkdir(exist_ok=True)
        os.environ['XDG_' + kind + '_HOME'] = str(path)
    for name in ('SR_DEV_APPNAME', 'SARRA_LIB', 'SARRAC_LIB', 'PYTHONPATH'):
        os.environ.pop(name, None)
    os.chdir(root)


def cli(*args):
    result = subprocess.run(['sr3'] + list(args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
    print(result.stdout, end='', flush=True)
    assert result.returncode == 0, 'sr3 command failed: ' + ' '.join(args)
    return result.stdout


def configuration(root, component, name):
    from sarracenia.config import no_file_config

    cfg = no_file_config()
    cfg.component, cfg.config, cfg.no = component, name, 1
    cfg.action = 'foreground'
    state = root / 'cache' / 'sr3' / component / name
    state.mkdir(parents=True, exist_ok=True)
    cfg.cfg_run_dir = str(state)
    cfg.metricsFilename = str(state / 'metrics.json')
    cfg.pid_filename = str(state / 'instance.pid')
    cfg.novipFilename = str(state / 'novip')
    cfg.parse_file(str(root / 'config' / 'sr3' / component / (name + '.conf')), component=component)
    cfg.finalize(component, name)
    return cfg


def flow_worker(root):
    from sarracenia.flow.subscribe import Subscribe

    Subscribe(configuration(root, 'subscribe', 'maintenance_flow')).run()


def fixtures(root, exchange, queues):
    source, destination = root / 'source', root / 'destination'
    source.mkdir()
    destination.mkdir()
    expected = {}
    for index in range(EXPECTED_COUNT):
        name = 'message-{}.txt'.format(index)
        payload = 'maintenance fixture {}\n'.format(index).encode('ascii')
        (source / name).write_bytes(payload)
        expected[name] = payload

    common = ('broker ' + BROKER + '\nexchange ' + exchange + '\ntopicPrefix v03\n'
              'durable True\nauto_delete False\nexpire 0\ninstances 1\nbatch 5\ntimeout 15\n'
              'messageAgeMax 0\nmessageCountMax 5\nsleep 0.1\nretry_ttl 0\n'
              'inflight None\naccelThreshold 0\npermCopy False\nmirror False\n'
              'directory ' + str(destination) + '\naccept .*\n')
    configs = {
        'subscribe/maintenance_moth': common + 'queueName ' + queues[0] + '\ndownload False\nsubtopic #\n',
        'subscribe/maintenance_flow': common + 'queueName ' + queues[1] + '\ndownload True\nsubtopic #\n',
        'post/maintenance_post': ('post_broker ' + BROKER + '\npost_exchange ' + exchange + '\n'
                                  'post_baseUrl file:' + str(source) + '\npost_baseDir ' + str(source) + '\n'
                                  'post_topicPrefix v03\ndurable True\nauto_delete False\n'
                                  'messageAgeMax 0\ntimeout 15\n'),
    }
    for name, content in configs.items():
        path = root / 'fixtures' / (name + '.conf')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        cli('add', str(path))
        installed = root / 'config' / 'sr3' / (name + '.conf')
        assert installed.read_text() == content, 'sr3 add did not preserve ' + name
        component, config_name = name.split('/')
        configuration(root, component, config_name)
    return list(configs), expected


def publish(root, count, expected):
    import sarracenia
    from sarracenia.moth import Moth

    cfg = configuration(root, 'post', 'maintenance_post')
    props = cfg.dictify()
    props.update(cfg.publishers[0])
    props['publisher_index'] = 0
    publisher = Moth.pubFactory(props)
    try:
        for name in list(expected)[:count]:
            message = sarracenia.Message()
            message.update(baseUrl='file:' + str(root / 'source'), relPath=name,
                           pubTime=sarracenia.nowstr(), size=len(expected[name]),
                           identity={'method': 'sha512', 'value': hashlib.sha512(expected[name]).hexdigest()})
            assert publisher.putNewMessage(message), 'publication failed for ' + name
    finally:
        publisher.close()


def consume(root, expected):
    from sarracenia.moth import Moth

    props = configuration(root, 'subscribe', 'maintenance_moth').dictify()
    props['subscription_index'] = 0
    consumer = Moth.subFactory(props)
    received = []
    deadline = time.monotonic() + 10
    try:
        while len(received) < EXPECTED_COUNT and time.monotonic() < deadline:
            message = consumer.getNewMessage()
            if message is None:
                time.sleep(0.05)
                continue
            name = message['relPath']
            assert name in expected, 'unexpected product: ' + name
            assert message['size'] == len(expected[name]), 'incorrect size for ' + name
            assert message['identity']['value'] == hashlib.sha512(expected[name]).hexdigest()
            assert consumer.ack(message), 'acknowledgement failed for ' + name
            received.append(name)
        assert sorted(received) == sorted(expected), 'expected five distinct deliveries, got ' + repr(received)
        assert consumer.getNewMessage() is None, 'unexpected extra delivery'
    finally:
        consumer.close()
    return received


def queue_state(connection, name):
    from amqp.exceptions import NotFound

    channel = connection.channel()
    try:
        return channel.queue_declare(name, passive=True)
    except NotFound:
        return None
    finally:
        if channel.is_open:
            channel.close()


def cleanup_resources(connection, queues, exchange):
    """Attempt every fixture cleanup operation, then raise the first failure."""
    failures = []
    channel = None
    try:
        channel = connection.channel()
    except Exception as error:
        failures.append(('open cleanup channel', error, error.__traceback__))

    if channel is not None:
        for queue in queues:
            try:
                channel.queue_delete(queue)
            except Exception as error:
                failures.append(('delete queue ' + queue, error,
                                 error.__traceback__))
        try:
            channel.exchange_delete(exchange)
        except Exception as error:
            failures.append(('delete exchange ' + exchange, error,
                             error.__traceback__))
        try:
            channel.close()
        except Exception as error:
            failures.append(('close cleanup channel', error,
                             error.__traceback__))

    try:
        connection.close()
    except Exception as error:
        failures.append(('close cleanup connection', error,
                         error.__traceback__))

    for operation, error, traceback in failures:
        logging.error('fixture cleanup failed while trying to %s: %s',
                      operation, error,
                      exc_info=(type(error), error, traceback))

    if failures:
        error = failures[0][1]
        raise error.with_traceback(failures[0][2])


def run(root, count, legacy_count):
    import amqp
    import sarracenia

    checkout = Path(__file__).resolve().parents[2]
    module = Path(sarracenia.__file__).resolve()
    assert checkout not in module.parents, 'run against the installed package, outside the checkout'
    print(json.dumps({'module': str(module), 'python': sys.version}), flush=True)
    prefix = 'sr-maintenance-' + uuid.uuid4().hex
    queues = [prefix + '-moth', prefix + '-flow']
    connection = amqp.Connection('127.0.0.1', userid='guest', password='guest',
                                 virtual_host='/', connect_timeout=15, read_timeout=15, write_timeout=15)
    configs = []
    primary_error = None
    primary_traceback = None
    result = None
    try:
        connection.connect()
        configs, expected = fixtures(root, prefix, queues)
        cli('--dangerWillRobinson', str(len(configs)), 'declare', *configs)
        for queue in queues:
            state = queue_state(connection, queue)
            assert state is not None and state.message_count == 0, 'declare failed for ' + queue
        publish(root, count, expected)
        received = consume(root, expected)
        for queue in queues:
            state = queue_state(connection, queue)
            expected_count = 0 if queue == queues[0] else EXPECTED_COUNT
            assert state.message_count == expected_count, 'unexpected queued count for ' + queue
        worker = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--flow-worker', str(root)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=60)
        print(worker.stdout, end='', flush=True)
        assert worker.returncode == 0, 'Flow API worker failed'
        actual = {path.name: path.read_bytes() for path in (root / 'destination').iterdir() if path.is_file()}
        assert actual == expected, 'Flow did not download exactly the five complete fixture files'
        assert queue_state(connection, queues[1]).message_count == 0, 'Flow left deliveries queued'
        cli('stop', *configs)
        status = cli('status', *configs)
        selected = sum('stop' in line for line in status.splitlines()) if legacy_count else len(configs)
        cli('--dangerWillRobinson', str(selected), 'cleanup', *configs)
        for queue in queues:
            assert queue_state(connection, queue) is None, 'cleanup left queue ' + queue
        cli('--dangerWillRobinson', str(len(configs)), 'remove', *configs)
        assert not list((root / 'config' / 'sr3').glob('*/*.conf')), 'remove left configurations behind'
        result = {'result': 'PASS', 'received': received,
                  'downloaded': sorted(actual),
                  'configurations_removed': len(configs),
                  'queues_removed': len(queues)}
    except BaseException as error:
        primary_error = error
        primary_traceback = error.__traceback__
    finally:
        # Only fixture-owned resources are touched, including on assertion/timeout failure.
        try:
            cleanup_resources(connection, queues, prefix)
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            logging.error('cleanup failed after the fixture had already failed: %s',
                          cleanup_error)

    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)

    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flow-worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--publish-count', type=int, choices=range(0, EXPECTED_COUNT + 1), default=EXPECTED_COUNT,
                        help='publish fewer than five only to verify the missing-message failure control')
    parser.add_argument('--legacy-cleanup-count', action='store_true',
                        help='reproduce the old stopped-row cleanup count; expected to fail')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.flow_worker:
        isolate(args.flow_worker)
        flow_worker(args.flow_worker)
        return
    with tempfile.TemporaryDirectory(prefix='sr-maintenance-') as temporary:
        root = Path(temporary)
        isolate(root)
        run(root, args.publish_count, args.legacy_cleanup_count)


if __name__ == '__main__':
    main()
