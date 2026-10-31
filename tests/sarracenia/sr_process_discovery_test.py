"""Optional resource accounting must not hide a live SR3 instance."""

from collections import namedtuple
from unittest.mock import Mock

import pytest

import sarracenia.sr as sr
from sarracenia.sr import sr_GlobalState


Memory = namedtuple('Memory', 'rss vms uss')
BasicMemory = namedtuple('BasicMemory', 'rss vms')
Cpu = namedtuple('Cpu', 'user system')


@pytest.mark.parametrize('full_memory', [None, Memory(100, 200, 50)])
@pytest.mark.parametrize('basic_memory', [None, BasicMemory(100, 200)])
@pytest.mark.parametrize('cpu', [None, Cpu(1.0, 2.0)])
def test_read_procs_keeps_identity_without_optional_statistics(
        monkeypatch, full_memory, basic_memory, cpu):
    state = sr_GlobalState.__new__(sr_GlobalState)
    process = Mock()
    statistics = {
        'pid': 123, 'username': 'srproof', 'name': 'python3',
        'cmdline': ['python3', 'instance.py', '--no', '1', 'start', 'flow/recovery'],
        'create_time': 1000.0, 'memory_full_info': full_memory,
        'memory_info': basic_memory, 'cpu_times': cpu,
    }
    process.as_dict.side_effect = lambda attributes: {key: statistics[key] for key in attributes}
    monkeypatch.setattr('sarracenia.sr.getpass.getuser', lambda: 'srproof')
    monkeypatch.setattr('sarracenia.sr.psutil.process_iter', lambda: iter([process]))
    monkeypatch.setitem(sr.features['process'], 'present', True)

    state._read_procs()

    assert set(state.procs) == {123}
    observed = state.procs[123]
    assert observed['claimed'] is False
    assert observed['create_time'] == 1000.0
    memory = full_memory or basic_memory
    assert observed['memory'] == {
        'rss': memory.rss if memory else 0,
        'vms': memory.vms if memory else 0,
        'uss': full_memory.uss if full_memory else 0,
    }
    assert observed['cpu'] == {
        'user': cpu.user if cpu else 0.0,
        'system': cpu.system if cpu else 0.0,
    }
