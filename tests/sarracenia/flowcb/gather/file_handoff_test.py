from collections import OrderedDict
import threading

import pytest

from sarracenia.config import no_file_config
from sarracenia.flowcb.gather.file import File


@pytest.mark.parametrize('arrival', ['before', 'during', 'after'])
def test_event_arriving_during_handoff_is_retained(tmp_path, arrival):
    """Keep events submitted by the observer thread while a batch is drained."""
    options = no_file_config()
    options.component = 'watch'
    options.config = 'handoff'
    options.no = 1
    for number, line in enumerate([
            'post_baseDir ' + str(tmp_path),
            'post_baseUrl file:' + str(tmp_path),
            'fileAgeMin 0',
            'blockSize 1',
            'identity arbitrary handoff-control',
    ], 1):
        options.parse_line('watch', 'handoff', 'handoff.conf', number, line)
    options.publishers = [{
        'baseDir': options.post_baseDir,
        'baseUrl': options.post_baseUrl,
        'topicPrefix': ['v03'],
        'format': 'v03',
    }]
    first = tmp_path / 'first.dat'
    second = tmp_path / 'second.dat'
    first.write_bytes(b'first event')
    second.write_bytes(b'second event')
    gather = File(options)
    gather.on_add('modify', str(first), None)

    copied = threading.Event()
    submitted = threading.Event()
    errors = []

    def producer():
        try:
            if not copied.wait(3):
                raise AssertionError('handoff did not reach its scheduling barrier')
            gather.on_add('modify', str(second), None)
        except Exception as error:
            errors.append(error)
        finally:
            submitted.set()

    class HandoffBarrier(OrderedDict):
        def update(self, *args, **kwargs):
            super().update(*args, **kwargs)
            # Allow the real on_add callback to run immediately after the pending
            # events are copied. This is the observer/flow thread interleaving.
            copied.set()
            assert submitted.wait(3), 'observer callback did not finish'

    thread = None
    if arrival == 'before':
        gather.on_add('modify', str(second), None)
    elif arrival == 'during':
        gather.left_events = HandoffBarrier()
        thread = threading.Thread(target=producer, daemon=True)
        thread.start()

    try:
        messages = gather.wakeup()
    finally:
        if thread is not None:
            copied.set()
            thread.join(timeout=3)
            assert not thread.is_alive(), 'observer callback is stuck'
    assert not errors
    if arrival == 'after':
        gather.on_add('modify', str(second), None)
    messages.extend(gather.wakeup())

    assert {message['new_file'] for message in messages} == {'first.dat', 'second.dat'}
