import pytest
import datetime
from unittest.mock import patch, MagicMock
import sarracenia
import sarracenia.config
from sarracenia.flowcb.authenticate.copernicus import Copernicus


def _make_opts():
    opts = sarracenia.config.default_config()
    opts.openidConnectUrl = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
    opts.clientId = 'cdse-public'
    opts.grantType = 'password'
    return opts


def _mock_cred(username='testuser', password='testpass'):
    details = MagicMock()
    details.url.username = username
    details.url.password = password
    return (True, details)


def _make_token_response(access_token='tok123', expires_in=600,
                         refresh_token='ref456', refresh_expires_in=3600):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        'access_token': access_token,
        'expires_in': expires_in,
        'refresh_token': refresh_token,
        'refresh_expires_in': refresh_expires_in,
    }
    return resp


def _build_instance():
    opts = _make_opts()
    cop = Copernicus(opts)
    return cop


# --- __init__ tests ---

def test_init_defaults():
    cop = _build_instance()
    assert cop.o.openidConnectUrl == 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
    assert cop.o.clientId == 'cdse-public'
    assert cop.o.grantType == 'password'
    assert cop._token is None
    assert cop._refresh is None


# --- get_token tests ---

def test_get_token_success():
    cop = _build_instance()
    cop.o.credentials = MagicMock()
    cop.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = _make_token_response(access_token='my_access_token')

    with patch('sarracenia.flowcb.authenticate.copernicus.requests.post', return_value=mock_resp):
        result = cop.get_token()

    assert result == 'my_access_token'
    assert cop._token == 'my_access_token'
    assert cop._refresh == 'ref456'


def test_get_token_cached_not_expired():
    cop = _build_instance()
    cop._token = 'cached_tok'
    cop._token_expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)

    result = cop.get_token()
    assert result == 'cached_tok'


def test_get_token_expired_refreshes():
    cop = _build_instance()
    cop._token = 'old_tok'
    cop._token_expires = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    cop._refresh = None
    cop._refresh_expires = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)

    cop.o.credentials = MagicMock()
    cop.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = _make_token_response(access_token='refreshed_tok')

    with patch('sarracenia.flowcb.authenticate.copernicus.requests.post', return_value=mock_resp):
        result = cop.get_token()

    assert result == 'refreshed_tok'


def test_get_token_refresh_token_path():
    cop = _build_instance()
    cop._token = 'old_tok'
    cop._token_expires = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    cop._refresh = 'valid_refresh_tok'
    cop._refresh_expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)

    mock_resp = _make_token_response(access_token='new_via_refresh')

    with patch('sarracenia.flowcb.authenticate.copernicus.requests.post', return_value=mock_resp) as mock_post:
        result = cop.get_token()

    assert result == 'new_via_refresh'
    call_data = mock_post.call_args[1].get('data') or mock_post.call_args[0][1] if len(mock_post.call_args[0]) > 1 else mock_post.call_args[1]['data']
    assert call_data['grant_type'] == 'refresh_token'
    assert call_data['refresh_token'] == 'valid_refresh_tok'


def test_get_token_cred_failure():
    cop = _build_instance()
    cop.o.credentials = MagicMock()
    cred_details = MagicMock()
    cred_details.url.username = None
    cred_details.url.password = None
    cop.o.credentials.get = MagicMock(return_value=(True, cred_details))

    result = cop.get_token()
    assert result is None


def test_get_token_http_error_retries_once():
    cop = _build_instance()
    cop.o.credentials = MagicMock()
    cop.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
    mock_resp.json.return_value = {'error': 'server_error'}

    success_resp = _make_token_response(access_token='retry_tok')

    with patch('sarracenia.flowcb.authenticate.copernicus.requests.post',
               side_effect=[mock_resp, success_resp]) as mock_post:
        result = cop.get_token()

    assert result == 'retry_tok'
    assert mock_post.call_count == 2


def test_get_token_http_error_no_infinite_retry():
    cop = _build_instance()
    cop.o.credentials = MagicMock()
    cop.o.credentials.get = MagicMock(return_value=_mock_cred())

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
    mock_resp.json.return_value = {'error': 'server_error'}

    with patch('sarracenia.flowcb.authenticate.copernicus.requests.post', return_value=mock_resp):
        result = cop.get_token(retry=True)

    assert result is None
