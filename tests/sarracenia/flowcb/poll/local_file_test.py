import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll import Poll


def make_poll(base_dir):
    options = sarracenia.config.no_file_config()
    options.post_baseUrl = 'file:'
    options.publishers = [{'baseUrl': 'file:', 'baseDir': str(base_dir)}]
    options.follow_symlinks = False
    options.fileEvents = ['create', 'modify', 'link']
    options.identity_method = None

    poll = Poll.__new__(Poll)
    poll.o = options
    return poll


def test_poll_list_post_keeps_local_regular_file_as_message(tmp_path):
    local_file = tmp_path / 'product.dat'
    local_file.write_bytes(b'weather data')
    poll = make_poll(tmp_path)

    messages = poll.poll_list_post(str(tmp_path), {'product.dat': None}, ['product.dat'])

    assert len(messages) == 1
    assert isinstance(messages[0], sarracenia.Message)
    assert messages[0]['new_file'] == 'product.dat'
