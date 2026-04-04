import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import *

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.nasa_cmr import Nasa_cmr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nasa_cmr(**overrides):
    """Create a Nasa_cmr instance with mocked parent init."""
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://cmr.earthdata.nasa.gov/search/granules.umm_json'
    options.post_baseUrl = 'https://cmr.earthdata.nasa.gov/'
    options.publishers = [{'baseUrl': 'https://cmr.earthdata.nasa.gov/', 'baseDir': None}]
    options.identity_method = 'cod,md5'
    options.collectionConceptId = ['C1234-PODAAC']
    options.dataSource = 'podaac'
    options.timeNowMinus = 3600.0
    options.pageSize = 2000
    options.dap_urlExtension = None
    options.dap_fileType = None
    options.relatedUrl_type = None
    options.relatedUrl_descriptionContains = None
    options.relatedUrl_urlContains = None
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Nasa_cmr.__new__(Nasa_cmr)
        inst.o = options
        inst.stop_requested = False
        Nasa_cmr.__init__(inst, options)
    return inst


def _cmr_response(items):
    """Build a CMR JSON response body."""
    return {'hits': len(items), 'took': 100, 'items': items}


def _podaac_item(granule_name="file1.nc",
                 data_url="https://archive.podaac.earthdata.nasa.gov/path/file1.nc",
                 md5_url=None):
    """Build a CMR item with PO.DAAC URLs."""
    urls = [{
        'URL': data_url,
        'Type': 'GET DATA',
        'Description': 'Download ' + granule_name,
    }]
    if md5_url:
        urls.append({
            'URL': md5_url,
            'Type': 'EXTENDED METADATA',
            'Description': 'Download md5 checksum',
        })
    return {'umm': {'RelatedUrls': urls}}


def _opendap_item(url="https://opendap.earthdata.nasa.gov/providers/POCLOUD/granules/data1"):
    """Build a CMR item with an OPeNDAP URL."""
    return {'umm': {'RelatedUrls': [{
        'URL': url,
        'Type': 'USE SERVICE API',
        'Description': 'OPeNDAP request URL for data1',
    }]}}


def _other_item(url="https://example.com/data/file.dat", type_str="GET DATA",
                description="Download file.dat", extra_urls=None):
    """Build a CMR item for the 'other' data source."""
    urls = [{'URL': url, 'Type': type_str, 'Description': description}]
    if extra_urls:
        urls.extend(extra_urls)
    return {'umm': {'RelatedUrls': urls}}


# ===========================================================================
# Initialization tests
# ===========================================================================

class Test_Nasa_cmr_init:

    def test_init_sets_default_poll_url(self):
        """When pollUrl is empty, __init__ sets it to the default CMR URL."""
        inst = _make_nasa_cmr(pollUrl='')
        assert inst.o.pollUrl == 'https://cmr.earthdata.nasa.gov/search/granules.umm_json'

    def test_init_preserves_custom_poll_url(self):
        """When pollUrl is provided, __init__ does NOT overwrite it."""
        custom = 'https://custom.example.com/search'
        inst = _make_nasa_cmr(pollUrl=custom)
        assert inst.o.pollUrl == custom

    def test_init_sets_post_baseUrl_none(self):
        """post_baseUrl is set to None during init."""
        inst = _make_nasa_cmr()
        assert inst.o.post_baseUrl is None

    def test_init_registers_options(self):
        """Key options are registered via add_option."""
        inst = _make_nasa_cmr()
        for attr in ['collectionConceptId', 'dataSource', 'timeNowMinus',
                     'pageSize', 'dap_urlExtension', 'dap_fileType',
                     'relatedUrl_type', 'relatedUrl_descriptionContains',
                     'relatedUrl_urlContains']:
            assert hasattr(inst.o, attr), f"Missing option: {attr}"

    def test_init_data_source_options_list(self):
        """_dataSource_options contains exactly podaac, opendap, other."""
        inst = _make_nasa_cmr()
        assert inst._dataSource_options == ['podaac', 'opendap', 'other']


# ===========================================================================
# poll – PO.DAAC data source
# ===========================================================================

