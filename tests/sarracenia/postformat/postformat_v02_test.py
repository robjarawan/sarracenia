import json
import pytest
from codecs import decode, encode

import sarracenia
import sarracenia.config
from sarracenia.postformat.v02 import V02


def _make_options():
    return sarracenia.config.default_config()


# ── content_type ─────────────────────────────────────────────────────────

class Test_content_type:
    def test_content_type(self):
        assert V02.content_type() == 'text/plain'


# ── mine ─────────────────────────────────────────────────────────────────

class Test_mine:
    def test_mine_text_plain(self):
        assert V02.mine('anything', {}, 'text/plain', {}) is True

    def test_mine_non_json_string(self):
        assert V02.mine('20231214 http://host /path', {}, '', {}) is True

    def test_mine_topic_v02(self):
        headers = {'topic': 'v02.post.data'}
        assert V02.mine('{}', headers, '', {}) is True

    def test_mine_json_content_type(self):
        assert V02.mine('{}', {}, 'application/json', {}) is False


# ── importMine ───────────────────────────────────────────────────────────

class Test_importMine:
    def test_importMine_basic(self):
        body = '20231214T151049.356 https://example.com /data/file.txt'
        msg = V02.importMine(body, {}, _make_options())
        assert msg is not None
        assert msg['pubTime'] == '20231214T151049.356'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['relPath'] == '/data/file.txt'
        assert msg['to_clusters'] == 'ALL'

    def test_importMine_format_set(self):
        body = '20231214T120000 https://host /path'
        msg = V02.importMine(body, {}, _make_options())
        assert msg['_format'] == 'v02'

    def test_importMine_converts_pubtime(self):
        body = '20231214151049.356 https://host /path'
        msg = V02.importMine(body, {}, _make_options())
        assert msg['pubTime'] == '20231214T151049.356'

    def test_importMine_sum_sha512(self):
        hex_value = 'abcdef0123456789'
        b64_value = encode(decode(hex_value, 'hex'), 'base64').decode('utf-8').strip()
        body = '20231214T120000 https://host /path'
        headers = {'sum': 's,' + hex_value}
        msg = V02.importMine(body, headers, _make_options())
        assert 'identity' in msg
        assert msg['identity']['method'] == 'sha512'
        assert msg['identity']['value'] == b64_value
        assert 'sum' not in msg

    def test_importMine_sum_md5(self):
        hex_value = 'abcdef0123456789'
        b64_value = encode(decode(hex_value, 'hex'), 'base64').decode('utf-8').strip()
        body = '20231214T120000 https://host /path'
        headers = {'sum': 'd,' + hex_value}
        msg = V02.importMine(body, headers, _make_options())
        assert msg['identity']['method'] == 'md5'
        assert msg['identity']['value'] == b64_value

    def test_importMine_sum_remove(self):
        body = '20231214T120000 https://host /path'
        headers = {'sum': 'R,0'}
        msg = V02.importMine(body, headers, _make_options())
        assert 'fileOp' in msg
        assert 'remove' in msg['fileOp']
        assert 'identity' not in msg

    def test_importMine_sum_mkdir(self):
        body = '20231214T120000 https://host /path'
        headers = {'sum': 'm,0'}
        msg = V02.importMine(body, headers, _make_options())
        assert 'fileOp' in msg
        assert 'directory' in msg['fileOp']
        assert 'identity' not in msg

    def test_importMine_invalid_body_returns_none(self):
        body = 'only-one-token'
        msg = V02.importMine(body, {}, _make_options())
        assert msg is None

    def test_importMine_url_decodes_baseUrl(self):
        body = '20231214T120000 https://host/path%20with%23chars /file'
        msg = V02.importMine(body, {}, _make_options())
        assert msg['baseUrl'] == 'https://host/path with#chars'

    def test_importMine_subtopic_set(self):
        body = '20231214T120000 https://host /a/b/c.txt'
        msg = V02.importMine(body, {}, _make_options())
        assert msg['subtopic'] == ['a', 'b', 'c.txt']

    def test_importMine_integrity_renamed_from_headers(self):
        body = '20231214T120000 https://host /path'
        headers = {'integrity': {'method': 'sha512', 'value': 'abc'}}
        msg = V02.importMine(body, headers, _make_options())
        assert 'identity' in msg
        assert msg['identity']['method'] == 'sha512'
        assert 'integrity' not in msg
