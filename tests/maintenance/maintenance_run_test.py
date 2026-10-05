from types import SimpleNamespace

import amqp
import pytest
import sarracenia

from tests.maintenance import run as maintenance


class BodyFailure(Exception):
    pass


class CleanupFailure(Exception):
    pass


class FaultChannel:

    def __init__(self, fault):
        self.fault = fault

    def queue_delete(self, queue):
        if self.fault == 'queue_delete':
            raise CleanupFailure('queue deletion failed')

    def exchange_delete(self, exchange):
        if self.fault == 'exchange_delete':
            raise CleanupFailure('exchange deletion failed')

    def close(self):
        if self.fault == 'channel_close':
            raise CleanupFailure('channel close failed')


class FaultConnection:

    def __init__(self, fault):
        self.fault = fault
        self.close_calls = 0

    def connect(self):
        pass

    def channel(self):
        if self.fault == 'channel_acquisition':
            raise CleanupFailure('channel acquisition failed')
        return FaultChannel(self.fault)

    def close(self):
        self.close_calls += 1
        if self.fault == 'connection_close':
            raise CleanupFailure('connection close failed')


FAULTS = [
    'channel_acquisition',
    'queue_delete',
    'exchange_delete',
    'channel_close',
    'connection_close',
]


def prepare(monkeypatch, tmp_path, fault):
    connection = FaultConnection(fault)
    monkeypatch.setattr(amqp, 'Connection', lambda *args, **kwargs: connection)
    monkeypatch.setattr(sarracenia, '__file__',
                        str(tmp_path / 'installed' / 'sarracenia' / '__init__.py'))
    return connection


@pytest.mark.parametrize('fault', FAULTS)
def test_cleanup_preserves_body_failure_and_closes_connection(
        monkeypatch, tmp_path, fault):
    connection = prepare(monkeypatch, tmp_path, fault)

    def fail_body(root, exchange, queues):
        raise BodyFailure('fixture body failed')

    monkeypatch.setattr(maintenance, 'fixtures', fail_body)

    with pytest.raises(BodyFailure, match='fixture body failed'):
        maintenance.run(tmp_path, maintenance.EXPECTED_COUNT, False)

    assert connection.close_calls == 1


@pytest.mark.parametrize('fault', FAULTS)
def test_cleanup_only_failure_is_fatal_without_printing_pass(
        monkeypatch, tmp_path, capsys, fault):
    connection = prepare(monkeypatch, tmp_path, fault)
    (tmp_path / 'destination').mkdir()
    (tmp_path / 'config' / 'sr3').mkdir(parents=True)
    configs = [
        'subscribe/maintenance_moth',
        'subscribe/maintenance_flow',
        'post/maintenance_post',
    ]

    monkeypatch.setattr(
        maintenance, 'fixtures',
        lambda root, exchange, queues: (configs, {}))
    monkeypatch.setattr(maintenance, 'publish', lambda root, count, expected: None)
    monkeypatch.setattr(maintenance, 'consume', lambda root, expected: [])
    monkeypatch.setattr(
        maintenance.subprocess, 'run',
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=''))

    def fake_cli(*args):
        if args[0] == 'status':
            return '\n'.join([
                'subscribe/maintenance_moth stop',
                'subscribe/maintenance_flow stop',
                'post/maintenance_post inte',
            ])
        return ''

    states = iter([
        SimpleNamespace(message_count=0),
        SimpleNamespace(message_count=0),
        SimpleNamespace(message_count=0),
        SimpleNamespace(message_count=maintenance.EXPECTED_COUNT),
        SimpleNamespace(message_count=0),
        None,
        None,
    ])
    monkeypatch.setattr(maintenance, 'cli', fake_cli)
    monkeypatch.setattr(maintenance, 'queue_state',
                        lambda active_connection, queue: next(states))

    with pytest.raises(CleanupFailure):
        maintenance.run(tmp_path, maintenance.EXPECTED_COUNT, False)

    assert connection.close_calls == 1
    assert '"result": "PASS"' not in capsys.readouterr().out
