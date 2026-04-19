import json
import pytest

import sarracenia
import sarracenia.config
from sarracenia.postformat.v03 import V03


# ── content_type ─────────────────────────────────────────────────────────

class Test_content_type:
    def test_content_type(self):
        assert V03.content_type() == 'application/json'


# ── mine ─────────────────────────────────────────────────────────────────

class Test_mine:
    def test_mine_matching_content_type(self):
        assert V03.mine('{}', {}, 'application/json', {}) is True

    def test_mine_non_matching(self):
        assert V03.mine('{}', {}, 'text/plain', {}) is False
        assert V03.mine('{}', {}, 'application/geo+json', {}) is False
        assert V03.mine('{}', {}, '', {}) is False


# ── importMine ───────────────────────────────────────────────────────────

class Test_importMine:
    def test_importMine_basic_json(self):
        body = json.dumps({
            'pubTime': '20231214T151049.356',
            'baseUrl': 'https://example.com',
            'relPath': '/data/file.txt',
        })
        msg = V03.importMine(body, {}, {})
        assert msg is not None
        assert msg['pubTime'] == '20231214T151049.356'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['relPath'] == '/data/file.txt'

    def test_importMine_format_set(self):
        body = json.dumps({'pubTime': '20231214T120000'})
        msg = V03.importMine(body, {}, {})
        assert msg['_format'] == 'v03'

    def test_importMine_retPath_renamed(self):
        body = json.dumps({'retPath': '/old/path'})
        msg = V03.importMine(body, {}, {})
        assert 'retrievePath' in msg
        assert msg['retrievePath'] == '/old/path'
        assert 'retPath' not in msg

    def test_importMine_integrity_renamed(self):
        body = json.dumps({'integrity': {'method': 'sha512', 'value': 'abc123'}})
        msg = V03.importMine(body, {}, {})
        assert 'identity' in msg
        assert msg['identity']['method'] == 'sha512'
        assert msg['identity']['value'] == 'abc123'
        assert 'integrity' not in msg

    def test_importMine_parts_simple(self):
        body = json.dumps({'parts': '1,1024,1,0,0'})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 1024
        assert 'parts' not in msg
        assert 'blocks' not in msg

    def test_importMine_parts_inplace(self):
        body = json.dumps({'parts': 'i,1024,10,512,3'})
        msg = V03.importMine(body, {}, {})
        assert 'blocks' in msg
        assert msg['blocks']['method'] == 'inplace'
        assert msg['blocks']['size'] == 1024
        assert msg['blocks']['count'] == 10
        assert msg['blocks']['remainder'] == 512
        assert msg['blocks']['number'] == 3
        assert 'parts' not in msg

    def test_importMine_parts_partitioned(self):
        body = json.dumps({'parts': 'p,2048,5,100,2'})
        msg = V03.importMine(body, {}, {})
        assert 'blocks' in msg
        assert msg['blocks']['method'] == 'partitioned'
        assert msg['blocks']['size'] == 2048
        assert msg['blocks']['count'] == 5
        assert msg['blocks']['remainder'] == 100
        assert msg['blocks']['number'] == 2

    def test_importMine_size_string_to_int(self):
        body = json.dumps({'size': '1024'})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 1024
        assert type(msg['size']) is int

    def test_importMine_blocks_manifest_string_keys(self):
        body = json.dumps({
            'blocks': {
                'method': 'inplace',
                'size': 1024,
                'manifest': {'0': 'hash_a', '1': 'hash_b'},
            }
        })
        msg = V03.importMine(body, {}, {})
        manifest = msg['blocks']['manifest']
        assert 0 in manifest
        assert 1 in manifest
        assert manifest[0] == 'hash_a'
        assert manifest[1] == 'hash_b'

    def test_importMine_invalid_json_returns_none(self):
        msg = V03.importMine('not-json{{{', {}, {})
        assert msg is None

    def test_importMine_size_already_int(self):
        body = json.dumps({'size': 512})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 512
        assert type(msg['size']) is int
