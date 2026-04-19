import json
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import *

import logging
import sarracenia
from sarracenia.postformat.v02 import V02

logger = logging.getLogger(__name__)


class Test_V02_content_type:
    def test_content_type(self):
        assert V02.content_type() == 'text/plain'


class Test_V02_mine:
    def test_mine_text_plain(self):
        assert V02.mine('some body', {}, 'text/plain', {}) is True

    def test_mine_json_content_type(self):
        assert V02.mine('{}', {}, 'application/json', {}) is False

    def test_mine_plain_text_body_no_json(self):
        """If body is a string not starting with '{', it should be recognized as v02."""
        assert V02.mine('20180118 https://example.com /file.txt', {}, '', {}) is True

    def test_mine_json_body(self):
        """If body starts with '{', it's probably not v02."""
        assert V02.mine('{"key": "value"}', {}, '', {}) is False

    def test_mine_v02_topic_header(self):
        """Headers with v02 topic should be recognized."""
        assert V02.mine('{}', {'topic': 'v02.post.data'}, '', {}) is True

    def test_mine_v03_topic_header(self):
        assert V02.mine('{}', {'topic': 'v03.post.data'}, '', {}) is False

    def test_mine_empty_headers(self):
        assert V02.mine('plain text body', {}, '', {}) is True


class Test_V02_importMine:
    def test_basic_import(self):
        body = '20180118151049.356378078 https://example.com /data/file.txt'
        headers = {}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg['_format'] == 'v02'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['relPath'] == '/data/file.txt'
        assert msg['to_clusters'] == 'ALL'

    def test_import_pubtime_conversion(self):
        """v2 pubTime should be converted to v3 format with 'T' separator."""
        body = '20180118151049.356 https://example.com /file.txt'
        msg = V02.importMine(body, {}, {})
        assert msg is not None
        assert 'T' in msg['pubTime']

    def test_import_malformed_body_too_few_fields(self):
        """Body with fewer than 3 fields should return None."""
        body = '20180118151049.356'
        msg = V02.importMine(body, {}, {})
        assert msg is None

    def test_import_empty_body(self):
        msg = V02.importMine('', {}, {})
        assert msg is None

    def test_import_url_encoding_in_baseurl(self):
        """Percent-encoded spaces and hashes should be decoded in baseUrl."""
        body = '20180118151049 https://example.com/path%20with%23hash /file.txt'
        msg = V02.importMine(body, {}, {})
        assert msg is not None
        assert '%20' not in msg['baseUrl']
        assert '%23' not in msg['baseUrl']
        assert ' ' in msg['baseUrl']
        assert '#' in msg['baseUrl']

    def test_import_subtopic_from_relpath(self):
        body = '20180118151049 https://example.com /data/subdir/file.txt'
        msg = V02.importMine(body, {}, {})
        assert msg is not None
        assert msg['subtopic'] == ['', 'data', 'subdir', 'file.txt']

    def test_import_sum_sha512(self):
        """Sum field with 's' code should map to sha512 identity."""
        hex_hash = 'abcdef0123456789' * 8  # 128 hex chars
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': f's,{hex_hash}'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'identity' in msg
        assert msg['identity']['method'] == 'sha512'
        assert 'sum' not in msg

    def test_import_sum_md5(self):
        hex_hash = 'abcdef0123456789' * 2  # 32 hex chars
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': f'd,{hex_hash}'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg['identity']['method'] == 'md5'

    def test_import_sum_random(self):
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': '0,some_random_value'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg['identity']['method'] == 'random'
        assert msg['identity']['value'] == 'some_random_value'

    def test_import_sum_arbitrary(self):
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': 'a,arbitrary_value_here'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg['identity']['method'] == 'arbitrary'
        assert msg['identity']['value'] == 'arbitrary_value_here'

    def test_import_sum_remove_operation(self):
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': 'R,00'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert 'remove' in msg['fileOp']

    def test_import_sum_mkdir_operation(self):
        body = '20180118151049 https://example.com /newdir/'
        headers = {'sum': 'm,00'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert 'directory' in msg['fileOp']

    def test_import_sum_rmdir_operation(self):
        body = '20180118151049 https://example.com /olddir/'
        headers = {'sum': 'r,00'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert 'remove' in msg['fileOp']
        assert 'directory' in msg['fileOp']

    def test_import_sum_link_operation(self):
        body = '20180118151049 https://example.com /linked.txt'
        hex_hash = 'abcdef01' * 4  # some hex
        headers = {'sum': f'L,{hex_hash}', 'link': '/target/file.txt'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert 'link' in msg['fileOp']

    def test_import_rename_with_oldname(self):
        hex_hash = 'abcdef01' * 4
        body = '20180118151049 https://example.com /new_name.txt'
        headers = {'sum': f'd,{hex_hash}', 'oldname': '/old_name.txt'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert msg['fileOp']['rename'] == '/old_name.txt'
        assert 'oldname' not in msg

    def test_import_rename_with_mkdir(self):
        body = '20180118151049 https://example.com /newdir/'
        headers = {'sum': 'm,00', 'oldname': '/olddir/'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'fileOp' in msg
        assert msg['fileOp']['rename'] == '/olddir/'
        assert 'mkdir' in msg['fileOp']

    def test_import_corrupt_sum_field(self):
        """Corrupt sum field should be gracefully handled."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': 'INVALID_SUM_FORMAT'}
        msg = V02.importMine(body, headers, {})
        # Should still return msg, just without identity
        assert msg is not None

    def test_import_parts_simple_style(self):
        body = '20180118151049 https://example.com /file.txt'
        headers = {'parts': '1,4096,1,0,0'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg['size'] == 4096
        assert 'parts' not in msg

    def test_import_parts_inplace_style(self):
        """Inplace partitioned transfers should log error."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'parts': 'i,1024,10,512,3'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        # 'i' style is deprecated, should not create blocks
        assert 'parts' not in msg

    def test_import_corrupt_parts_field(self):
        body = '20180118151049 https://example.com /file.txt'
        headers = {'parts': 'NOT_A_VALID_PARTS_FIELD'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None

    def test_import_integrity_to_identity_conversion(self):
        """v02 messages may also have 'integrity' that should become 'identity'."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'integrity': {'method': 'sha512', 'value': 'xyz'}}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'identity' in msg
        assert 'integrity' not in msg

    def test_import_sum_cod_method(self):
        """Sum code 'z' maps to 'cod' with nested method."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': 'z,d'}  # cod,md5
        msg = V02.importMine(body, headers, {})
        assert msg is not None

    def test_import_deleteOnPost_includes_subtopic(self):
        body = '20180118151049 https://example.com /file.txt'
        msg = V02.importMine(body, {}, {})
        assert msg is not None
        assert 'subtopic' in msg['_deleteOnPost']

    def test_import_headers_copied_to_message(self):
        """Headers from the wire should be copied into the message."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'custom_header': 'custom_value', 'another': '123'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert msg.get('custom_header') == 'custom_value'
        assert msg.get('another') == '123'

    def test_import_sum_md5name_no_identity(self):
        """md5name sum code should not produce identity field."""
        body = '20180118151049 https://example.com /file.txt'
        headers = {'sum': 'n,0'}
        msg = V02.importMine(body, headers, {})
        assert msg is not None
        assert 'identity' not in msg
