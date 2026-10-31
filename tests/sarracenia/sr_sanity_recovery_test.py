"""Sanity must finish stray termination before replacing missing instances."""

from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

import sarracenia.sr as sr


@pytest.fixture
def recovery(monkeypatch):
    state = sr.sr_GlobalState.__new__(sr.sr_GlobalState)
    state.leftovers = []
    state._action_all_configs = True
    state.flux = {}
    state.please_stop = False
    state.options = SimpleNamespace(dry_run=False)
    state.filtered_configurations = ['flow/recovery']
    state.configs = {'flow': {'recovery': {'status': 'missing', 'instances': 1}}}
    state.states = {'flow': {'recovery': {'hung_instances': [], 'instance_pids': {}}}}
    state.missing = [['flow', 'recovery', 1]]
    state.strays = {123: 'python3 instance.py --no 1 start flow/recovery'}
    state.procs = {123: {'create_time': 10.0}}
    events = []
    process = Mock()
    process.create_time.return_value = 10.0
    process.is_running.side_effect = lambda: 123 in state.procs
    process.status.return_value = psutil.STATUS_RUNNING

    def kill():
        events.append('kill')
        state.procs.pop(123, None)

    process.kill.side_effect = kill
    monkeypatch.setattr(sr.psutil, 'Process', Mock(return_value=process))
    monkeypatch.setattr(sr, 'signal_pid', lambda pid, sig: events.append(sig))
    state._tag_sanity = Mock()
    state._find_component_path = Mock(return_value='instance.py')
    state._find_missing_instances = Mock()
    state._clean_missing_proc_state = Mock()
    state._read_states = Mock()
    state._resolve = Mock()
    state._read_procs = Mock()
    state._start_missing = Mock(side_effect=lambda: events.append('start'))
    return state, process, events


def test_sanity_kills_stray_before_starting_replacement(recovery):
    state, process, events = recovery

    state.sanity()

    assert events == ['kill', 'start']
    assert state.missing == [['flow', 'recovery', 1]]
    state._tag_sanity.assert_called_with(ending=True)


def test_sanity_preserves_existing_replacement(recovery):
    state, process, events = recovery
    state.configs['flow']['recovery']['status'] = 'running'
    state.states['flow']['recovery']['instance_pids'] = {1: 456}
    state.missing = []
    state.procs[456] = {'create_time': 20.0}

    state.sanity()

    assert events == ['kill']
    assert state.states['flow']['recovery']['instance_pids'] == {1: 456}
    state._start_missing.assert_not_called()


def test_sanity_does_not_start_when_stray_cannot_be_killed(recovery):
    state, process, events = recovery
    process.kill.side_effect = psutil.AccessDenied(pid=123)

    state.sanity()

    state._start_missing.assert_not_called()
    state._tag_sanity.assert_called_with(ending=True)


def test_sanity_does_not_start_until_stray_has_exited(recovery, monkeypatch):
    state, process, events = recovery
    process.kill.side_effect = lambda: events.append('kill')
    times = iter([0.0, 6.0])
    monkeypatch.setattr(sr.time, 'monotonic', lambda: next(times))

    state.sanity()

    assert events == ['kill']
    state._start_missing.assert_not_called()
    state._tag_sanity.assert_called_with(ending=True)


def test_sanity_does_not_treat_discovery_omission_as_process_exit(recovery, monkeypatch):
    state, process, events = recovery
    process.is_running.side_effect = lambda: True
    times = iter([0.0, 6.0])
    monkeypatch.setattr(sr.time, 'monotonic', lambda: next(times))

    state.sanity()

    state._start_missing.assert_not_called()
    state._tag_sanity.assert_called_with(ending=True)


@pytest.mark.parametrize('status', [psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD])
def test_sanity_accepts_terminal_process_states(recovery, status):
    state, process, events = recovery
    process.kill.side_effect = lambda: events.append('kill')
    process.status.return_value = status

    state.sanity()

    assert events == ['kill', 'start']


def test_sanity_does_not_start_when_exit_cannot_be_confirmed(recovery):
    state, process, events = recovery
    process.kill.side_effect = lambda: events.append('kill')
    process.status.side_effect = psutil.AccessDenied(pid=123)

    state.sanity()

    state._start_missing.assert_not_called()
    state._tag_sanity.assert_called_with(ending=True)


def test_sanity_does_not_kill_a_reused_pid(recovery):
    state, process, events = recovery
    process.create_time.return_value = 30.0
    state._read_procs.side_effect = lambda: state.procs.update({123: {'create_time': 30.0}})

    state.sanity()

    process.kill.assert_not_called()
    state._start_missing.assert_called_once()


def test_sanity_dry_run_does_not_signal_or_start(recovery):
    state, process, events = recovery
    state.options.dry_run = True

    state.sanity()

    assert events == []
    process.kill.assert_not_called()
    state._start_missing.assert_not_called()
