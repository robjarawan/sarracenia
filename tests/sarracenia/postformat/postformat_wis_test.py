import json
import pytest

import sarracenia
import sarracenia.config
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

    def test_mine_non_matching(self):
        assert Wis.mine('{}', {}, 'application/json', {}) is False
        assert Wis.mine('{}', {}, 'text/plain', {}) is False
        assert Wis.mine('{}', {}, '', {}) is False


# ── importMine ───────────────────────────────────────────────────────────

class Test_importMine:
    def test_importMine_basic_geojson(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2/data'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg is not None
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['retrievePath'] == '/data/file.grib2'
        assert msg['size'] == 4096

    def test_importMine_format_set(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2/data'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['_format'] == 'wis'

    def test_importMine_pubtime_parsed(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        # '2023-12-14T15:10:49Z' -> '20231214T151049'
        assert msg['pubTime'] == '20231214T151049'

    def test_importMine_invalid_json_returns_none(self):
        msg = Wis.importMine('not valid json!!!', {}, {})
        assert msg is None

    def test_importMine_geometry_preserved(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['geometry']['type'] == 'Point'
        assert msg['geometry']['coordinates'] == [-75.0, 45.0]

    def test_importMine_content_type_from_link(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['contentType'] == 'application/x-grib2'

    def test_importMine_relPath_from_topic_and_data_id(self):
        geojson = _geojson_body()
        headers = {'topic': 'origin/a/wis2/data'}
        msg = Wis.importMine(json.dumps(geojson), headers, {})
        assert msg['relPath'] == 'origin/a/wis2/data/some-data-id'


# ── exportMine ───────────────────────────────────────────────────────────

class Test_exportMine:
    def _make_body(self, **overrides):
        """Create a plain dict body suitable for JSON serialization in exportMine."""
        body = {
            'pubTime': '20231214T151049',
            'baseUrl': 'https://example.com',
            'relPath': '/data/file.grib2',
            'size': 4096,
        }
        body.update(overrides)
        return body

    def test_exportMine_creates_geojson(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['type'] == 'Feature'
        assert parsed['version'] == 'v04'
        assert ct == 'application/geo+json'

    def test_exportMine_includes_links(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert 'links' in parsed
        link = parsed['links'][0]
        assert link['href'] == 'https://example.com//data/file.grib2'
        assert link['length'] == 4096
        assert link['rel'] == 'canonical'

    def test_exportMine_geometry(self):
        geom = {'type': 'Point', 'coordinates': [-75.0, 45.0]}
        body = self._make_body(geometry=geom)
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['geometry'] == geom

    def test_exportMine_pubtime_formatted(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['properties']['pubtime'] == '2023-12-14T15:10:49Z'

    def test_exportMine_content_type_in_link(self):
        body = self._make_body(contentType='application/x-grib2')
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['links'][0]['type'] == 'application/x-grib2'

    def test_exportMine_geometry_default_none(self):
        body = self._make_body()
        raw, headers, ct = Wis.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['geometry'] is None
