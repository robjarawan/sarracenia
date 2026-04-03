import pytest
import datetime
from unittest.mock import patch, MagicMock
import sarracenia
import sarracenia.config
from sarracenia.flowcb.authenticate.nasa_earthdata import Nasa_earthdata


def _make_opts():
    opts = sarracenia.config.default_config()
    opts.earthdataUrl = 'https://urs.earthdata.nasa.gov'
    return opts


def _mock_cred(username='testuser', password='testpass'):
    details = MagicMock()
    details.url.username = username
    details.url.password = password
    return (True, details)


def _build_instance(earthdataUrl='https://urs.earthdata.nasa.gov'):
    opts = _make_opts()
    opts.earthdataUrl = earthdataUrl
    ned = Nasa_earthdata(opts)
    return ned


# --- __init__ tests ---

def test_init_strips_trailing_slash():
    opts = _make_opts()
    opts.earthdataUrl = 'https://urs.earthdata.nasa.gov/'
    ned = Nasa_earthdata(opts)
    assert ned.o.earthdataUrl == 'https://urs.earthdata.nasa.gov'


def test_init_no_trailing_slash():
    ned = _build_instance('https://urs.earthdata.nasa.gov')
    assert ned.o.earthdataUrl == 'https://urs.earthdata.nasa.gov'


# --- token_expires property tests ---

def test_token_expires_setter_string():
    ned = _build_instance()
    ned._token_expires = "07/23/2025"
    assert ned._token_expires == datetime.datetime(2025, 7, 23)


def test_token_expires_setter_datetime():
    ned = _build_instance()
    dt = datetime.datetime(2025, 12, 31)
    ned._token_expires = dt
    assert ned._token_expires == dt


def test_token_expiry_str_format():
    ned = _build_instance()
    ned._token_expires = datetime.datetime(2025, 1, 15)
    assert ned._token_expiry_str() == "2025-01-15"


def test_token_expiry_str_none():
    ned = _build_instance()
    assert ned._token_expiry_str() is None


# --- get_token tests ---

def test_get_token_returns_cached_when_not_expired():
    ned = _build_instance()
    ned._token = 'cached_token_12345'
    ned._token_expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    result = ned.get_token()
    assert result == 'cached_token_12345'


def test_get_token_clears_expired_token():
    ned = _build_instance()
    ned._token = 'old_token_12345'
    ned._token_expires = datetime.datetime.utcnow() - datetime.timedelta(days=1)

    with patch.object(ned, 'get_earthdata_token', return_value=False):
        result = ned.get_token()
    assert result is None


# --- get_earthdata_token tests ---

def test_get_earthdata_token_success():
    ned = _build_instance()
    ned.o.credentials = MagicMock()
    ned.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {'access_token': 'existing_tok_99', 'expiration_date': '12/31/2025'}
    ]

    with patch('sarracenia.flowcb.authenticate.nasa_earthdata.requests.get', return_value=mock_resp):
        result = ned.get_earthdata_token()

    assert result is True
    assert ned._token == 'existing_tok_99'
    assert ned._token_expires == datetime.datetime(2025, 12, 31)


def test_get_earthdata_token_empty_creates_new():
    ned = _build_instance()
    ned.o.credentials = MagicMock()
    ned.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = []

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {
        'access_token': 'brand_new_token', 'expiration_date': '03/15/2026'
    }

    with patch('sarracenia.flowcb.authenticate.nasa_earthdata.requests.get', return_value=mock_get_resp), \
         patch('sarracenia.flowcb.authenticate.nasa_earthdata.requests.post', return_value=mock_post_resp):
        result = ned.get_earthdata_token()

    assert result is True
    assert ned._token == 'brand_new_token'


def test_get_earthdata_token_cred_failure():
    ned = _build_instance()
    ned.o.credentials = MagicMock()
    ned.o.credentials.get = MagicMock(return_value=(False, MagicMock()))

    result = ned.get_earthdata_token()
    assert result is False
    assert ned._token is None


# --- create_earthdata_token tests ---

def test_create_earthdata_token_success():
    ned = _build_instance()
    auth = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        'access_token': 'created_token_abc', 'expiration_date': '06/01/2026'
    }

    with patch('sarracenia.flowcb.authenticate.nasa_earthdata.requests.post', return_value=mock_resp):
        result = ned.create_earthdata_token(auth)

    assert result is True
    assert ned._token == 'created_token_abc'
    assert ned._token_expires == datetime.datetime(2026, 6, 1)


def test_create_earthdata_token_http_error():
    ned = _build_instance()
    auth = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = 'Unauthorized'

    with patch('sarracenia.flowcb.authenticate.nasa_earthdata.requests.post', return_value=mock_resp):
        result = ned.create_earthdata_token(auth)

    assert result is False
    assert ned._token is None