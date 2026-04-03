import pytest
import copy
import sarracenia.moth


def test_default_options_returns_dict():
    d = sarracenia.moth.default_options()
    assert isinstance(d, dict)


def test_default_options_has_expected_keys():
    d = sarracenia.moth.default_options()
    for k in ['broker', 'exchange', 'topicPrefix', 'messageDebugDump',
              'inline', 'inlineEncoding', 'inlineByteMax']:
        assert k in d, f"Missing key: {k}"


def test_default_options_broker_none():
    d = sarracenia.moth.default_options()
    assert d['broker'] is None


def test_default_options_inline_defaults():
    d = sarracenia.moth.default_options()
    assert d['inline'] == False
    assert d['inlineEncoding'] == 'guess'
    assert d['inlineByteMax'] == 4096


def test_default_options_is_fresh_copy():
    d1 = sarracenia.moth.default_options()
    d2 = sarracenia.moth.default_options()
    d1['broker'] = 'modified'
    assert d2['broker'] is None


def test_default_options_message_strategy():
    d = sarracenia.moth.default_options()
    assert 'message_strategy' in d
    ms = d['message_strategy']
    assert ms['reset'] is True
    assert ms['stubborn'] is True
    assert ms['failure_duration'] == '5m'


def test_default_options_deep_copy():
    d1 = sarracenia.moth.default_options()
    d1['message_strategy']['reset'] = False
    d2 = sarracenia.moth.default_options()
    assert d2['message_strategy']['reset'] is True


def test_ProtocolPresent_unknown():
    assert sarracenia.moth.ProtocolPresent('unknown') is False
