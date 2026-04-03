import pytest
import datetime
from unittest.mock import patch, MagicMock
import sarracenia
import sarracenia.config
from sarracenia.flowcb.authenticate.eumetsat import Eumetsat


def _make_opts():
    opts = sarracenia.config.default_config()
    opts.apiTokenUrl = 'https://api.eumetsat.int/token'
    return opts


def _mock_cred(username='consumer_key', password='consumer_secret'):
    details = MagicMock()
    details.url.username = username
    details.url.password = password
    return (True, details)


def _build_instance():
    opts = _make_opts()
    eum = Eumetsat(opts)
    return eum


# --- __init__ tests ---

def test_init_defaults():
    eum = _build_instance()
    assert eum.o.apiTokenUrl == 'https://api.eumetsat.int/token'
    assert eum._api_token is None
    assert eum._token_expiry_time is None


# --- get_token tests ---

def test_get_token_success():
    eum = _build_instance()
    eum.o.credentials = MagicMock()
    eum.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'access_token': 'eumetsat_tok_abc',
        'expires_in': 3600,
    }

    with patch('sarracenia.flowcb.authenticate.eumetsat.requests.post', return_value=mock_resp):
        result = eum.get_token()

    assert result == 'eumetsat_tok_abc'
    assert eum._api_token == 'eumetsat_tok_abc'
    assert eum._token_expiry_time is not None


def test_get_token_cached():
    eum = _build_instance()
    eum._api_token = 'cached_eumetsat_tok'
    eum._token_expiry_time = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)

    result = eum.get_token()
    assert result == 'cached_eumetsat_tok'


def test_get_token_expired():
    eum = _build_instance()
    eum._api_token = 'expired_tok'
    eum._token_expiry_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)

    eum.o.credentials = MagicMock()
    eum.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'access_token': 'new_eumetsat_tok',
        'expires_in': 3600,
    }

    with patch('sarracenia.flowcb.authenticate.eumetsat.requests.post', return_value=mock_resp):
        result = eum.get_token()

    assert result == 'new_eumetsat_tok'


def test_get_token_cred_failure():
    eum = _build_instance()
    eum.o.credentials = MagicMock()
    eum.o.credentials.get = MagicMock(return_value=(False, MagicMock()))

    result = eum.get_token()
    assert result is None


def test_get_token_http_exception():
    eum = _build_instance()
    eum.o.credentials = MagicMock()
    eum.o.credentials.get = MagicMock(return_value=_mock_cred())

    with patch('sarracenia.flowcb.authenticate.eumetsat.requests.post',
               side_effect=Exception("Connection error")):
        result = eum.get_token()

    assert result is None
    assert eum._api_token is None
