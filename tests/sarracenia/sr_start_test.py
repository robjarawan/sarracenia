import multiprocessing
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

from sarracenia.sr import sr_GlobalState


def _run_start(cache_dir, component_script, config_name, result):
    manager = object.__new__(sr_GlobalState)
    options = SimpleNamespace(
        instances=1,
        logStdout=True,
        statehost=False,
    )
    manager.options = SimpleNamespace(dry_run=False)
    manager.leftovers = []
    manager._action_all_configs = True
    manager.please_stop = False
    manager.filtered_configurations = ['test/' + config_name]
    manager.user_cache_dir = cache_dir
    manager.log_dir = str(Path(cache_dir) / 'log')
    manager.hostdir = 'local'
    manager.configs = {
        'test': {
            config_name: {
                'status': 'new',
                'instances': 1,
                'options': options,
            }
        }
    }
    manager._check_sanitizing = lambda: False
    manager._find_component_path = lambda component: component_script

    manager.start()

    state_dir = Path(cache_dir) / 'test' / config_name
    result.put({
        'running': (state_dir / 'running').exists(),
        'starting': (state_dir / 'starting').exists(),
    })


def _start_in_bounded_child(cache_dir, component_script, config_name):
    result = multiprocessing.Queue()
    child = multiprocessing.Process(
        target=_run_start,
        args=(str(cache_dir), str(component_script), config_name, result),
    )
    child.start()
    child.join(2.5)
    hung = child.is_alive()
    if hung:
        child.terminate()
        child.join(2)

    observed = None
    try:
        observed = result.get_nowait()
    except Empty:
        pass
    result.close()
    return hung, child.exitcode, observed


def test_start_returns_when_launched_instance_exits(tmp_path):
    component = tmp_path / 'failed_component.py'
    component.write_text('raise SystemExit(7)\n')
    cache_dir = tmp_path / 'failed-cache'

    hung, exitcode, observed = _start_in_bounded_child(
        cache_dir, component, 'failed'
    )

    state_dir = cache_dir / 'test' / 'failed'
    assert not hung
    assert exitcode == 0
    assert observed == {'running': False, 'starting': False}
    assert not (state_dir / 'starting').exists()


def test_start_marks_an_instance_ready_after_pid_creation(tmp_path):
    cache_dir = tmp_path / 'ready-cache'
    pid_file = cache_dir / 'test' / 'ready' / 'ready.pid'
    component = tmp_path / 'ready_component.py'
    component.write_text(
        'from pathlib import Path\n'
        'import time\n'
        'pid = Path(' + repr(str(pid_file)) + ')\n'
        'pid.parent.mkdir(parents=True, exist_ok=True)\n'
        'pid.write_text("fixture")\n'
        'time.sleep(2)\n'
    )

    hung, exitcode, observed = _start_in_bounded_child(
        cache_dir, component, 'ready'
    )

    assert not hung
    assert exitcode == 0
    assert observed == {'running': True, 'starting': False}
