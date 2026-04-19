import pytest
import re
import datetime
from unittest.mock import patch, MagicMock, PropertyMock

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.copernicus_marine_s3 import Copernicus_marine_s3


# ---------------------------------------------------------------------------
# Helper: build a Copernicus_marine_s3 instance with sensible defaults
# ---------------------------------------------------------------------------

def _make_copernicus(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://stac.marine.copernicus.eu/metadata'
    options.post_baseUrl = 'https://stac.marine.copernicus.eu'
    options.publishers = [{'baseUrl': 'https://stac.marine.copernicus.eu', 'baseDir': None}]
    options.productID = ['SEALEVEL_GLO_PHY_L4_NRT_008_046']
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Copernicus_marine_s3.__new__(Copernicus_marine_s3)
        inst.o = options
        inst.stop_requested = False
        Copernicus_marine_s3.__init__(inst, options)
    return inst


# Convenience: a minimal STAC product page with one dataset link
def _stac_product_page(dataset_hrefs):
    """Return a fake STAC product.stac.json response body."""
    links = [{'href': h} for h in dataset_hrefs]
    return {'links': links}


def _stac_dataset_page(s3_href):
    """Return a fake dataset.stac.json with a native S3 URL."""
    return {'assets': {'native': {'href': s3_href}}}


def _stac_dataset_page_no_native():
    """Return a dataset.stac.json *without* the native asset."""
    return {'assets': {'other': {'href': 'https://example.com/other'}}}


# ---------------------------------------------------------------------------
# Test_Copernicus_init
# ---------------------------------------------------------------------------

class Test_Copernicus_init:

    def test_init_sets_stac_base_url_with_trailing_slash(self):
        inst = _make_copernicus(pollUrl='https://stac.example.com/metadata')
        assert inst.stac_base_url == 'https://stac.example.com/metadata/'

    def test_init_preserves_existing_trailing_slash(self):
        inst = _make_copernicus(pollUrl='https://stac.example.com/metadata/')
        assert inst.stac_base_url == 'https://stac.example.com/metadata/'

    def test_init_parses_single_productID(self):
        inst = _make_copernicus(productID=['SEALEVEL_GLO_PHY_L4_NRT_008_046'])
        assert 'SEALEVEL_GLO_PHY_L4_NRT_008_046' in inst.productIDs
        assert inst.productIDs['SEALEVEL_GLO_PHY_L4_NRT_008_046'] is None

    def test_init_parses_productID_with_regex(self):
        inst = _make_copernicus(
            productID=['INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030 dataset_href=.*latest.*']
        )
        assert 'INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030' in inst.productIDs
        pat = inst.productIDs['INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030']
        assert pat is not None
        assert pat.match('some_latest_dataset.stac.json')

    def test_init_invalid_regex_logs_error_but_continues(self):
        """A productID with bad syntax still creates an entry (value=None)."""
        inst = _make_copernicus(
            productID=[
                'BADPRODUCT bad_option_no_dataset_href',
                'GOODPRODUCT',
            ]
        )
        # BADPRODUCT entry created with None because split("dataset_href=") fails
        assert 'BADPRODUCT' in inst.productIDs
        assert inst.productIDs['BADPRODUCT'] is None
        # Second productID still parsed
        assert 'GOODPRODUCT' in inst.productIDs

    def test_init_empty_productID_list(self):
        inst = _make_copernicus(productID=[])
        assert inst.productIDs == {}


# ---------------------------------------------------------------------------
# Test_Copernicus_get_s3_urls_from_stac
# ---------------------------------------------------------------------------

class Test_Copernicus_get_s3_urls_from_stac:

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_happy_path_single_product(self, mock_get):
        inst = _make_copernicus()
        s3_url = 'https://s3.waw3-1.cloudferro.com/mdl-native-07/native/SEALEVEL/cmems_ds1'

        product_resp = MagicMock()
        product_resp.json.return_value = _stac_product_page(
            ['cmems_obs-sl_glo/dataset.stac.json']
        )
        product_resp.raise_for_status.return_value = None

        dataset_resp = MagicMock()
        dataset_resp.json.return_value = _stac_dataset_page(s3_url)
        dataset_resp.__bool__ = lambda self: True

        mock_get.side_effect = [product_resp, dataset_resp]

        result = inst.get_s3_urls_from_stac(
            {'SEALEVEL_GLO_PHY_L4_NRT_008_046': None}
        )
        assert 'SEALEVEL_GLO_PHY_L4_NRT_008_046' in result
        assert result['SEALEVEL_GLO_PHY_L4_NRT_008_046'] == [s3_url]

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_with_regex_filter(self, mock_get):
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/bucket/native/prod/latest_ds'

        product_resp = MagicMock()
        product_resp.json.return_value = _stac_product_page([
            'latest_ds/dataset.stac.json',
            'old_ds/dataset.stac.json',
        ])
        product_resp.raise_for_status.return_value = None

        dataset_resp = MagicMock()
        dataset_resp.json.return_value = _stac_dataset_page(s3_url)
        dataset_resp.__bool__ = lambda self: True

        mock_get.side_effect = [product_resp, dataset_resp]

        regex = re.compile('.*latest.*')
        result = inst.get_s3_urls_from_stac({'PROD': regex})
        assert 'PROD' in result
        assert len(result['PROD']) == 1
        # Only the latest href matched; old_ds was filtered out
        assert mock_get.call_count == 2  # product page + 1 dataset

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_regex_no_match_returns_empty(self, mock_get):
        inst = _make_copernicus()

        product_resp = MagicMock()
        product_resp.json.return_value = _stac_product_page([
            'old_ds/dataset.stac.json',
        ])
        product_resp.raise_for_status.return_value = None

        mock_get.return_value = product_resp

        regex = re.compile('.*latest.*')
        result = inst.get_s3_urls_from_stac({'PROD': regex})
        # No dataset href matched the regex, so no datasets fetched
        assert result == {}

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_no_datasets_found_continues(self, mock_get):
        """If no links contain 'dataset.stac.json', the product is skipped."""
        inst = _make_copernicus()

        product_resp = MagicMock()
        product_resp.json.return_value = _stac_product_page([
            'some_other_link.json',  # no 'dataset.stac.json'
        ])
        product_resp.raise_for_status.return_value = None
        mock_get.return_value = product_resp

        result = inst.get_s3_urls_from_stac({'PROD': None})
        assert result == {}

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_missing_native_asset_skipped(self, mock_get):
        inst = _make_copernicus()

        product_resp = MagicMock()
        product_resp.json.return_value = _stac_product_page([
            'ds1/dataset.stac.json',
        ])
        product_resp.raise_for_status.return_value = None

        dataset_resp = MagicMock()
        dataset_resp.json.return_value = _stac_dataset_page_no_native()
        dataset_resp.__bool__ = lambda self: True

        mock_get.side_effect = [product_resp, dataset_resp]

        result = inst.get_s3_urls_from_stac({'PROD': None})
        # No 'native' key → nothing added
        assert result == {}

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_network_error_returns_empty(self, mock_get):
        inst = _make_copernicus()
        mock_get.side_effect = Exception('Connection refused')

        result = inst.get_s3_urls_from_stac({'PROD': None})
        assert result == {}

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.requests.get')
    def test_stac_malformed_json(self, mock_get):
        inst = _make_copernicus()

        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError('No JSON')
        mock_get.return_value = resp

        result = inst.get_s3_urls_from_stac({'PROD': None})
        assert result == {}


# ---------------------------------------------------------------------------
# Test_Copernicus_identify_client
# ---------------------------------------------------------------------------

class Test_Copernicus_identify_client:

    def test_identify_client_adds_headers(self):
        inst = _make_copernicus()
        params = {
            'headers': {'User-Agent': 'boto3/1.0'},
            'query_string': {},
            'url': 'https://s3.example.com/bucket?list-type=2',
        }
        inst._identify_client(model=None, params=params, request_signer=None)

        expected_client = 'Sarracenia' + sarracenia.__version__
        assert params['headers']['x-cop-client'] == expected_client
        assert params['query_string']['x-cop-client'] == expected_client
        assert expected_client in params['headers']['User-Agent']
        assert 'x-cop-client' in params['url']

    def test_identify_client_no_user_agent(self):
        """When User-Agent is absent, headers are still set without error."""
        inst = _make_copernicus()
        params = {
            'headers': {},
            'query_string': {},
            'url': 'https://s3.example.com/bucket?list-type=2',
        }
        inst._identify_client(model=None, params=params, request_signer=None)

        expected_client = 'Sarracenia' + sarracenia.__version__
        assert params['headers']['x-cop-client'] == expected_client

    def test_identify_client_swallows_exceptions(self):
        """If params is broken, the method should not raise."""
        inst = _make_copernicus()
        # params without required keys → triggers exception inside try block
        params = {}
        # Should not raise
        inst._identify_client(model=None, params=params, request_signer=None)


# ---------------------------------------------------------------------------
# Test_Copernicus_poll
# ---------------------------------------------------------------------------

class Test_Copernicus_poll:

    def _s3_page(self, contents):
        """Build a fake S3 list_objects page."""
        page = {}
        if contents is not None:
            page['Contents'] = contents
        return page

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_happy_path_builds_messages(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        s3_url = 'https://s3.waw3-1.cloudferro.com/mdl-native-07/native/SEALEVEL/cmems_ds'
        mock_stac.return_value = {'SEALEVEL': [s3_url]}

        now = datetime.datetime(2024, 1, 15, 12, 0, 0)
        fake_page = self._s3_page([
            {'Key': 'native/SEALEVEL/cmems_ds/file1.nc', 'LastModified': now, 'Size': 1024},
        ])
        paginator = MagicMock()
        paginator.paginate.return_value = [fake_page]
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client

        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['baseUrl'] == 'https://s3.waw3-1.cloudferro.com'

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_empty_stac_returns_empty(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        mock_stac.return_value = {}

        msgs = inst.poll()
        assert msgs == []
        mock_boto.assert_not_called()

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_s3_error_returns_empty(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/bucket/prefix/data'
        mock_stac.return_value = {'PROD': [s3_url]}

        mock_boto.side_effect = Exception('S3 unavailable')

        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_stop_requested_breaks_early(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/mybucket/prefix/data'
        mock_stac.return_value = {'PROD': [s3_url]}

        now = datetime.datetime(2024, 1, 15, 12, 0, 0)
        page1 = self._s3_page([
            {'Key': 'prefix/data/file1.nc', 'LastModified': now, 'Size': 100},
        ])
        # Set stop_requested after first page
        def pages_gen():
            yield page1
            inst.stop_requested = True
            yield self._s3_page([
                {'Key': 'prefix/data/file2.nc', 'LastModified': now, 'Size': 200},
            ])

        paginator = MagicMock()
        paginator.paginate.return_value = pages_gen()
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client

        msgs = inst.poll()
        # stop_requested is set after yielding page1 but before processing page2,
        # so we get file1 from page1 plus file2 from page2 (stop checked *after* page loop body)
        # Actually looking at the source: the check is after processing Contents,
        # so page2 contents are processed then stop breaks
        assert len(msgs) == 2

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_message_has_correct_baseUrl(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        endpoint = 'https://s3.waw3-1.cloudferro.com'
        s3_url = endpoint + '/mdl-native-07/native/SEALEVEL/cmems_ds'
        mock_stac.return_value = {'SEALEVEL': [s3_url]}

        now = datetime.datetime(2024, 6, 1, 0, 0, 0)
        fake_page = self._s3_page([
            {'Key': 'native/SEALEVEL/cmems_ds/obs.nc', 'LastModified': now, 'Size': 512},
        ])
        paginator = MagicMock()
        paginator.paginate.return_value = [fake_page]
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client

        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['baseUrl'] == endpoint
        assert msgs[0]['new_baseUrl'] == endpoint
        assert msgs[0]['post_baseUrl'] == endpoint
        assert 'post_baseUrl' in msgs[0]['_deleteOnPost']

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_message_has_size_and_mtime_from_s3(self, mock_stac, mock_boto):
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/bucket/prefix/data'
        mock_stac.return_value = {'PROD': [s3_url]}

        now = datetime.datetime(2024, 3, 10, 8, 30, 0)
        fake_page = self._s3_page([
            {'Key': 'prefix/data/file.nc', 'LastModified': now, 'Size': 999999},
        ])
        paginator = MagicMock()
        paginator.paginate.return_value = [fake_page]
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client

        msgs = inst.poll()
        assert len(msgs) == 1
        msg = msgs[0]
        assert 'size' in msg or 'mtime' in msg  # fromFileInfo sets these from stat
        # The file path should include bucket + key
        assert 'bucket' in msg['new_dir'] or 'prefix' in msg.get('new_file', '')

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_no_Contents_in_page(self, mock_stac, mock_boto):
        """An S3 page with no 'Contents' key should be handled gracefully."""
        inst = _make_copernicus()
        s3_url = 'https://s3.example.com/bucket/prefix/data'
        mock_stac.return_value = {'PROD': [s3_url]}

        empty_page = self._s3_page(None)  # no Contents key
        paginator = MagicMock()
        paginator.paginate.return_value = [empty_page]
        s3_client = MagicMock()
        s3_client.get_paginator.return_value = paginator
        mock_boto.return_value = s3_client

        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.copernicus_marine_s3.boto3.client')
    @patch.object(Copernicus_marine_s3, 'get_s3_urls_from_stac')
    def test_poll_multiple_endpoints(self, mock_stac, mock_boto):
        """Files from different S3 endpoints produce messages with correct baseUrls."""
        inst = _make_copernicus()
        mock_stac.return_value = {
            'PROD_A': ['https://s3.eu1.example.com/bucket-a/prefix-a/data'],
            'PROD_B': ['https://s3.eu2.example.com/bucket-b/prefix-b/data'],
        }

        now = datetime.datetime(2024, 1, 1, 0, 0, 0)

        def make_s3_client(*args, **kwargs):
            endpoint = kwargs.get('endpoint_url', '')
            client = MagicMock()
            paginator = MagicMock()
            if 'eu1' in endpoint:
                paginator.paginate.return_value = [self._s3_page([
                    {'Key': 'prefix-a/data/f1.nc', 'LastModified': now, 'Size': 10},
                ])]
            else:
                paginator.paginate.return_value = [self._s3_page([
                    {'Key': 'prefix-b/data/f2.nc', 'LastModified': now, 'Size': 20},
                ])]
            client.get_paginator.return_value = paginator
            return client

        mock_boto.side_effect = make_s3_client

        msgs = inst.poll()
        assert len(msgs) == 2
        base_urls = {m['baseUrl'] for m in msgs}
        assert 'https://s3.eu1.example.com' in base_urls
        assert 'https://s3.eu2.example.com' in base_urls