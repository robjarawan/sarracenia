import pytest

from sarracenia.config import no_file_config
from sarracenia.featuredetection import features
from sarracenia.flowcb.gather.file import File


@pytest.mark.skipif(not features['watch']['present'], reason='watchdog is not installed')
@pytest.mark.parametrize('watch', [False, True])
@pytest.mark.parametrize('count', [0, 1, 10, 11, 17, 20, 21, 25])
def test_startup_delivers_every_batch(tmp_path, watch, count):
    """A watcher must drain the startup scan before switching to new events."""
    options = no_file_config()
    options.component = 'watch' if watch else 'post'
    options.config = 'startup_batch'
    options.no = 1
    options.force_polling = False
    options.postpath = [str(tmp_path)]
    for number, line in enumerate([
            'batch 10',
            'sleep 1' if watch else 'sleep -1',
            'post_on_start True',
            'recursive True',
            'fileAgeMin 0',
            'blockSize 1',
            'identity arbitrary startup-control',
            'post_baseDir ' + str(tmp_path),
            'post_baseUrl file:' + str(tmp_path),
            'post_exchange startup-control',
    ], 1):
        options.parse_line(options.component, options.config, 'startup.conf', number, line)
    options.publishers = [{
        'baseDir': options.post_baseDir,
        'baseUrl': options.post_baseUrl,
        'topicPrefix': ['v03'],
        'format': 'v03',
    }]
    expected = {'item-{}'.format(index) for index in range(count)}
    for name in expected:
        (tmp_path / name).write_bytes(b'startup fixture')

    gather = File(options)
    # A restarted watcher encounters identities already cached on disk. Prime
    # those attributes so writing them cannot generate compensating modify events.
    gather.walk(str(tmp_path))
    gather.on_start()
    received = []
    try:
        for _ in range(count // options.batch + 3):
            received.extend(gather.gather(options.batch)[1])
    finally:
        if hasattr(gather, 'observer'):
            gather.observer.stop()
            gather.observer.join(timeout=3)

    assert {message['new_file'] for message in received} == expected