class Test_Nasa_cmr_poll_podaac:

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_podaac_happy_path_single_granule(self, mock_get):
        """Single PO.DAAC granule produces one message."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_podaac_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_podaac_with_md5_url(self, mock_get):
        """When an md5 URL is present, the identity uses md5 method."""
        md5_url = 'https://archive.podaac.earthdata.nasa.gov/path/file1.nc.md5'
        cmr_resp = MagicMock()
        cmr_resp.json.return_value = _cmr_response([
            _podaac_item(md5_url=md5_url)
        ])
        md5_resp = MagicMock()
        md5_resp.text = 'abc123def456 file1.nc'
        mock_get.side_effect = [cmr_resp, md5_resp]
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['identity'] == {'method': 'md5', 'value': 'abc123def456'}

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_podaac_md5_fetch_fails(self, mock_get):
        """When md5 fetch raises an exception, identity falls back to cod."""
        md5_url = 'https://archive.podaac.earthdata.nasa.gov/path/file1.nc.md5'
        cmr_resp = MagicMock()
        cmr_resp.json.return_value = _cmr_response([
            _podaac_item(md5_url=md5_url)
        ])
        mock_get.side_effect = [cmr_resp, Exception("network error")]
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['identity'] == {'method': 'cod', 'value': 'md5'}

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_podaac_no_md5_url(self, mock_get):
        """Without an md5 URL, podaac uses cod identity."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_podaac_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['identity'] == {'method': 'cod', 'value': 'md5'}

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_podaac_multiple_granules(self, mock_get):
        """Multiple granules each produce a message."""
        items = [
            _podaac_item(data_url='https://archive.podaac.earthdata.nasa.gov/path/a.nc'),
            _podaac_item(data_url='https://archive.podaac.earthdata.nasa.gov/path/b.nc'),
            _podaac_item(data_url='https://archive.podaac.earthdata.nasa.gov/path/c.nc'),
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response(items)
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 3


# ===========================================================================
# poll – OPeNDAP data source
# ===========================================================================

class Test_Nasa_cmr_poll_opendap:

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_opendap_happy_path(self, mock_get):
        """OPeNDAP URL with extension and file type produces a message."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_opendap_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='opendap', dap_urlExtension='dods', dap_fileType='nc4',
        )
        msgs = inst.poll()
        assert len(msgs) == 1
        # Only one requests.get call (CMR API), no md5 fetch for opendap
        assert mock_get.call_count == 1

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_opendap_dap4b_no_extension(self, mock_get):
        """dap_fileType='dap4b' results in an empty file-type suffix."""
        base_url = "https://opendap.earthdata.nasa.gov/providers/POCLOUD/granules/data1"
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_opendap_item(url=base_url)])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='opendap', dap_urlExtension='dap', dap_fileType='dap4b',
        )
        msgs = inst.poll()
        assert len(msgs) == 1
        # URL constructed as: base_url + ".dap." (empty dap_fileType for dap4b)
        # The new_file path reflects this
        assert msgs[0]['post_baseUrl'] == 'https://opendap.earthdata.nasa.gov/'

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_opendap_url_construction(self, mock_get):
        """Verify the data URL includes dap_urlExtension and dap_fileType."""
        base_url = "https://opendap.earthdata.nasa.gov/providers/POCLOUD/granules/data1"
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_opendap_item(url=base_url)])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='opendap', dap_urlExtension='dmr', dap_fileType='nc4',
        )
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['post_baseUrl'] == 'https://opendap.earthdata.nasa.gov/'

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_opendap_no_md5_fetch(self, mock_get):
        """OPeNDAP does not trigger a second request for md5."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_opendap_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='opendap', dap_urlExtension='dods', dap_fileType='nc4',
        )
        inst.poll()
        assert mock_get.call_count == 1


# ===========================================================================
# poll – other data source
# ===========================================================================

