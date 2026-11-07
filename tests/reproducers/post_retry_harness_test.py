"""Failure-path checks for the human reproduction's owned resources."""

import importlib.util
from pathlib import Path
import signal
from unittest.mock import Mock, patch

import pytest


def load_recipe():
    path = Path(__file__).with_name('post_retry_same_name.py')
    spec = importlib.util.spec_from_file_location('post_retry_recipe', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failure', [KeyboardInterrupt(), OSError('broken pipe')])
def test_worker_interruption_is_reaped(failure):
    recipe = load_recipe()
    process = Mock(pid=12345)
    process.poll.return_value = None
    process.communicate.side_effect = [failure, ('', None)]
    with patch.object(recipe.subprocess, 'Popen', return_value=process), \
            patch.object(recipe.os, 'killpg') as kill:
        with pytest.raises(type(failure)):
            recipe.run_foreground(['sr3', 'foreground', 'flow/probe'], {})
    kill.assert_called_once_with(12345, signal.SIGTERM)
    assert process.communicate.call_count == 2


def test_partial_broker_setup_closes_connection(tmp_path):
    recipe = load_recipe()
    connection = Mock()
    connection.channel.side_effect = OSError('channel setup failed')
    with patch.object(recipe.amqp, 'Connection', return_value=connection):
        with pytest.raises(OSError, match='channel setup failed'):
            recipe.run_case(tmp_path, False)
    connection.close.assert_called_once_with()


def test_cleanup_attempts_every_release_without_masking_primary():
    recipe = load_recipe()
    broken = Mock(side_effect=OSError('release failed'))
    remaining = Mock()
    with pytest.raises(ValueError, match='primary failure'):
        try:
            raise ValueError('primary failure')
        finally:
            recipe.close_resources([broken, remaining])
    remaining.assert_called_once_with()


def test_cleanup_failure_prevents_success():
    recipe = load_recipe()
    with pytest.raises(OSError, match='release failed'):
        recipe.close_resources([Mock(side_effect=OSError('release failed'))])
