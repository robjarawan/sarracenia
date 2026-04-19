import json
import pytest
from unittest.mock import patch

import sarracenia
from sarracenia.postformat.v03 import V03


# ── content_type ─────────────────────────────────────────────────────────

class Test_content_type:
    def test_returns_application_json(self):
        assert V03.content_type() == 'application/json'


# ── mine ─────────────────────────────────────────────────────────────────

class Test_mine:
    def test_true_for_matching_content_type(self):
        assert V03.mine(b'{}', {}, 'application/json', {}) is True

    def test_false_for_text_plain(self):
        assert V03.mine(b'{}', {}, 'text/plain', {}) is False

    def test_false_for_geo_json(self):
        assert V03.mine(b'{}', {}, 'application/geo+json', {}) is False

    def test_false_for_empty_string(self):
        assert V03.mine(b'', {}, '', {}) is False


# ── importMine ───────────────────────────────────────────────────────────

class Test_importMine:
    def test_valid_json_body(self):
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

    def test_invalid_json_returns_none(self):
        msg = V03.importMine('not-valid-json{{{', {}, {})
        assert msg is None

    def test_format_field_set_to_v03(self):
        body = json.dumps({'pubTime': '20240101T000000'})
        msg = V03.importMine(body, {}, {})
        assert msg['_format'] == 'v03'

    def test_retPath_renamed_to_retrievePath(self):
        body = json.dumps({'retPath': '/legacy/path'})
        msg = V03.importMine(body, {}, {})
        assert msg['retrievePath'] == '/legacy/path'
        assert 'retPath' not in msg

    def test_integrity_renamed_to_identity(self):
        body = json.dumps({'integrity': {'method': 'sha512', 'value': 'deadbeef'}})
        msg = V03.importMine(body, {}, {})
        assert msg['identity'] == {'method': 'sha512', 'value': 'deadbeef'}
        assert 'integrity' not in msg

    def test_parts_mode_1_sets_size(self):
        body = json.dumps({'parts': '1,2048,1,0,0'})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 2048
        assert 'parts' not in msg
        assert 'blocks' not in msg

    def test_parts_mode_i_creates_inplace_blocks(self):
        body = json.dumps({'parts': 'i,1024,10,512,3'})
        msg = V03.importMine(body, {}, {})
        assert msg['blocks'] == {
            'method': 'inplace',
            'size': 1024,
            'count': 10,
            'remainder': 512,
            'number': 3,
        }
        assert 'parts' not in msg

    def test_parts_mode_p_creates_partitioned_blocks(self):
        body = json.dumps({'parts': 'p,4096,8,256,5'})
        msg = V03.importMine(body, {}, {})
        assert msg['blocks']['method'] == 'partitioned'
        assert msg['blocks']['size'] == 4096
        assert msg['blocks']['count'] == 8
        assert msg['blocks']['remainder'] == 256
        assert msg['blocks']['number'] == 5
        assert 'parts' not in msg

    def test_string_size_converted_to_int(self):
        body = json.dumps({'size': '9999'})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 9999
        assert type(msg['size']) is int

    def test_int_size_unchanged(self):
        body = json.dumps({'size': 512})
        msg = V03.importMine(body, {}, {})
        assert msg['size'] == 512
        assert type(msg['size']) is int

    def test_blocks_manifest_string_keys_converted_to_int(self):
        body = json.dumps({
            'blocks': {
                'method': 'inplace',
                'size': 1024,
                'manifest': {'0': 'hash_a', '1': 'hash_b', '2': 'hash_c'},
            }
        })
        msg = V03.importMine(body, {}, {})
        manifest = msg['blocks']['manifest']
        assert 0 in manifest and manifest[0] == 'hash_a'
        assert 1 in manifest and manifest[1] == 'hash_b'
        assert 2 in manifest and manifest[2] == 'hash_c'
        # string keys should no longer exist
        assert '0' not in manifest

    def test_blocks_manifest_string_keys_all_converted(self):
        """JSON serializes int keys as strings; verify importMine converts
        all string keys back to int.
        """
        body = json.dumps({
            'blocks': {
                'method': 'inplace',
                'size': 512,
                'manifest': {'0': 'hash_x', '1': 'hash_y'},
            }
        })
        result = V03.importMine(body, {}, {})
        manifest = result['blocks']['manifest']
        assert manifest[0] == 'hash_x'
        assert manifest[1] == 'hash_y'


# ── exportMine ───────────────────────────────────────────────────────────

class Test_exportMine:
    @patch('sarracenia.postformat.PostFormat.topicDerive')
    def test_returns_json_body(self, mock_topic):
        mock_topic.return_value = ['v03', 'data']
        body = {'pubTime': '20240101T000000', 'baseUrl': 'https://example.com', 'relPath': '/a/b.txt'}
        raw, headers, ct = V03.exportMine(body, {})
        parsed = json.loads(raw)
        assert parsed['pubTime'] == '20240101T000000'
        assert parsed['baseUrl'] == 'https://example.com'

    @patch('sarracenia.postformat.PostFormat.topicDerive')
    def test_returns_application_json_content_type(self, mock_topic):
        mock_topic.return_value = ['v03']
        raw, headers, ct = V03.exportMine({'key': 'value'}, {})
        assert ct == 'application/json'

    @patch('sarracenia.postformat.PostFormat.topicDerive')
    def test_headers_contain_topic(self, mock_topic):
        mock_topic.return_value = ['v03', 'alerts']
        raw, headers, ct = V03.exportMine({'relPath': 'alerts/cap.xml'}, {})
        assert headers['topic'] == ['v03', 'alerts']

    @patch('sarracenia.postformat.PostFormat.topicDerive')
    def test_body_is_valid_json(self, mock_topic):
        mock_topic.return_value = []
        body = {'size': 1024, 'nested': {'a': 1}}
        raw, headers, ct = V03.exportMine(body, {})
        assert json.loads(raw) == body
