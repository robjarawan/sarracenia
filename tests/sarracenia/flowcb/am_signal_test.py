import copy
import multiprocessing
import os
import signal
import sys
import time

import pytest

import sarracenia.config
from sarracenia.flowcb.gather.am import Am as GatherAm
from sarracenia.flowcb.send.am import Am as SendAm
from sarracenia.instance import instance


class _RunningFlow:
    def __init__(self):
        self.stop_requested = False

    def stop_request(self):
        self.stop_requested = True


def _make_options(name):
    options = copy.deepcopy(sarracenia.config.default_config())
    options.component = 'flow'
    options.config = name
    options.sendTo = 'am://127.0.0.1:5005'
    options.fileSizeMax = 0
    options.no = 1
    return options


def _signal_child(callback_name, connection):
    controller = object.__new__(instance)
    controller.o = _make_options(callback_name)
    controller.running_instance = _RunningFlow()
    signal.signal(signal.SIGTERM, controller.stop_signal)

    callback_class = SendAm if callback_name == 'send' else GatherAm
    callback = callback_class(controller.o)
    connection.send({
        'ready': True,
        'parent_handler_preserved': signal.getsignal(signal.SIGTERM) == controller.stop_signal,
    })

    deadline = time.monotonic() + 2
    while not controller.running_instance.stop_requested and time.monotonic() < deadline:
        time.sleep(0.01)

    if not controller.running_instance.stop_requested:
        connection.send({'stop_requested': False})
        connection.close()
        return

    system_exit = None
    try:
        callback.on_stop()
    except SystemExit as ex:
        system_exit = ex.code
    connection.send({
        'stop_requested': True,
        'socket_closed': callback.s.fileno() == -1,
        'system_exit': system_exit,
    })
    connection.close()


def _run_signal_case(callback_name):
    parent, child_connection = multiprocessing.Pipe()
    child = multiprocessing.Process(
        target=_signal_child,
        args=(callback_name, child_connection),
    )
    child.start()
    child_connection.close()

    ready = parent.recv() if parent.poll(3) else {'ready': False}
    if ready.get('ready'):
        os.kill(child.pid, signal.SIGTERM)

    stopped = None
    if parent.poll(3):
        try:
            stopped = parent.recv()
        except EOFError:
            pass
    child.join(3)
    if child.is_alive():
        child.terminate()
        child.join(2)
    parent.close()
    return ready, stopped, child.exitcode


@pytest.mark.skipif(sys.platform == 'win32', reason='AM gather uses POSIX process handling')
@pytest.mark.parametrize('callback_name', ['gather', 'send'])
def test_am_callback_preserves_instance_sigterm_handler(callback_name):
    ready, stopped, exitcode = _run_signal_case(callback_name)

    assert ready == {'ready': True, 'parent_handler_preserved': True}
    assert stopped is not None
    assert stopped['stop_requested'] is True
    assert stopped['socket_closed'] is True
    assert exitcode == 0
