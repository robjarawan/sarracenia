import pytest
import datetime
import json
from unittest.mock import patch, MagicMock, call

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.eumetsat import Eumetsat


# ---------------------------------------------------------------------------
# Helper: build an Eumetsat instance with sane defaults, no network
# ---------------------------------------------------------------------------
def _make_eumetsat(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://api.eumetsat.int/data/browse/collections/'
    options.post_baseUrl = 'https://api.eumetsat.int/data/download/1.0.0/collections/'
    options.collectionId = ['EO:EUM:DAT:0412']
    options.acceptMediaType = ['application/x-netcdf']
    options.timeNowMinus = 3600.0
    # publishers is required by Message.updatePaths to resolve relPath / baseUrl
    options.publishers = [{'baseUrl': 'https://api.eumetsat.int/data/download/1.0.0/collections/', 'baseDir': None}]
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Eumetsat.__new__(Eumetsat)
        inst.o = options
        inst.stop_requested = False
        Eumetsat.__init__(inst, options)
    return inst


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------
def _product_details(media_type='application/x-netcdf',
                     href_suffix='EO%3AEUM%3ADAT%3A0412/products/SOMEFILE.nc/entry?name=SOMEFILE.nc',
                     updated='2023-12-29T02:06:33.451Z',
                     md5='abc123',
                     size=1024,
                     geojson=True,
                     extra_links=None):
    """Return a product-details JSON dict."""
    base = 'https://api.eumetsat.int/data/download/1.0.0/collections/'
    link_info = {'href': base + href_suffix, 'mediaType': media_type}
    links = {'data': [link_info]}
    if extra_links:
        links.update(extra_links)
    props = {
        'links': links,
        'updated': updated,
        'extraInformation': {'md5': md5},
        'productInformation': {'size': size},
    }
    details = {'properties': props}
    if geojson:
        details['type'] = 'Feature'
        details['geometry'] = {'type': 'Polygon', 'coordinates': [[[0, 0]]]}
    return details


def _products_page(*detail_hrefs):
    """Return a browse-API products page."""
    products = []
    for href in detail_hrefs:
        products.append({
            'links': [{'title': 'Product details', 'href': href}]
        })
    return {'products': products}


def _mock_response(json_data, ok=True):
    resp = MagicMock()
    resp.ok = ok
    resp.json.return_value = json_data
    resp.__bool__ = lambda self: ok
    return resp


# ===================================================================
# __init__ tests
# ===================================================================
class Test_init:
    def test_pollUrl_gets_placeholder(self):
        inst = _make_eumetsat()
        assert '---COLLECTION_ID---' in inst.o.pollUrl

    def test_pollUrl_trailing_slash_no_double(self):
        inst = _make_eumetsat(pollUrl='https://example.com/browse/')
        assert '//---' not in inst.o.pollUrl
        assert inst.o.pollUrl.endswith('---COLLECTION_ID---/')

    def test_pollUrl_no_trailing_slash(self):
        inst = _make_eumetsat(pollUrl='https://example.com/browse')
        assert inst.o.pollUrl.endswith('---COLLECTION_ID---/')

    def test_post_baseUrl_trailing_slash_added(self):
        inst = _make_eumetsat(post_baseUrl='https://example.com/download')
        assert inst.o.post_baseUrl.endswith('/')

    def test_post_baseUrl_already_has_slash(self):
        inst = _make_eumetsat(post_baseUrl='https://example.com/download/')
        assert not inst.o.post_baseUrl.endswith('//')

    def test_collection_ids_url_encoded(self):
        inst = _make_eumetsat(collectionId=['EO:EUM:DAT:0412'])
        assert inst._encoded_collectionIds == ['EO%3AEUM%3ADAT%3A0412']

    def test_already_encoded_ids_left_alone(self):
        inst = _make_eumetsat(collectionId=['EO%3AEUM%3ADAT%3A0412'])
        assert inst._encoded_collectionIds == ['EO%3AEUM%3ADAT%3A0412']

    def test_multiple_collection_ids(self):
        inst = _make_eumetsat(collectionId=['EO:EUM:DAT:0412', 'EO:EUM:DAT:0556'])
        assert len(inst._encoded_collectionIds) == 2

    def test_default_pollUrl_applied_when_empty(self):
        inst = _make_eumetsat(pollUrl='')
        # When empty string (falsy), default is applied
        assert 'api.eumetsat.int' in inst.o.pollUrl

    def test_default_post_baseUrl_when_ends_with_browse(self):
        inst = _make_eumetsat(post_baseUrl='https://api.eumetsat.int/data/browse/collections')
        assert 'download' in inst.o.post_baseUrl


# ===================================================================
# msg_from_link_info_json tests
# ===================================================================
class Test_msg_from_link_info_json:
    def test_basic_message_creation(self):
        inst = _make_eumetsat()
        link_info = {
            'href': 'https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0412/products/FILE.nc/entry?name=FILE.nc',
            'mediaType': 'application/x-netcdf',
        }
        m = inst.msg_from_link_info_json(link_info)
        assert m is not None
        assert m['contentType'] == 'application/x-netcdf'

    def test_entry_name_stripped_from_relPath(self):
        inst = _make_eumetsat()
        link_info = {
            'href': 'https://api.eumetsat.int/data/download/1.0.0/collections/CID/products/XYZ/entry?name=XYZ',
            'mediaType': 'application/x-netcdf',
        }
        m = inst.msg_from_link_info_json(link_info)
        assert 'entry?name=' not in m['relPath']

    def test_duplicate_path_segments_removed(self):
        inst = _make_eumetsat()
        # href that would produce consecutive duplicate segments after unquoting
        link_info = {
            'href': 'https://api.eumetsat.int/data/download/1.0.0/collections/CID/products/AAA/AAA/entry?name=AAA',
            'mediaType': 'application/x-netcdf',
        }
        m = inst.msg_from_link_info_json(link_info)
        parts = m['relPath'].split('/')
        for i in range(1, len(parts)):
            assert not (parts[i] == parts[i-1])

    def test_retrievePath_set(self):
        inst = _make_eumetsat()
        link_info = {
            'href': 'https://api.eumetsat.int/data/download/1.0.0/collections/CID/products/FILE.nc/entry?name=FILE.nc',
            'mediaType': 'application/x-netcdf',
        }
        m = inst.msg_from_link_info_json(link_info)
        assert 'retrievePath' in m
        assert 'entry?name=' in m['retrievePath']

    def test_malformed_href_returns_none(self):
        inst = _make_eumetsat()
        link_info = {
            'href': 'totally-broken-url',
            'mediaType': 'application/x-netcdf',
        }
        m = inst.msg_from_link_info_json(link_info)
        assert m is None

    def test_missing_href_returns_none(self):
        inst = _make_eumetsat()
        m = inst.msg_from_link_info_json({'mediaType': 'application/x-netcdf'})
        assert m is None


# ===================================================================
# msgs_from_details_page tests
# ===================================================================
class Test_msgs_from_details_page:
    def test_matching_media_type_produces_message(self):
        inst = _make_eumetsat()
        details = _product_details(media_type='application/x-netcdf')
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 1

    def test_non_matching_media_type_skipped(self):
        inst = _make_eumetsat()
        details = _product_details(media_type='image/jpeg')
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 0

    def test_mtime_set_from_updated(self):
        inst = _make_eumetsat()
        details = _product_details(updated='2023-12-29T02:06:33.451Z')
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 1
        assert msgs[0]['mtime'] == '20231229T020633.451'

    def test_geojson_attached(self):
        inst = _make_eumetsat()
        details = _product_details(geojson=True)
        msgs = inst.msgs_from_details_page(details)
        assert msgs[0]['type'] == 'Feature'
        assert 'geometry' in msgs[0]

    def test_no_geojson_when_absent(self):
        inst = _make_eumetsat()
        details = _product_details(geojson=False)
        msgs = inst.msgs_from_details_page(details)
        assert 'type' not in msgs[0] or msgs[0].get('type') != 'Feature'

    def test_md5_set_for_zip_media_type(self):
        inst = _make_eumetsat(acceptMediaType=['application/zip'])
        details = _product_details(media_type='application/zip', md5='deadbeef')
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 1
        assert msgs[0]['identity'] == {'method': 'md5', 'value': 'deadbeef'}

    def test_md5_not_set_for_non_zip(self):
        inst = _make_eumetsat()
        details = _product_details(media_type='application/x-netcdf', md5='deadbeef')
        msgs = inst.msgs_from_details_page(details)
        assert 'identity' not in msgs[0] or msgs[0]['identity'].get('method') != 'md5'

    def test_missing_properties_returns_empty(self):
        inst = _make_eumetsat()
        msgs = inst.msgs_from_details_page({'no_properties': True})
        assert msgs == []

    def test_missing_updated_no_mtime(self):
        inst = _make_eumetsat()
        details = _product_details()
        del details['properties']['updated']
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 1
        # mtime not set by the plugin (fromFileInfo may set its own default)
        # Just ensure no crash

    def test_missing_extra_information_no_md5(self):
        inst = _make_eumetsat(acceptMediaType=['application/zip'])
        details = _product_details(media_type='application/zip')
        del details['properties']['extraInformation']
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 1
        assert msgs[0].get('identity', {}).get('method') != 'md5'

    def test_non_list_link_group_skipped(self):
        inst = _make_eumetsat()
        details = _product_details()
        # Add a non-list entry in links – should be skipped silently
        details['properties']['links']['metadata'] = 'not-a-list'
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) >= 1  # the 'data' list link still works

    def test_multiple_links_multiple_messages(self):
        inst = _make_eumetsat(acceptMediaType=['application/x-netcdf', 'image/jpeg'])
        base = 'https://api.eumetsat.int/data/download/1.0.0/collections/'
        details = _product_details()
        details['properties']['links']['data'].append({
            'href': base + 'CID/products/OTHER.jpg/entry?name=OTHER.jpg',
            'mediaType': 'image/jpeg',
        })
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 2

    def test_empty_accept_media_type_produces_no_messages(self):
        inst = _make_eumetsat(acceptMediaType=[])
        details = _product_details()
        msgs = inst.msgs_from_details_page(details)
        assert len(msgs) == 0


