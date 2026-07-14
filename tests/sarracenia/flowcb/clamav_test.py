import types

import pytest

from sarracenia.flowcb.clamav import Clamav


def make_message(filename):
    return {'new_dir': '/data', 'new_file': filename}


def make_callback(component):
    callback = Clamav.__new__(Clamav)
    callback.o = types.SimpleNamespace(component=component)
    callback.avscan_hit = lambda path: path.endswith('clean.dat')
    return callback


@pytest.mark.parametrize('component', ['sender', 'post', 'watch'])
def test_after_accept_keeps_clean_and_rejects_infected(component):
    clean = make_message('clean.dat')
    infected = make_message('infected.dat')
    worklist = types.SimpleNamespace(incoming=[clean, infected], rejected=[])

    make_callback(component).after_accept(worklist)

    assert worklist.incoming == [clean]
    assert worklist.rejected == [infected]


@pytest.mark.parametrize('component', ['subscribe', 'sarra'])
def test_after_work_keeps_clean_and_rejects_infected(component):
    clean = make_message('clean.dat')
    infected = make_message('infected.dat')
    worklist = types.SimpleNamespace(ok=[clean, infected], rejected=[])

    make_callback(component).after_work(worklist)

    assert worklist.ok == [clean]
    assert worklist.rejected == [infected]
