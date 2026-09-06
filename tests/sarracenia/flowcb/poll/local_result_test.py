import os
from types import SimpleNamespace

import pytest

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll import Poll


def _poll(source, follow_symlinks):
    options = sarracenia.config.no_file_config()
    options.pollUrl = 'file:' + str(source)
    options.post_baseUrl = 'file:'
    options.post_baseDir = str(source)
    options.publishers = [{'baseUrl': 'file:', 'baseDir': str(source)}]
    options.path = [str(source)]
    options.recursive = False
    options.follow_symlinks = follow_symlinks
    options.fileEvents = {'create', 'modify', 'link'}
    options.identity_method = 'random'
    return Poll(options)


@pytest.mark.parametrize(
    'symbolic_link,follow_symlinks',
    [(False, False), (True, False), (True, True)],
)
def test_local_poll_returns_messages_from_real_listing(
        tmp_path, symbolic_link, follow_symlinks):
    source = tmp_path / 'poll'
    source.mkdir()
    expected_link = None
    if symbolic_link:
        target = tmp_path / 'source.dat'
        target.write_bytes(b'weather data')
        link = source / 'product.dat'
        expected_link = '../' + target.name
        link.symlink_to(expected_link)
        expected_name = link.name
    else:
        target = source / 'target.dat'
        target.write_bytes(b'weather data')
        expected_name = target.name

    messages = _poll(source, follow_symlinks).poll()

    assert len(messages) == 1
    assert isinstance(messages[0], sarracenia.Message)
    assert messages[0]['new_file'] == expected_name
    if symbolic_link and not follow_symlinks:
        assert messages[0]['fileOp'] == {'link': expected_link}


@pytest.mark.parametrize('symbolic_link', [False, True])
def test_remote_advertisement_result_control(tmp_path, symbolic_link):
    path = tmp_path / 'target.dat'
    path.write_bytes(b'weather data')
    if symbolic_link:
        link = tmp_path / 'product.dat'
        link.symlink_to(path.name)
        path = link

    options = sarracenia.config.no_file_config()
    options.post_baseUrl = 'https://example.invalid/data'
    options.post_baseDir = str(tmp_path)
    options.publishers = [{
        'baseUrl': options.post_baseUrl,
        'baseDir': str(tmp_path),
    }]
    options.follow_symlinks = False
    options.fileEvents = {'create', 'modify', 'link'}
    options.identity_method = None
    callback = object.__new__(Poll)
    callback.o = options
    callback.dest = SimpleNamespace(readlink=os.readlink)

    messages = callback.poll_list_post(
        str(tmp_path), {path.name: os.lstat(path)}, [path.name])

    assert len(messages) == 1
    assert isinstance(messages[0], sarracenia.Message)
