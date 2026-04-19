import json
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import *

import logging
import sarracenia
from sarracenia.postformat.wis import Wis

logger = logging.getLogger(__name__)


def make_geojson_body(**overrides):
    """Helper to build a valid WIS GeoJSON body."""
    body = {
        'type': 'Feature',
        'version': 'v04',
        'geometry': {'type': 'Point', 'coordinates': [0.0, 0.0]},
        'properties': {
            'pubtime': '2023-01-18T15:10:49Z',
            'data_id': 'test-data-id',
        },
        'links': [{
            'href': 'https://example.com/data/file.txt',
            'length': 1024,
            'rel': 'canonical',
            'type': 'application/octet-stream',
        }],
    }
    body.update(overrides)
    return body


class Test_Wis_content_type:
    def test_content_type(self):
        assert Wis.content_type() == 'application/geo+json'


class Test_Wis_mine:
    def test_mine_matching_content_type(self):
        assert Wis.mine('{}', {}, 'application/geo+json', {}) is True

    def test_mine_non_matching_content_type(self):
        assert Wis.mine('{}', {}, 'application/json', {}) is False

    def test_mine_text_plain(self):
        assert Wis.mine('text', {}, 'text/plain', {}) is False

    def test_mine_empty_content_type(self):
        assert Wis.mine('{}', {}, '', {}) is False


class Test_Wis_importMine:
    def test_basic_import(self):
        geojson = make_geojson_body()
        body = json.dumps(geojson)
        headers = {'topic': 'origin/a/b'}
        msg = Wis.importMine(body, headers, {})
        assert msg is not None
        assert msg['_format'] == 'wis'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['retrievePath'] == '/data/file.txt'
        assert msg['size'] == 1024

    def test_import_invalid_json(self):
        msg = Wis.importMine('not valid json', {}, {})
        assert msg is None

    def test_import_empty_body(self):
        msg = Wis.importMine('', {}, {})
        assert msg is None

    def test_import_pubtime_conversion(self):
        geojson = make_geojson_body()
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        pubTime = msg['pubTime']
        assert pubTime.startswith('2023')
        assert '-' not in pubTime

    def test_import_missing_pubtime(self):
        geojson = make_geojson_body()
        del geojson['properties']['pubtime']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'pubTime' not in msg

    def test_import_missing_geometry(self):
        geojson = make_geojson_body()
        del geojson['geometry']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'geometry' not in msg

    def test_import_null_geometry(self):
        geojson = make_geojson_body(geometry=None)
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'geometry' not in msg

    def test_import_missing_version(self):
        geojson = make_geojson_body()
        del geojson['version']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None

    def test_import_wrong_version(self):
        geojson = make_geojson_body(version='v03')
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None

    def test_import_missing_type(self):
        geojson = make_geojson_body()
        del geojson['type']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None

    def test_import_missing_links(self):
        geojson = make_geojson_body()
        del geojson['links']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'baseUrl' not in msg
        assert 'retrievePath' not in msg

    def test_import_missing_data_id(self):
        geojson = make_geojson_body()
        del geojson['properties']['data_id']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None

    def test_import_data_id_with_topic_builds_relpath(self):
        geojson = make_geojson_body()
        body = json.dumps(geojson)
        headers = {'topic': 'origin/a/b'}
        msg = Wis.importMine(body, headers, {})
        assert msg is not None
        assert msg['relPath'] == 'origin/a/b/test-data-id'

    def test_import_links_with_type(self):
        geojson = make_geojson_body()
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert msg['contentType'] == 'application/octet-stream'

    def test_import_links_without_type(self):
        geojson = make_geojson_body()
        del geojson['links'][0]['type']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'contentType' not in msg

    def test_import_extra_properties(self):
        geojson = make_geojson_body()
        geojson['properties']['wigos_station_identifier'] = 'STATION_123'
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert msg['wigos_station_identifier'] == 'STATION_123'

    def test_import_preserves_links(self):
        geojson = make_geojson_body()
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert 'links' in msg
        assert len(msg['links']) == 1

    def test_import_complex_url(self):
        geojson = make_geojson_body()
        geojson['links'][0]['href'] = 'https://data.wmo.int:8443/cache/a/wis2/some/path/file.bufr'
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None
        assert msg['baseUrl'] == 'https://data.wmo.int:8443'
        assert msg['retrievePath'] == '/cache/a/wis2/some/path/file.bufr'

    def test_import_missing_properties(self):
        geojson = make_geojson_body()
        del geojson['properties']
        body = json.dumps(geojson)
        msg = Wis.importMine(body, {}, {})
        assert msg is not None


class Test_Wis_exportMine:
    def test_basic_export(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 1024,
        }
        options = {'topic': 'origin/a/b'}
        raw_body, headers, content_type = Wis.exportMine(body, options)
        assert content_type == 'application/geo+json'
        parsed = json.loads(raw_body)
        assert parsed['type'] == 'Feature'
        assert parsed['version'] == 'v04'
        assert 'topic' in headers

    def test_export_pubtime_format(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        pubtime = parsed['properties']['pubtime']
        assert '-' in pubtime
        assert ':' in pubtime
        assert pubtime.endswith('Z')

    def test_export_with_geometry(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
            'geometry': {'type': 'Point', 'coordinates': [45.0, -75.0]},
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['geometry'] == {'type': 'Point', 'coordinates': [45.0, -75.0]}

    def test_export_without_geometry(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['geometry'] is None

    def test_export_with_retrievepath(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'retrievePath': 'special/path.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['links'][0]['href'] == 'https://example.com/special/path.txt'

    def test_export_with_relpath(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['links'][0]['href'] == 'https://example.com/data/file.txt'

    def test_export_with_content_type(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.bufr',
            'size': 100,
            'contentType': 'application/x-bufr',
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['links'][0]['type'] == 'application/x-bufr'

    def test_export_without_content_type(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert 'type' not in parsed['links'][0]

    def test_export_generates_uuid_if_no_id(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert 'id' in parsed
        import uuid
        uuid.UUID(parsed['id'])  # should not raise

    def test_export_topic_from_body(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
            'topic': 'origin/wis2/data',
        }
        options = {}
        raw_body, headers, _ = Wis.exportMine(body, options)
        assert headers['topic'] == ['origin', 'wis2', 'data']

    def test_export_topic_from_options(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {'topic': 'origin/wis2/fallback'}
        raw_body, headers, _ = Wis.exportMine(body, options)
        assert headers['topic'] == ['origin', 'wis2', 'fallback']

    def test_export_no_topic(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
        }
        options = {}
        raw_body, headers, _ = Wis.exportMine(body, options)
        assert headers['topic'] == []

    def test_export_link_length_matches_size(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 9999,
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['links'][0]['length'] == 9999

    def test_export_extra_properties_passed_through(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'size': 100,
            'wigos_station_identifier': 'STATION_X',
            '_format': 'wis',
        }
        options = {'topic': 'a/b'}
        raw_body, _, _ = Wis.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['properties']['wigos_station_identifier'] == 'STATION_X'
        assert parsed['properties']['_format'] == 'wis'
