"""
Cross-poll-plugin contract / invariant test suite.

Tests invariants that ALL poll plugins should obey, parametrized across
the mail, copernicus_marine_s3, and usgs plugins.

Known inconsistency (documented):
  - mail.poll() returns None on credential failure or connection error,
    while the other plugins always return a list.
"""

import pytest
import types
import datetime
import imaplib
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.mail import Mail
from sarracenia.flowcb.poll.copernicus_marine_s3 import Copernicus_marine_s3
from sarracenia.flowcb.poll.usgs import Usgs


# ===========================================================================
# Shared helpers
# ===========================================================================

def _raw_email(subject="ContractTest"):
    return (
        f"From: sender@example.com\r\n"
        f"To: receiver@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"\r\n"
        f"Body.\r\n"
    ).encode("utf-8")


def _s3_page(contents):
    page = {}
    if contents is not None:
        page['Contents'] = contents
    return page


def _mock_urlopen_resp(status_code=200):
    resp = MagicMock()
    resp.getcode.return_value = status_code
    return resp


USGS_POLL_URL = (
    'http://waterservices.usgs.gov/nwis/iv/'
    '?format=waterml,2.0&site={0:}&period=PT3H'
)
USGS_STATION = '7|70026|9014087|Dry Dock, MI|US|MI|-5.0'


# ===========================================================================
# Instance builders
# ===========================================================================

def _make_mail_instance():
    options = sarracenia.config.default_config()
    options.pollUrl = "imaps://user@mail.example.com/"
    options.post_baseUrl = "imaps://mail.example.com"
    options.publishers = [{
        'baseUrl': 'imaps://mail.example.com', 'baseDir': None,
    }]
    url_ns = types.SimpleNamespace(
        username="user", password="pass",
        hostname="mail.example.com", scheme="imaps", port=None,
    )
    cred = types.SimpleNamespace(url=url_ns)
    cred_db = MagicMock()
    cred_db.get.return_value = (True, cred)
    options.credentials = cred_db
    inst = Mail.__new__(Mail)
    inst.o = options
    inst.metrics = {
        'transferRxBytes': 0, 'transferRxFiles': 0,
        'transferTxBytes': 0, 'transferTxFiles': 0,
    }
    return inst


