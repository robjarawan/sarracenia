import json
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import *

import logging
import sarracenia
from sarracenia.postformat.v03 import V03

logger = logging.getLogger(__name__)


class Test_V03_content_type:
    def test_content_type(self):
        assert V03.content_type() == 'application/json'


class Test_V03_mine:
    def test_mine_matching_content_type(self):
        assert V03.mine('{}', {}, 'application/json', {}) is True

    def test_mine_non_matching_content_type(self):
        assert V03.mine('{}', {}, 'text/plain', {}) is False

    def test_mine_empty_content_type(self):
        assert V03.mine('{}', {}, '', {}) is False

    def test_mine_geojson_content_type(self):
        assert V03.mine('{}', {}, 'application/geo+json', {}) is False


class Test_V03_importMine:
    def test_basic_import(self):
        body = json.dumps({
            'pubTime': '20230118T151049.356378078',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 1024,
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert msg['_format'] == 'v03'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['relPath'] == 'data/file.txt'
        assert msg['size'] == 1024

    def test_import_invalid_json(self):
        msg = V03.importMine('not valid json{{{', {}, {})
        assert msg is None

    def test_import_empty_body(self):
        msg = V03.importMine('', {}, {})
        assert msg is None

    def test_import_retPath_legacy_field(self):
        """Legacy 'retPath' should be converted to 'retrievePath' (issue #628)."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'retPath': '/old/path/to/file.txt',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'retrievePath' in msg
        assert msg['retrievePath'] == '/old/path/to/file.txt'
        assert 'retPath' not in msg

    def test_import_integrity_legacy_field(self):
        """Legacy 'integrity' should be converted to 'identity'."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'integrity': {'method': 'sha512', 'value': 'abc123'},
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'identity' in msg
        assert msg['identity']['method'] == 'sha512'
        assert 'integrity' not in msg

    def test_import_parts_header_simple(self):
        """v2 bug: parts header in v03 messages. Style '1' means simple file."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'parts': '1,2048,1,0,0',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'parts' not in msg
        assert msg['size'] == 2048

    def test_import_parts_header_inplace(self):
        """v2 bug: parts header with style 'i' (inplace)."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'parts': 'i,1024,10,512,3',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'parts' not in msg
        assert 'blocks' in msg
        assert msg['blocks']['method'] == 'inplace'
        assert msg['blocks']['size'] == 1024
        assert msg['blocks']['count'] == 10
        assert msg['blocks']['remainder'] == 512
        assert msg['blocks']['number'] == 3

    def test_import_parts_header_partitioned(self):
        """v2 bug: parts header with style 'p' (partitioned)."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'parts': 'p,512,5,128,2',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'blocks' in msg
        assert msg['blocks']['method'] == 'partitioned'

    def test_import_size_as_string(self):
        """Size as string should be converted to int."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'size': '4096',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert msg['size'] == 4096
        assert type(msg['size']) is int

    def test_import_size_as_int(self):
        """Size as int should remain int."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'size': 4096,
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert msg['size'] == 4096

    def test_import_blocks_manifest_string_keys(self):
        """JSON converts numeric keys to strings; importMine should fix them back to int."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'blocks': {
                'method': 'inplace',
                'size': 1024,
                'count': 3,
                'remainder': 512,
                'number': 0,
                'manifest': {
                    '0': 'hash_block_0',
                    '1': 'hash_block_1',
                    '2': 'hash_block_2',
                }
            }
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        manifest = msg['blocks']['manifest']
        for key in manifest:
            assert type(key) is int
        assert manifest[0] == 'hash_block_0'
        assert manifest[1] == 'hash_block_1'
        assert manifest[2] == 'hash_block_2'

    def test_import_blocks_manifest_int_keys(self):
        """If keys are already int, they should remain as is."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'blocks': {
                'method': 'inplace',
                'size': 1024,
                'count': 1,
                'remainder': 0,
                'number': 0,
                'manifest': {'0': 'hash_value'}  # JSON always serializes as string
            }
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 0 in msg['blocks']['manifest']

    def test_import_blocks_without_manifest(self):
        """Blocks without manifest should work fine."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'blocks': {
                'method': 'inplace',
                'size': 1024,
                'count': 1,
                'remainder': 0,
                'number': 0,
            }
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert 'manifest' not in msg['blocks']

    def test_import_preserves_all_fields(self):
        """Extra fields in the message should be preserved."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'customField': 'customValue',
            'anotherField': 42,
        })
        msg = V03.importMine(body, {}, {})
        assert msg['customField'] == 'customValue'
        assert msg['anotherField'] == 42

    def test_import_both_retpath_and_integrity(self):
        """Both legacy fields should be converted simultaneously."""
        body = json.dumps({
            'pubTime': '20230118T151049',
            'retPath': '/old/path',
            'integrity': {'method': 'md5', 'value': 'abc'},
        })
        msg = V03.importMine(body, {}, {})
        assert 'retrievePath' in msg
        assert 'identity' in msg
        assert 'retPath' not in msg
        assert 'integrity' not in msg

    def test_import_empty_json_object(self):
        """An empty JSON object should still produce a message with _format."""
        body = json.dumps({})
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert msg['_format'] == 'v03'


class Test_V03_exportMine:
    def test_basic_export(self):
        body = {
            'pubTime': '20230118T151049.356378078',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
        }
        options = {
            'publishers': [{'broker': MagicMock(url=MagicMock(scheme='amqp')), 'topicPrefix': ['v03'], 'exchange': None}],
            'publisher_index': 0,
        }
        raw_body, headers, content_type = V03.exportMine(body, options)
        assert content_type == 'application/json'
        assert 'topic' in headers
        parsed = json.loads(raw_body)
        assert parsed['baseUrl'] == 'https://example.com'
        assert parsed['pubTime'] == '20230118T151049.356378078'

    def test_export_preserves_all_fields(self):
        body = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'file.txt',
            'identity': {'method': 'sha512', 'value': 'abc'},
            'size': 1024,
        }
        options = {
            'publishers': [{'broker': MagicMock(url=MagicMock(scheme='amqp')), 'topicPrefix': ['v03'], 'exchange': None}],
            'publisher_index': 0,
        }
        raw_body, headers, content_type = V03.exportMine(body, options)
        parsed = json.loads(raw_body)
        assert parsed['identity'] == {'method': 'sha512', 'value': 'abc'}
        assert parsed['size'] == 1024

    def test_export_roundtrip(self):
        """Export then import should preserve the message content."""
        original = {
            'pubTime': '20230118T151049',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 2048,
            'identity': {'method': 'md5', 'value': 'xyz'},
        }
        options = {
            'publishers': [{'broker': MagicMock(url=MagicMock(scheme='amqp')), 'topicPrefix': ['v03'], 'exchange': None}],
            'publisher_index': 0,
        }
        raw_body, headers, content_type = V03.exportMine(original, options)
        restored = V03.importMine(raw_body, headers, {})
        assert restored['pubTime'] == original['pubTime']
        assert restored['baseUrl'] == original['baseUrl']
        assert restored['relPath'] == original['relPath']
        assert restored['size'] == original['size']
        assert restored['identity'] == original['identity']
