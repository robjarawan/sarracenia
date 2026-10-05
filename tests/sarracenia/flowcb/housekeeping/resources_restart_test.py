import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import sarracenia.flowcb.housekeeping.resources as resources_module
from sarracenia.flowcb.housekeeping.resources import Resources


def _restart_worker(directory, mode, ready, release, executed):
    callback = object.__new__(Resources)
    callback.o = SimpleNamespace(cfg_run_dir=directory)
    callback.randomSleep = 0.02

    def simulated_exec(*args):
        executed.set()
        if mode == 'hold':
            ready.set()
            release.wait(3)
            Path(directory, 'resources_restart').unlink(missing_ok=True)
            os._exit(0)
        if mode == 'crash':
            os._exit(17)
        os._exit(0)

    resources_module.resource = None
    resources_module.os.execl = simulated_exec
    callback.restart()


def _start_worker(directory, mode):
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    executed = multiprocessing.Event()
    process = multiprocessing.Process(
        target=_restart_worker,
        args=(str(directory), mode, ready, release, executed),
    )
    process.start()
    return process, ready, release, executed


@pytest.mark.skipif(os.name == 'nt', reason='uses POSIX child exit to simulate interrupted exec')
def test_resource_restart_recovers_marker_left_by_dead_owner(tmp_path):
    owner, _, _, owner_executed = _start_worker(tmp_path, 'crash')
    assert owner_executed.wait(2)
    owner.join(2)
    assert owner.exitcode == 17
    assert (tmp_path / 'resources_restart').is_file()

    recovery, _, _, recovery_executed = _start_worker(tmp_path, 'complete')
    recovery.join(2)
    hung = recovery.is_alive()
    if hung:
        recovery.terminate()
        recovery.join(2)

    assert not hung, 'a dead restart owner leaves every later restart waiting forever'
    assert recovery_executed.is_set()
    assert recovery.exitcode == 0


@pytest.mark.skipif(os.name == 'nt', reason='uses POSIX child exit to simulate interrupted exec')
def test_resource_restart_recovers_legacy_orphan_marker(tmp_path):
    marker = tmp_path / 'resources_restart'
    marker.write_text('20260906T010203.123456')
    stale_time = time.time() - 120
    os.utime(marker, (stale_time, stale_time))

    recovery, _, _, recovery_executed = _start_worker(tmp_path, 'complete')
    recovery.join(2)
    hung = recovery.is_alive()
    if hung:
        recovery.terminate()
        recovery.join(2)

    assert not hung, 'a marker written by the current release cannot identify a dead owner'
    assert recovery_executed.is_set()
    assert recovery.exitcode == 0


@pytest.mark.skipif(os.name == 'nt', reason='uses POSIX child exit to simulate interrupted exec')
def test_resource_restart_does_not_take_recent_legacy_marker(tmp_path):
    marker = tmp_path / 'resources_restart'
    marker.write_text('20260906T010203.123456')

    contender, _, _, contender_executed = _start_worker(tmp_path, 'complete')
    time.sleep(0.2)
    assert not contender_executed.is_set()

    marker.unlink()
    contender.join(2)
    if contender.is_alive():
        contender.terminate()
        contender.join(2)

    assert contender_executed.is_set()
    assert contender.exitcode == 0


@pytest.mark.skipif(os.name == 'nt', reason='uses POSIX child exit to simulate interrupted exec')
def test_resource_restart_waits_while_marker_owner_is_alive(tmp_path):
    owner, owner_ready, owner_release, _ = _start_worker(tmp_path, 'hold')
    assert owner_ready.wait(2)

    contender, _, _, contender_executed = _start_worker(tmp_path, 'complete')
    time.sleep(0.2)
    assert not contender_executed.is_set()

    owner_release.set()
    owner.join(2)
    contender.join(2)
    if owner.is_alive():
        owner.terminate()
        owner.join(2)
    if contender.is_alive():
        contender.terminate()
        contender.join(2)

    assert owner.exitcode == 0
    assert contender_executed.is_set()
    assert contender.exitcode == 0
