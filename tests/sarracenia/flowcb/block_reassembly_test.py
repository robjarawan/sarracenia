import multiprocessing
from datetime import timedelta
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

import flufl.lock

import sarracenia.config
from sarracenia.flowcb.block_reassembly import Block_reassembly


class TestMessage(dict):

    def setReport(self, code, text):
        self['report'] = {'code': code, 'message': text}


def _options():
    options = sarracenia.config.default_config()
    options.inflight = None
    options.bufSize = 2
    return options


def _message(directory, part_name):
    return TestMessage({
        'blocks': {
            'manifest': {0: {'size': 4}},
            'number': 0,
            'size': 4,
        },
        'new_dir': str(directory),
        'new_file': part_name,
        'relPath': part_name,
    })


def _worklist(message):
    return SimpleNamespace(ok=[message], failed=[], rejected=[])


def _lock_available(lock_path):
    contender = flufl.lock.Lock(str(lock_path))
    try:
        contender.lock(timeout=timedelta(milliseconds=250))
    except flufl.lock.TimeOutError:
        return False
    contender.unlock()
    return True


def _run_reassembly(directory, part_name, result_queue):
    message = _message(directory, part_name)
    worklist = _worklist(message)
    try:
        Block_reassembly(_options()).after_work(worklist)
    except Exception as exception:
        result_queue.put({'exception': repr(exception)})
        return
    result_queue.put({
        'ok': len(worklist.ok),
        'failed': len(worklist.failed),
        'rejected': len(worklist.rejected),
        'part_exists': (Path(directory) / part_name).exists(),
    })


def test_standard_block_suffix_reassembles_complete_file(tmp_path):
    part_name = 'result§block_0000,4bytes_§'
    part_path = tmp_path / part_name
    part_path.write_bytes(b'abcd')
    message = _message(tmp_path, part_name)
    worklist = _worklist(message)

    Block_reassembly(_options()).after_work(worklist)

    assert worklist.ok == [message]
    assert worklist.failed == []
    assert worklist.rejected == []
    assert not part_path.exists()
    assert (tmp_path / 'result').read_bytes() == b'abcd'
    assert _lock_available(tmp_path / 'result.flufl_lock')


def test_short_block_fails_releases_lock_and_can_retry(tmp_path):
    part_name = 'result§block_0000,4bytes_§'
    part_path = tmp_path / part_name
    part_path.write_bytes(b'ab')
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_run_reassembly,
        args=(str(tmp_path), part_name, result_queue),
    )
    process.start()
    process.join(1)

    timed_out = process.is_alive()
    lock_available_while_stuck = None
    if timed_out:
        lock_available_while_stuck = _lock_available(
            tmp_path / 'result.flufl_lock'
        )
        process.terminate()
        process.join(2)

    assert not timed_out, (
        'short block did not return within one second; '
        'lock available while stuck: %s' % lock_available_while_stuck
    )

    try:
        result = result_queue.get(timeout=1)
    except Empty:
        raise AssertionError('reassembly exited without returning a disposition')

    assert result == {
        'ok': 0,
        'failed': 1,
        'rejected': 0,
        'part_exists': True,
    }
    assert part_path.read_bytes() == b'ab'
    assert _lock_available(tmp_path / 'result.flufl_lock')

    part_path.write_bytes(b'abcd')
    retry_message = _message(tmp_path, part_name)
    retry_worklist = _worklist(retry_message)
    Block_reassembly(_options()).after_work(retry_worklist)

    assert retry_worklist.ok == [retry_message]
    assert retry_worklist.failed == []
    assert retry_worklist.rejected == []
    assert not part_path.exists()
    assert (tmp_path / 'result').read_bytes() == b'abcd'
    assert _lock_available(tmp_path / 'result.flufl_lock')