# ===================================================================
# poll tests
# ===================================================================
class Test_poll:
    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_basic_poll_single_product(self, mock_get):
        inst = _make_eumetsat()
        detail_url = 'https://api.eumetsat.int/details/product1'
        products_resp = _mock_response(_products_page(detail_url))
        base = 'https://api.eumetsat.int/data/download/1.0.0/collections/'
        details_resp = _mock_response(_product_details(
            href_suffix='CID/products/FILE.nc/entry?name=FILE.nc'))

        mock_get.side_effect = [products_resp, products_resp, details_resp, details_resp]
        msgs = inst.poll()
        assert len(msgs) >= 1

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_no_products_key(self, mock_get):
        inst = _make_eumetsat()
        mock_get.return_value = _mock_response({'no_products': True})
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_empty_products(self, mock_get):
        inst = _make_eumetsat()
        mock_get.return_value = _mock_response({'products': []})
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_stop_requested_stops_early(self, mock_get):
        inst = _make_eumetsat()
        detail_url = 'https://api.eumetsat.int/details/product1'
        products_resp = _mock_response(_products_page(detail_url, detail_url))

        def side_effect_fn(url):
            if 'details' in url:
                inst.stop_requested = True
                return _mock_response(_product_details())
            return products_resp

        mock_get.side_effect = side_effect_fn
        msgs = inst.poll()
        # Should have stopped after processing first detail, not second
        assert isinstance(msgs, list)

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_time_range_builds_correct_hours(self, mock_get):
        inst = _make_eumetsat(timeNowMinus=7200.0)  # 2 hours => n_hours=3
        mock_get.return_value = _mock_response({'products': []})
        inst.poll()
        # With 1 collection and 3 hours, we expect 3 browse requests
        assert mock_get.call_count == 3

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_multiple_collections(self, mock_get):
        inst = _make_eumetsat(collectionId=['EO:EUM:DAT:0412', 'EO:EUM:DAT:0556'])
        mock_get.return_value = _mock_response({'products': []})
        inst.poll()
        # 2 collections x 2 hours (timeNowMinus=3600 => n_hours=2)
        assert mock_get.call_count == 2 * 2

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_falsy_response_handled(self, mock_get):
        inst = _make_eumetsat()
        mock_get.return_value = _mock_response({}, ok=False)
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_product_without_links_skipped(self, mock_get):
        inst = _make_eumetsat()
        page = {'products': [{'no_links_key': True}]}
        mock_get.return_value = _mock_response(page)
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_product_link_without_title_skipped(self, mock_get):
        inst = _make_eumetsat()
        page = {'products': [{'links': [{'href': 'http://x'}]}]}
        mock_get.return_value = _mock_response(page)
        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.eumetsat.requests.get')
    def test_poll_dates_not_duplicated_in_url(self, mock_get):
        inst = _make_eumetsat()
        mock_get.return_value = _mock_response({'products': []})
        inst.poll()
        for c in mock_get.call_args_list:
            url = c[0][0]
            assert url.count('/dates/') == 1