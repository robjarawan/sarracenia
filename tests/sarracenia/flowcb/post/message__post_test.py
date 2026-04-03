import pytest
import types
from unittest.mock import patch, MagicMock

from sarracenia import Message as SR3Message
import sarracenia.config
import sarracenia.moth


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_message():
    m = SR3Message()
    m['new_dir'] = '/tmp/test'
    m['new_file'] = 'file.txt'
    return m


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def make_mock_poster():
    mock_poster = MagicMock()
    mock_poster.putNewMessage = MagicMock(return_value=True)
    mock_poster.metricsReport = MagicMock(return_value={'txGoodCount': 0, 'txBadCount': 0, 'txByteCount': 0})
    mock_poster.putSetup = MagicMock()
    mock_poster.close = MagicMock()
    return mock_poster


def make_options_with_publishers():
    options = make_options()
    options.post_broker = 'amqp://user:pass@host'
    options.publishers = [{'broker': 'amqp://user:pass@host'}]
    return options


def test___init___no_post_broker():
    options = make_options()
    # Ensure post_broker is not set
    if hasattr(options, 'post_broker'):
        delattr(options, 'post_broker')
    with patch('sarracenia.moth.Moth.pubFactory') as mock_factory:
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)
        mock_factory.assert_not_called()
        assert not hasattr(msg_cb, 'posters')


def test___init___with_publishers():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster) as mock_factory:
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)
        assert mock_factory.call_count == 1
        assert len(msg_cb.posters) == 1
        assert msg_cb.posters[0] is mock_poster


def test_post_all_succeed():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    mock_poster.putNewMessage.return_value = True
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    wl = make_worklist()
    m = make_message()
    wl.ok = [m]
    msg_cb.post(wl)

    assert len(wl.ok) == 1
    assert len(wl.failed) == 0


def test_post_failure_tracked():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    mock_poster.putNewMessage.return_value = False
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    wl = make_worklist()
    m = make_message()
    wl.ok = [m]
    msg_cb.post(wl)

    assert len(wl.ok) == 0
    assert len(wl.failed) == 1
    assert 'post_failures' in wl.failed[0]
    assert 0 in wl.failed[0]['post_failures']


def test_post_exception_handled():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    mock_poster.putNewMessage.side_effect = Exception("connection lost")
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    wl = make_worklist()
    m = make_message()
    wl.ok = [m]
    msg_cb.post(wl)

    assert len(wl.ok) == 0
    assert len(wl.failed) == 1
    assert 'post_failures' in wl.failed[0]


def test_post_with_publisher_index():
    options = make_options_with_publishers()
    options.publishers = [
        {'broker': 'amqp://user:pass@host1'},
        {'broker': 'amqp://user:pass@host2'},
    ]
    mock_poster0 = make_mock_poster()
    mock_poster1 = make_mock_poster()
    mock_poster1.putNewMessage.return_value = True

    with patch('sarracenia.moth.Moth.pubFactory', side_effect=[mock_poster0, mock_poster1]):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    wl = make_worklist()
    m = make_message()
    m['publisher_index'] = 1
    wl.ok = [m]
    msg_cb.post(wl)

    mock_poster0.putNewMessage.assert_not_called()
    mock_poster1.putNewMessage.assert_called_once()
    assert len(wl.ok) == 1
    assert len(wl.failed) == 0


def test_post_retry_only_failed():
    options = make_options_with_publishers()
    options.publishers = [
        {'broker': 'amqp://user:pass@host1'},
        {'broker': 'amqp://user:pass@host2'},
    ]
    mock_poster0 = make_mock_poster()
    mock_poster1 = make_mock_poster()

    with patch('sarracenia.moth.Moth.pubFactory', side_effect=[mock_poster0, mock_poster1]):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    wl = make_worklist()
    m = make_message()
    # Simulate a previous failure on poster index 1 only
    m['post_failures'] = [1]
    m['_deleteOnPost'] |= set(['post_failures'])
    wl.ok = [m]

    msg_cb.post(wl)

    # Poster 0 should NOT be called (not in post_failures)
    mock_poster0.putNewMessage.assert_not_called()
    # Poster 1 should be called (it was in post_failures)
    mock_poster1.putNewMessage.assert_called_once()
    assert len(wl.ok) == 1


def test_metricsReport():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    report = msg_cb.metricsReport()
    assert isinstance(report, dict)
    assert 'amqp://user:pass@host' in report
    assert report['amqp://user:pass@host']['txGoodCount'] == 0


def test_on_start_calls_putSetup():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    msg_cb.on_start()
    mock_poster.putSetup.assert_called_once()


def test_on_stop_calls_close():
    options = make_options_with_publishers()
    mock_poster = make_mock_poster()
    with patch('sarracenia.moth.Moth.pubFactory', return_value=mock_poster):
        from sarracenia.flowcb.post.message import Message
        msg_cb = Message(options)

    msg_cb.on_stop()
    mock_poster.close.assert_called_once()