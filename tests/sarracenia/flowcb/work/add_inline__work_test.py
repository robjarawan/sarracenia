import pytest
import types
from unittest.mock import MagicMock

from sarracenia.flowcb.work.add_inline import Add_inline
from sarracenia import Message as SR3Message
import sarracenia.config


def make_options():
    options = sarracenia.config.default_config()
    options.logLevel = 'DEBUG'
    return options


def make_message():
    m = SR3Message()
    return m


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def test___init__():
    options = make_options()
    cb = Add_inline(options)
    assert cb is not None
    assert cb.o is options


def test_after_work_calls_putContentInline():
    options = make_options()
    cb = Add_inline(options)

    msg = make_message()
    msg.putContentInline = MagicMock()

    worklist = make_worklist()
    worklist.ok = [msg]

    cb.after_work(worklist)
    msg.putContentInline.assert_called_once_with(options)


def test_after_work_skips_existing_content():
    options = make_options()
    cb = Add_inline(options)

    msg = make_message()
    msg['content'] = {'encoding': 'utf-8', 'value': 'already here'}
    msg.putContentInline = MagicMock()

    worklist = make_worklist()
    worklist.ok = [msg]

    cb.after_work(worklist)
    msg.putContentInline.assert_not_called()


def test_after_work_empty_worklist():
    options = make_options()
    cb = Add_inline(options)

    worklist = make_worklist()
    # No error with empty ok list
    cb.after_work(worklist)
    assert worklist.ok == []


def test_after_work_mixed_messages():
    options = make_options()
    cb = Add_inline(options)

    msg_with_content = make_message()
    msg_with_content['content'] = {'encoding': 'utf-8', 'value': 'data'}
    msg_with_content.putContentInline = MagicMock()

    msg_without_content = make_message()
    msg_without_content.putContentInline = MagicMock()

    msg_without_content_2 = make_message()
    msg_without_content_2.putContentInline = MagicMock()

    worklist = make_worklist()
    worklist.ok = [msg_with_content, msg_without_content, msg_without_content_2]

    cb.after_work(worklist)

    msg_with_content.putContentInline.assert_not_called()
    msg_without_content.putContentInline.assert_called_once_with(options)
    msg_without_content_2.putContentInline.assert_called_once_with(options)
