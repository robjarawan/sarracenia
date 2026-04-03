import pytest
import types
import logging
from unittest.mock import patch, MagicMock

from sarracenia import Message as SR3Message
import sarracenia.config


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


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test___init__():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    ct = Content_type(options)
    assert ct.o.filterContentType_rejectUnknown is True
    assert ct.o.filterContentType_rejectUndefined is True
    assert ct.o.filterContentType_acceptType == []
    assert ct.o.filterContentType_rejectType == []


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test___init___strips_whitespace():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_acceptType = [' text/plain ', ' image/png']
    options.filterContentType_rejectType = ['application/zip ']
    ct = Content_type(options)
    assert ct.o.filterContentType_acceptType == ['text/plain', 'image/png']
    assert ct.o.filterContentType_rejectType == ['application/zip']


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test___init___warns_on_overlap(caplog):
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_acceptType = ['text/plain', 'image/png']
    options.filterContentType_rejectType = ['text/plain']
    with caplog.at_level(logging.WARNING):
        ct = Content_type(options)
    assert 'text/plain' in caplog.text
    assert 'acceptType' in caplog.text and 'rejectType' in caplog.text


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_known_accept_type():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_acceptType = ['text/plain']
    ct = Content_type(options)

    msg = make_message()
    msg['contentType'] = 'text/plain'

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 1
    assert len(wl.rejected) == 0


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_known_reject_type():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_rejectType = ['application/zip']
    ct = Content_type(options)

    msg = make_message()
    msg['contentType'] = 'application/zip'

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 1


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_undefined_type_rejected_by_default():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_acceptType = ['text/plain']
    options.filterContentType_rejectType = ['application/zip']
    ct = Content_type(options)

    msg = make_message()
    msg['contentType'] = 'image/png'

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 1


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_undefined_type_accepted():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_acceptType = ['text/plain']
    options.filterContentType_rejectType = ['application/zip']
    options.filterContentType_rejectUndefined = False
    ct = Content_type(options)

    msg = make_message()
    msg['contentType'] = 'image/png'

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 1
    assert len(wl.rejected) == 0


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_unknown_type_rejected_by_default():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    ct = Content_type(options)

    msg = make_message()
    # No contentType set, and features['filetypes'] is False so set_content_type fails

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 1


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_unknown_type_accepted():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    options.filterContentType_rejectUnknown = False
    ct = Content_type(options)

    msg = make_message()
    # No contentType set

    wl = make_worklist()
    wl.incoming = [msg]
    ct.after_accept(wl)

    assert len(wl.incoming) == 1
    assert len(wl.rejected) == 0


@patch('sarracenia.flowcb.filter.content_type.features', {'filetypes': {'present': False}})
def test_after_accept_empty_worklist():
    from sarracenia.flowcb.filter.content_type import Content_type
    options = make_options()
    ct = Content_type(options)

    wl = make_worklist()
    ct.after_accept(wl)

    assert len(wl.incoming) == 0
    assert len(wl.rejected) == 0
    assert len(wl.failed) == 0