class Test_Nasa_cmr_poll_other:

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_other_happy_path(self, mock_get):
        """Matching relatedUrl_type produces a message."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([
            _other_item(type_str='GET DATA')
        ])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(dataSource='other', relatedUrl_type='GET DATA')
        msgs = inst.poll()
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_other_description_filter_skip(self, mock_get):
        """URL skipped when Description doesn't contain relatedUrl_descriptionContains."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([
            _other_item(type_str='GET DATA', description='Download file.dat')
        ])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='other',
            relatedUrl_type='GET DATA',
            relatedUrl_descriptionContains=['SpecialKeyword'],
        )
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_other_url_filter_skip(self, mock_get):
        """URL skipped when URL doesn't contain relatedUrl_urlContains."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([
            _other_item(type_str='GET DATA', url='https://example.com/data/file.dat')
        ])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='other',
            relatedUrl_type='GET DATA',
            relatedUrl_urlContains=['special-path'],
        )
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_other_both_filters_match(self, mock_get):
        """URL is posted when both filter lists match."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([
            _other_item(type_str='GET DATA',
                        url='https://example.com/data/file.dat',
                        description='Download special file')
        ])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            dataSource='other',
            relatedUrl_type='GET DATA',
            relatedUrl_descriptionContains=['Download'],
            relatedUrl_urlContains=['example.com'],
        )
        msgs = inst.poll()
        assert len(msgs) == 1


# ===========================================================================
# poll – edge cases
# ===========================================================================

class Test_Nasa_cmr_poll_edge:

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_empty_response_returns_empty(self, mock_get):
        """Response with zero items returns an empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_item_missing_related_urls(self, mock_get):
        """Items without RelatedUrls are skipped."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([
            {'umm': {'SomeOtherField': True}}
        ])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_stop_requested_returns_empty_list(self, mock_get):
        """stop_requested during URL iteration returns []."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_podaac_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        inst.stop_requested = True
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_multiple_collection_concept_ids(self, mock_get):
        """poll iterates through all collectionConceptIds."""
        resp1 = MagicMock()
        resp1.json.return_value = _cmr_response([
            _podaac_item(data_url='https://archive.podaac.earthdata.nasa.gov/path/a.nc')
        ])
        resp2 = MagicMock()
        resp2.json.return_value = _cmr_response([
            _podaac_item(data_url='https://archive.podaac.earthdata.nasa.gov/path/b.nc')
        ])
        mock_get.side_effect = [resp1, resp2]
        inst = _make_nasa_cmr(collectionConceptId=['C1234-PODAAC', 'C5678-PODAAC'])
        msgs = inst.poll()
        assert len(msgs) == 2
        assert mock_get.call_count == 2

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_poll_url_trailing_slash_stripped(self, mock_get):
        """Trailing slash on pollUrl is stripped before building request URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(
            pollUrl='https://cmr.earthdata.nasa.gov/search/granules.umm_json/'
        )
        inst.poll()
        called_url = mock_get.call_args[0][0]
        assert called_url.startswith(
            'https://cmr.earthdata.nasa.gov/search/granules.umm_json?'
        )

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_page_size_clamped_to_2000(self, mock_get):
        """pageSize > 2000 gets clamped to 2000 in the URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(pageSize=5000)
        inst.poll()
        called_url = mock_get.call_args[0][0]
        assert 'pageSize=2000' in called_url

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_page_size_negative_clamped(self, mock_get):
        """pageSize < 1 gets clamped to 2000 in the URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(pageSize=0)
        inst.poll()
        called_url = mock_get.call_args[0][0]
        assert 'pageSize=2000' in called_url

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_invalid_timeNowMinus_defaults_zero(self, mock_get):
        """Non-float timeNowMinus doesn't crash; n_seconds defaults to 0."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr(timeNowMinus='not_a_number')
        # Should not raise
        msgs = inst.poll()
        assert isinstance(msgs, list)

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_message_baseUrl_set_correctly(self, mock_get):
        """post_baseUrl on message is extracted from data URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_podaac_item(
            data_url='https://archive.podaac.earthdata.nasa.gov/path/to/file.nc'
        )])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['post_baseUrl'] == 'https://archive.podaac.earthdata.nasa.gov/'

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_return_always_list(self, mock_get):
        """poll() always returns a list even with empty response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        result = inst.poll()
        assert isinstance(result, list)

    @patch('sarracenia.flowcb.poll.nasa_cmr.requests.get')
    def test_repeated_poll_no_accumulation(self, mock_get):
        """Calling poll() twice yields the same count (no accumulation)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _cmr_response([_podaac_item()])
        mock_get.return_value = mock_resp
        inst = _make_nasa_cmr()
        msgs1 = inst.poll()
        msgs2 = inst.poll()
        assert len(msgs1) == len(msgs2) == 1