def _make_copernicus(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://stac.marine.copernicus.eu/metadata'
    options.post_baseUrl = 'https://stac.marine.copernicus.eu'
    options.publishers = [{
        'baseUrl': 'https://stac.marine.copernicus.eu', 'baseDir': None,
    }]
    options.productID = overrides.pop(
        'productID', ['SEALEVEL_GLO_PHY_L4_NRT_008_046'],
    )
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Copernicus_marine_s3.__new__(Copernicus_marine_s3)
        inst.o = options
        inst.stop_requested = False
        Copernicus_marine_s3.__init__(inst, options)
    return inst


def _make_usgs(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = USGS_POLL_URL
    options.post_baseUrl = 'http://waterservices.usgs.gov/'
    options.publishers = [{
        'baseUrl': 'http://waterservices.usgs.gov/', 'baseDir': None,
    }]
    options.poll_usgs_station = [USGS_STATION]
    options.batch = 1
    options.msg = types.SimpleNamespace(new_baseurl='')
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Usgs.__new__(Usgs)
        inst.o = options
        inst.stop_requested = False
        Usgs.__init__(inst, options)
    return inst


# ===========================================================================
# Context-manager factories — happy path (returns ≥1 message)
# ===========================================================================

@contextmanager
def mail_happy():
    inst = _make_mail_instance()
    raw = _raw_email("Hello")
    with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
        mock_conn = MagicMock()
        mock_imap.IMAP4_SSL.return_value = mock_conn
        mock_conn.select.return_value = ("OK", [b"1"])
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [(b"1", raw)])
        yield inst


@contextmanager
def copernicus_happy():
    inst = _make_copernicus()
    s3_url = ('https://s3.waw3-1.cloudferro.com'
              '/mdl-native-07/native/SEALEVEL/cmems_ds')
    now = datetime.datetime(2024, 1, 15, 12, 0, 0)
    fake_page = _s3_page([{
        'Key': 'native/SEALEVEL/cmems_ds/file1.nc',
        'LastModified': now, 'Size': 1024,
    }])
    with patch.object(Copernicus_marine_s3,
                      'get_s3_urls_from_stac') as mock_stac, \
         patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client') \
            as mock_boto:
        mock_stac.return_value = {'SEALEVEL': [s3_url]}
        paginator = MagicMock()
        paginator.paginate.return_value = [fake_page]
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client
        yield inst


@contextmanager
def usgs_happy():
    inst = _make_usgs()
    with patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen') as m:
        m.return_value = _mock_urlopen_resp(200)
        yield inst


# ===========================================================================
# Context-manager factories — error path (external boundary fails)
# ===========================================================================

@contextmanager
def mail_error():
    inst = _make_mail_instance()
    with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
        mock_imap.IMAP4_SSL.side_effect = imaplib.IMAP4.error("refused")
        mock_imap.IMAP4 = imaplib.IMAP4
        yield inst


@contextmanager
def copernicus_error():
    inst = _make_copernicus()
    s3_url = 'https://s3.example.com/bucket/prefix/data'
    with patch.object(Copernicus_marine_s3,
                      'get_s3_urls_from_stac') as mock_stac, \
         patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client') \
            as mock_boto:
        mock_stac.return_value = {'PROD': [s3_url]}
        mock_boto.side_effect = Exception('S3 unavailable')
        yield inst


@contextmanager
def usgs_error():
    inst = _make_usgs()
    with patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen') as m:
        m.return_value = _mock_urlopen_resp(403)
        yield inst


# ===========================================================================
# Context-manager factories — empty (no data available)
# ===========================================================================

@contextmanager
def mail_empty():
    inst = _make_mail_instance()
    with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
        mock_conn = MagicMock()
        mock_imap.IMAP4_SSL.return_value = mock_conn
        mock_conn.select.return_value = ("OK", [b"0"])
        mock_conn.search.return_value = ("OK", [b""])
        yield inst


@contextmanager
def copernicus_empty():
    inst = _make_copernicus()
    with patch.object(Copernicus_marine_s3,
                      'get_s3_urls_from_stac') as mock_stac:
        mock_stac.return_value = {}
        yield inst


@contextmanager
def usgs_empty():
    inst = _make_usgs()
    inst.sitecodes = []
    with patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen'):
        yield inst


# ===========================================================================
# Context-manager factories — error → happy (recovery)
# Yields (instance, reconfigure_callback).
# ===========================================================================

@contextmanager
def mail_error_then_happy():
    inst = _make_mail_instance()
    with patch("sarracenia.flowcb.poll.mail.imaplib") as mock_imap:
        mock_imap.IMAP4_SSL.side_effect = imaplib.IMAP4.error("refused")
        mock_imap.IMAP4 = imaplib.IMAP4

        def reconfigure():
            raw = _raw_email("Recovered")
            mock_conn = MagicMock()
            mock_imap.IMAP4_SSL.side_effect = None
            mock_imap.IMAP4_SSL.return_value = mock_conn
            mock_conn.select.return_value = ("OK", [b"1"])
            mock_conn.search.return_value = ("OK", [b"1"])
            mock_conn.fetch.return_value = ("OK", [(b"1", raw)])

        yield inst, reconfigure


@contextmanager
def copernicus_error_then_happy():
    inst = _make_copernicus()
    s3_url = ('https://s3.waw3-1.cloudferro.com'
              '/mdl-native-07/native/SEALEVEL/cmems_ds')
    now = datetime.datetime(2024, 1, 15, 12, 0, 0)
    with patch.object(Copernicus_marine_s3,
                      'get_s3_urls_from_stac') as mock_stac, \
         patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client') \
            as mock_boto:
        mock_stac.return_value = {'PROD': [s3_url]}
        mock_boto.side_effect = Exception('S3 unavailable')

        def reconfigure():
            mock_boto.side_effect = None
            mock_stac.return_value = {'SEALEVEL': [s3_url]}
            fake_page = _s3_page([{
                'Key': 'native/SEALEVEL/cmems_ds/file1.nc',
                'LastModified': now, 'Size': 1024,
            }])
            paginator = MagicMock()
            paginator.paginate.return_value = [fake_page]
            s3_client = MagicMock()
            s3_client.get_paginator.return_value = paginator
            mock_boto.return_value = s3_client

        yield inst, reconfigure


@contextmanager
def usgs_error_then_happy():
    inst = _make_usgs()
    with patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen') as m:
        m.return_value = _mock_urlopen_resp(403)

        def reconfigure():
            m.return_value = _mock_urlopen_resp(200)

        yield inst, reconfigure


# ===========================================================================
# Parametrize ID lists
# ===========================================================================

HAPPY = [mail_happy, copernicus_happy, usgs_happy]
ERROR = [mail_error, copernicus_error, usgs_error]
EMPTY = [mail_empty, copernicus_empty, usgs_empty]
RECOVERY = [
    mail_error_then_happy, copernicus_error_then_happy,
    usgs_error_then_happy,
]
IDS = ['mail', 'copernicus', 'usgs']


# ===========================================================================
# Test_Poll_Contract_return_type
# ===========================================================================

class Test_Poll_Contract_return_type:

    @pytest.mark.parametrize("factory", HAPPY, ids=IDS)
    def test_poll_happy_returns_list(self, factory):
        """poll() must return a list on the happy path.
        (mail returns None only on credential failure — tested separately.)"""
        with factory() as inst:
            result = inst.poll()
        assert isinstance(result, list)

    @pytest.mark.parametrize("factory", ERROR, ids=IDS)
    def test_poll_error_returns_list_or_none(self, factory):
        """When the external service fails, poll() should return list or
        None — never raise an unhandled exception."""
        with factory() as inst:
            result = inst.poll()
        assert result is None or isinstance(result, list)

    @pytest.mark.parametrize("factory", EMPTY, ids=IDS)
    def test_poll_empty_returns_list(self, factory):
        """When no data is available, poll() returns an empty list."""
        with factory() as inst:
            result = inst.poll()
        assert isinstance(result, list)
        assert len(result) == 0


# ===========================================================================
# Test_Poll_Contract_no_stale_state
# ===========================================================================

class Test_Poll_Contract_no_stale_state:

    @pytest.mark.parametrize("factory", HAPPY, ids=IDS)
    def test_repeated_poll_no_stale_messages(self, factory):
        """Calling poll() twice with the same mock returns the same count
        (no message accumulation across invocations)."""
        with factory() as inst:
            result1 = inst.poll()
            result2 = inst.poll()
        count1 = len(result1) if result1 is not None else 0
        count2 = len(result2) if result2 is not None else 0
        assert count1 == count2

    @pytest.mark.parametrize("factory", RECOVERY, ids=IDS)
    def test_poll_after_error_resets_cleanly(self, factory):
        """After an error call, a subsequent successful call still works."""
        with factory() as (inst, reconfigure):
            err_result = inst.poll()
            assert err_result is None or isinstance(err_result, list)
            reconfigure()
            ok_result = inst.poll()
        assert isinstance(ok_result, list)
        assert len(ok_result) > 0


# ===========================================================================
# Test_Poll_Contract_stop_requested
# ===========================================================================

class Test_Poll_Contract_stop_requested:

    def test_copernicus_stop_requested_breaks(self):
        """Only copernicus checks stop_requested during S3 pagination."""
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/mybucket/prefix/data'
        now = datetime.datetime(2024, 1, 15, 12, 0, 0)
        page1 = _s3_page([{
            'Key': 'prefix/data/file1.nc',
            'LastModified': now, 'Size': 100,
        }])

        def pages_gen():
            yield page1
            inst.stop_requested = True
            yield _s3_page([{
                'Key': 'prefix/data/file2.nc',
                'LastModified': now, 'Size': 200,
            }])

        with patch.object(Copernicus_marine_s3,
                          'get_s3_urls_from_stac') as mock_stac, \
             patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client') \
                as mock_boto:
            mock_stac.return_value = {'PROD': [s3_url]}
            paginator = MagicMock()
            paginator.paginate.return_value = pages_gen()
            s3_client = MagicMock()
            s3_client.get_paginator.return_value = paginator
            mock_boto.return_value = s3_client
            msgs = inst.poll()
        # stop_requested is checked after processing each page body, so
        # both pages are consumed before the break fires.
        assert len(msgs) == 2


# ===========================================================================
# Test_Poll_Contract_message_shape
# ===========================================================================

class Test_Poll_Contract_message_shape:

    @pytest.mark.parametrize("factory", HAPPY, ids=IDS)
    def test_messages_are_sarracenia_messages(self, factory):
        """Returned items must be sarracenia.Message instances
        (a dict subclass)."""
        with factory() as inst:
            result = inst.poll()
        assert isinstance(result, list) and len(result) > 0
        for msg in result:
            assert isinstance(msg, dict), \
                f"Expected dict-like Message, got {type(msg)}"

    @pytest.mark.parametrize("factory", HAPPY, ids=IDS)
    def test_messages_have_baseUrl(self, factory):
        """Each message must contain a 'baseUrl' key."""
        with factory() as inst:
            result = inst.poll()
        assert isinstance(result, list) and len(result) > 0
        for msg in result:
            assert 'baseUrl' in msg, \
                f"Message missing 'baseUrl': {dict(msg)}"


# ===========================================================================
# Test_Poll_Contract_malformed_input
# ===========================================================================

class Test_Poll_Contract_malformed_input:

    def test_usgs_empty_stations_returns_empty(self):
        """USGS with no sitecodes returns an empty list."""
        inst = _make_usgs()
        inst.sitecodes = []
        with patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen') as m:
            msgs = inst.poll()
        assert msgs == []
        m.assert_not_called()

    def test_copernicus_empty_productIDs_returns_empty(self):
        """Copernicus with no productIDs returns an empty list."""
        inst = _make_copernicus(productID=[])
        with patch.object(Copernicus_marine_s3,
                          'get_s3_urls_from_stac') as mock_stac, \
             patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client'):
            mock_stac.return_value = {}
            msgs = inst.poll()
        assert msgs == []

    def test_mail_bad_credentials_returns_none(self):
        """Known return-type inconsistency: mail returns None (not [])
        when credentials.get fails.  The caller may expect a list."""
        inst = _make_mail_instance()
        inst.o.credentials.get.return_value = (False, None)
        result = inst.poll()
        assert result is None
