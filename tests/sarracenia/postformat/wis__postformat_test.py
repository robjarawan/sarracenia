import json
import pytest
from tests.conftest import *

import sarracenia
from sarracenia.postformat.wis import Wis


def _geojson_body(**overrides):
    """Build a minimal valid WIS GeoJSON body."""
    base = {
        'type': 'Feature',
        'version': 'v04',
        'geometry': {
            'type': 'Point',
            'coordinates': [-75.0, 45.0],
        },
        'properties': {
            'pubtime': '2023-12-14T15:10:49Z',
            'data_id': 'some-data-id',
        },
        'links': [{
            'href': 'https://example.com/data/file.grib2',
            'length': 4096,
            'rel': 'canonical',
            'type': 'application/x-grib2',
        }],
    }
    base.update(overrides)
    return base


# ── content_type ─────────────────────────────────────────────────────────

class Test_content_type:
    def test_content_type(self):
        assert Wis.content_type() == 'application/geo+json'


# ── mine ─────────────────────────────────────────────────────────────────

class Test_mine:
    def test_mine_matching(self):
        assert Wis.mine('{}', {}, 'application/geo+json', {}) is True

    def test_mine_not_matching(self):
        assert Wis.mine('{}', {}, 'application/json', {}) is False
        assert Wis.mine('{}', {}, 'text/plain', {}) is False
        assert Wis.mine('{}', {}, '', {}) is False


# ── importMine ───────────────────────────────────────────────────────────

class Test_importMine:
    def test_import_valid_geojson(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2/data'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg is not None
        assert msg['_format'] == 'wis'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['retrievePath'] == '/data/file.grib2'
        assert msg['size'] == 4096

    def test_import_invalid_json(self):
        msg = Wis.importMine('not valid json!!!', {}, {})
        assert msg is None

    def test_import_pubtime_conversion(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        # '2023-12-14T15:10:49Z' -> '20231214T151049'
        assert msg['pubTime'] == '20231214T151049'

    def test_import_geometry(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['geometry']['type'] == 'Point'
        assert msg['geometry']['coordinates'] == [-75.0, 45.0]

    def test_import_links_to_baseurl(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['retrievePath'] == '/data/file.grib2'
        assert msg['contentType'] == 'application/x-grib2'

    def test_import_missing_pubtime(self):
        """When pubtime is absent, the message is still returned but without pubTime."""
        geojson = _geojson_body()
        del geojson['properties']['pubtime']
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg is not None
        assert 'pubTime' not in msg

    def test_import_data_id_with_topic(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2/data'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['relPath'] == 'origin/a/wis2/data/some-data-id'

    def test_import_geometry_none(self):
        """When geometry is explicitly null, it should not be set on the message."""
        geojson = _geojson_body(geometry=None)
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert 'geometry' not in msg


# ── exportMine ───────────────────────────────────────────────────────────

class Test_exportMine:
    def _make_body(self, **overrides):
        """Create a plain dict body suitable for exportMine."""
        body = {
            'pubTime': '20231214T151049',
            'baseUrl': 'https://example.com',
            'relPath': '/data/file.grib2',
            'size': 4096,
        }
        body.update(overrides)
        return body

    def test_export_creates_geojson(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['type'] == 'Feature'
        assert parsed['version'] == 'v04'
        assert ct == 'application/geo+json'

    def test_export_pubtime_format(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        # '20231214T151049' -> '2023-12-14T15:10:49Z'
        assert parsed['properties']['pubtime'] == '2023-12-14T15:10:49Z'

    def test_export_links_from_baseurl(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert 'links' in parsed
        link = parsed['links'][0]
        assert link['href'] == 'https://example.com//data/file.grib2'
        assert link['length'] == 4096
        assert link['rel'] == 'canonical'

    def test_export_geometry_preserved(self):
        geom = {'type': 'Point', 'coordinates': [-75.0, 45.0]}
        body = self._make_body(geometry=geom)
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['geometry'] == geom

    def test_export_retrievepath_used(self):
        body = self._make_body(retrievePath='special/path.bin')
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert 'special/path.bin' in parsed['links'][0]['href']

    def test_export_geometry_default_none(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['geometry'] is None

    def test_export_content_type_in_link(self):
        body = self._make_body(contentType='application/x-grib2')
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['links'][0]['type'] == 'application/x-grib2'
