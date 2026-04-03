import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import sarracenia
import sarracenia.config
from sarracenia.flowcb.authenticate.bc_trans import Bc_trans


def _mock_cred(username='client_id', password='client_secret'):
    details = MagicMock()
    details.url.username = username
    details.url.password = password
    return (True, details)


def _build_instance():
    """Build a Bc_trans instance. The __init__ calls credentials.get() via the
    class-level Config.credentials, so we need to mock that."""
    opts = sarracenia.config.default_config()
    opts.tokenEndpoint_baseUrl = 'https://loginproxy.gov.bc.ca/'
    opts.tokenEndpoint_path = 'auth/realms/apigw/protocol/openid-connect/token'

    with patch.object(sarracenia.config.Config, 'credentials', create=True) as mock_creds_cls:
        mock_creds_cls.get = MagicMock(return_value=_mock_cred())
        bc = Bc_trans(opts)
    return bc


# --- get_token tests ---

def test_get_token_success():
    bc = _build_instance()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {'access_token': 'bc_token_xyz'}

    with patch('sarracenia.flowcb.authenticate.bc_trans.requests.post', return_value=mock_resp):
        result = bc.get_token()

    assert result == 'bc_token_xyz'
    assert bc._bearer_token == 'bc_token_xyz'


def test_get_token_http_error():
    bc = _build_instance()

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = 'Forbidden'

    with patch('sarracenia.flowcb.authenticate.bc_trans.requests.post', return_value=mock_resp):
        result = bc.get_token()

    assert result is None
    assert bc._bearer_token is None


def test_get_token_exception():
    bc = _build_instance()

    with patch('sarracenia.flowcb.authenticate.bc_trans.requests.post',
               side_effect=Exception("Connection refused")):
        result = bc.get_token()

    assert result is None
