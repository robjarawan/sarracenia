import pytest
from tests.conftest import *
from unittest.mock import patch, MagicMock
import datetime
import json

import sarracenia
import sarracenia.config
import sarracenia.flowcb.poll.odata
from sarracenia.flowcb.poll.odata import Odata


def _make_odata(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://example.com/odata/v1/Products?$filter='
    options.post_baseUrl = 'https://example.com/odata/v1/'
    options.publishers = [{'baseUrl': 'https://example.com/odata/v1/', 'baseDir': None}]
    options.post_urlTemplate = 'Products(--PRODUCT_ID--)/$value'
    options.dataCollection = None
    options.queryString = None
    options.timeNowMinus = 3600.0
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Odata.__new__(Odata)
        inst.o = options
        inst.stop_requested = False
        Odata.__init__(inst, options)
    return inst


SAMPLE_PRODUCT = {
    '@odata.mediaContentType': 'application/octet-stream',
    'Id': 'd86d00a2-58bc-4603-9e6a-28bc571d79a6',
    'Name': 'S1A_IW_GRDH_1SDV_20230425T123926_20230425T123951_048253_05CD62_EA4E_COG.SAFE',
    'ContentType': 'application/octet-stream',
    'ContentLength': 1229909379,
    'OriginDate': '2023-05-06T12:36:57.469Z',
    'PublicationDate': '2023-05-06T12:42:38.119Z',
    'ModificationDate': '2023-05-06T12:42:38.119Z',
    'Online': True,
    'EvictionDate': '',
    'S3Path': '/eodata/Sentinel-1/SAR/IW_GRDH_1S-COG/2023/04/25/S1A.SAFE',
    'Checksum': [
        {'Algorithm': 'MD5', 'Value': 'abc123def456', 'Date': '2023-05-06T12:42:38.119Z'},
        {'Algorithm': 'BLAKE3', 'Value': 'blake3hash', 'Date': '2023-05-06T12:42:38.119Z'},
    ],
    'ContentDate': {'Start': '2023-04-25T12:39:26.902Z', 'End': '2023-04-25T12:39:51.900Z'},
    'Footprint': "geography'SRID=4326;POLYGON ((79.757111 29.038794))'",
    'GeoFootprint': {
        'type': 'Polygon',
        'coordinates': [[[79.757111, 29.038794], [82.362259, 29.452579],
                         [82.123009, 30.965038], [79.47876, 30.555256],
                         [79.757111, 29.038794]]]
    },
}


class Test_parse_json_to_msg:

    def test_basic_product(self):
        """Full product with all fields should produce a correct message."""
        inst = _make_odata()
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg is not None
        assert msg['relPath'] == SAMPLE_PRODUCT['Name']
        assert msg['retrievePath'] == f"Products({SAMPLE_PRODUCT['Id']})/$value"
        assert msg['size'] == 1229909379

    def test_checksum_md5(self):
        """MD5 checksum should be extracted from the Checksum list."""
        inst = _make_odata()
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg['identity'] == {'method': 'md5', 'value': 'abc123def456'}

    def test_checksum_md5_case_insensitive(self):
        """MD5 matching should be case-insensitive."""
        product = {**SAMPLE_PRODUCT, 'Checksum': [{'Algorithm': 'md5', 'Value': 'lower_md5'}]}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert msg['identity'] == {'method': 'md5', 'value': 'lower_md5'}

    def test_checksum_no_md5(self):
        """When no MD5 checksum is present, identity should not be set."""
        product = {**SAMPLE_PRODUCT, 'Checksum': [{'Algorithm': 'BLAKE3', 'Value': 'bbb'}]}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'identity' not in msg

    def test_checksum_empty_list(self):
        """Empty Checksum list should not set identity."""
        product = {**SAMPLE_PRODUCT, 'Checksum': []}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'identity' not in msg

    def test_geofootprint(self):
        """GeoFootprint should set type=Feature and geometry."""
        inst = _make_odata()
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg['type'] == 'Feature'
        assert msg['geometry'] == SAMPLE_PRODUCT['GeoFootprint']

    def test_no_geofootprint(self):
        """Without GeoFootprint, type and geometry should not be set."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'GeoFootprint'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'geometry' not in msg

    def test_modification_date_normalization(self):
        """ModificationDate should be normalized by stripping Z, colons, and dashes."""
        inst = _make_odata()
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        # '2023-05-06T12:42:38.119Z' -> '20230506T124238.119'
        assert msg['mtime'] == '20230506T124238.119'

    def test_no_modification_date(self):
        """Without ModificationDate, mtime should not be set."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'ModificationDate'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'mtime' not in msg

    def test_missing_name(self):
        """Without Name, relPath should remain the product URL path."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'Name'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'retrievePath' not in msg
        expected_path = f"Products({SAMPLE_PRODUCT['Id']})/$value"
        assert expected_path in msg['relPath'] or msg['new_file'] == '$value'

    def test_empty_name(self):
        """Empty Name string should not set retrievePath."""
        product = {**SAMPLE_PRODUCT, 'Name': ''}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'retrievePath' not in msg

    def test_content_type(self):
        """ContentType should be set in the message."""
        inst = _make_odata()
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg['contentType'] == 'application/octet-stream'

    def test_no_content_type(self):
        """Without ContentType, contentType should not be set."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'ContentType'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'contentType' not in msg

    def test_missing_content_length(self):
        """Without ContentLength, size should not be set."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'ContentLength'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'size' not in msg

    def test_missing_checksum_key(self):
        """Without Checksum key at all, identity should not be set."""
        product = {k: v for k, v in SAMPLE_PRODUCT.items() if k != 'Checksum'}
        inst = _make_odata()
        msg = inst.parse_json_to_msg(product)
        assert 'identity' not in msg

    def test_custom_url_template(self):
        """A custom post_urlTemplate should be used correctly."""
        inst = _make_odata(post_urlTemplate='download/--PRODUCT_ID--')
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg['retrievePath'] == f"download/{SAMPLE_PRODUCT['Id']}"

    def test_url_template_no_placeholder(self):
        """A template without --PRODUCT_ID-- should remain unchanged."""
        inst = _make_odata(post_urlTemplate='static/path')
        msg = inst.parse_json_to_msg(SAMPLE_PRODUCT)
        assert msg['retrievePath'] == 'static/path'


class Test_poll:

    def _mock_response(self, json_data, status_code=200):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.status_code = status_code
        return mock_resp

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_value_array(self, mock_get):
        """Response with 'value' array should produce multiple messages."""
        data = {'value': [SAMPLE_PRODUCT, {**SAMPLE_PRODUCT, 'Id': 'second-id'}]}
        mock_get.return_value = self._mock_response(data)
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 2
        mock_get.assert_called_once()

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_single_product(self, mock_get):
        """Response with just 'Id' (no 'value' key) should produce one message."""
        mock_get.return_value = self._mock_response(SAMPLE_PRODUCT)
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['relPath'] == SAMPLE_PRODUCT['Name']

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_empty_response(self, mock_get):
        """Response with no value and no Id should produce zero messages and log warning."""
        mock_get.return_value = self._mock_response({'something': 'else'})
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_empty_value_array(self, mock_get):
        """Response with empty 'value' array should produce zero messages."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_pagination(self, mock_get):
        """@odata.nextLink should be followed for pagination."""
        page1 = {
            'value': [SAMPLE_PRODUCT],
            '@odata.nextLink': 'https://example.com/odata/v1/Products?$skip=20',
        }
        page2 = {
            'value': [{**SAMPLE_PRODUCT, 'Id': 'page2-id'}],
        }
        mock_get.side_effect = [
            self._mock_response(page1),
            self._mock_response(page2),
        ]
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 2
        assert mock_get.call_count == 2
        # Second call should use the nextLink URL
        assert mock_get.call_args_list[1][0][0] == 'https://example.com/odata/v1/Products?$skip=20'

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_network_error(self, mock_get):
        """Network errors should be caught and return empty list."""
        mock_get.side_effect = Exception("Connection failed")
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_single_data_collection(self, mock_get):
        """Single dataCollection should appear in the URL query."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata(dataCollection=['SENTINEL-1'])
        inst.poll()
        called_url = mock_get.call_args[0][0]
        # requests.utils.quote doesn't encode '/' by default
        assert "Collection/Name%20eq%20%27SENTINEL-1%27" in called_url

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_multiple_data_collections(self, mock_get):
        """Multiple dataCollections should be combined with 'or'."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata(dataCollection=['SENTINEL-1', 'SENTINEL-2'])
        inst.poll()
        called_url = mock_get.call_args[0][0]
        # Both collections should be in the URL
        assert 'SENTINEL-1' in called_url
        assert 'SENTINEL-2' in called_url

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_query_strings(self, mock_get):
        """queryString options should be appended to the URL."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata(queryString=["and Name eq 'test'", "and Online eq true"])
        inst.poll()
        called_url = mock_get.call_args[0][0]
        assert 'test' in called_url
        assert 'Online' in called_url

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_invalid_time_now_minus(self, mock_get):
        """Invalid timeNowMinus should fall back to 0 and not crash."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata(timeNowMinus='invalid_string')
        msgs = inst.poll()
        assert isinstance(msgs, list)
        # With 0 seconds delta, start and end time should be the same
        called_url = mock_get.call_args[0][0]
        assert called_url is not None

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_time_range_in_url(self, mock_get):
        """The poll URL should contain ContentDate/Start time range filters."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata()
        inst.poll()
        called_url = mock_get.call_args[0][0]
        # requests.utils.quote doesn't encode '/' by default
        assert 'ContentDate/Start' in called_url

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_no_data_collection_no_query_string(self, mock_get):
        """With no dataCollection and no queryString, URL should only have time range."""
        mock_get.return_value = self._mock_response({'value': []})
        inst = _make_odata(dataCollection=None, queryString=None)
        inst.poll()
        called_url = mock_get.call_args[0][0]
        assert called_url.startswith('https://example.com/odata/v1/Products?$filter=')
        # Should not contain Collection/Name
        assert 'Collection' not in called_url

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_error_mid_pagination(self, mock_get):
        """Error during pagination should return messages gathered so far."""
        page1 = {
            'value': [SAMPLE_PRODUCT],
            '@odata.nextLink': 'https://example.com/odata/v1/Products?$skip=20',
        }
        mock_get.side_effect = [
            self._mock_response(page1),
            Exception("Connection lost"),
        ]
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.odata.requests.get')
    def test_json_decode_error(self, mock_get):
        """JSON decode error should be caught gracefully."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = mock_resp
        inst = _make_odata()
        msgs = inst.poll()
        assert len(msgs) == 0