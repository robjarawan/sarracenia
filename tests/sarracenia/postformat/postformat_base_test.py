import json
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import *

import logging
import sarracenia
from sarracenia.postformat import PostFormat

logger = logging.getLogger(__name__)


class Test_PostFormat_content_type:
    """Tests for PostFormat.content_type() static method."""

    def test_content_type_v03(self):
        assert PostFormat.content_type('v03') == 'application/json'

    def test_content_type_v02(self):
        assert PostFormat.content_type('v02') == 'text/plain'

    def test_content_type_wis(self):
        assert PostFormat.content_type('wis') == 'application/geo+json'

    def test_content_type_unknown_format(self):
        assert PostFormat.content_type('nonexistent_format') is None

    def test_content_type_empty_string(self):
        assert PostFormat.content_type('') is None

    def test_content_type_case_sensitive(self):
        # PostFormat uses lowercase comparison on class name
        assert PostFormat.content_type('V03') is None

    def test_content_type_swim(self):
        result = PostFormat.content_type('swim')
        # swim format exists as a subclass
        assert result is not None


class Test_PostFormat_importAny:
    """Tests for PostFormat.importAny() static method."""

    def test_importAny_v03_json(self):
        payload = json.dumps({
            'pubTime': '20230118T151049.356378078',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 1024,
        })
        headers = {}
        msg = PostFormat.importAny(payload, headers, 'application/json', {})
        assert msg is not None
        assert msg['_format'] == 'v03'
        assert msg['baseUrl'] == 'https://example.com'
        assert msg['size'] == 1024

    def test_importAny_v02_plaintext(self):
        body = '20180118151049.356378078 https://example.com /data/file.txt'
        headers = {'sum': 'd,c35f14e247931c3185d5dc69c5cd543e'}
        msg = PostFormat.importAny(body, headers, 'text/plain', {})
        assert msg is not None
        assert msg['_format'] == 'v02'
        assert msg['baseUrl'] == 'https://example.com'

    def test_importAny_unknown_format_returns_none(self):
        # Binary payload that doesn't match any known format
        payload = b'\x00\x01\x02'
        headers = {}
        msg = PostFormat.importAny(payload, headers, 'application/octet-stream', {})
        assert msg is None

    def test_importAny_geojson(self):
        geojson = {
            'type': 'Feature',
            'version': 'v04',
            'geometry': {'type': 'Point', 'coordinates': [0, 0]},
            'properties': {
                'pubtime': '2023-01-18T15:10:49Z',
                'data_id': 'test-data-id',
            },
            'links': [{
                'href': 'https://example.com/data/file.txt',
                'length': 1024,
                'rel': 'canonical',
            }],
        }
        payload = json.dumps(geojson)
        headers = {'topic': 'origin/a/b'}
        msg = PostFormat.importAny(payload, headers, 'application/geo+json', {})
        assert msg is not None
        assert msg['_format'] == 'wis'


class Test_PostFormat_exportAny:
    """Tests for PostFormat.exportAny() static method."""

    def test_exportAny_v03(self):
        msg = {
            'pubTime': '20230118T151049.356378078',
            'baseUrl': 'https://example.com',
            'relPath': 'data/file.txt',
            'size': 1024,
        }
        options = {
            'post_format': 'v03',
            'topicPrefix': ['v03'],
            'publishers': [{'broker': MagicMock(url=MagicMock(scheme='amqp')), 'topicPrefix': ['v03'], 'exchange': None}],
            'publisher_index': 0,
        }
        body, headers, content_type = PostFormat.exportAny(msg, 'v03', ['v03'], options)
        assert body is not None
        assert content_type == 'application/json'
        assert 'topic' in headers
        parsed = json.loads(body)
        assert parsed['baseUrl'] == 'https://example.com'

    def test_exportAny_unknown_format(self):
        msg = {'pubTime': '20230118T151049.356378078'}
        body, headers, content_type = PostFormat.exportAny(msg, 'nonexistent')
        assert body is None
        assert headers is None
        assert content_type is None

    def test_exportAny_empty_format(self):
        msg = {'pubTime': '20230118T151049.356378078'}
        body, headers, content_type = PostFormat.exportAny(msg, '')
        assert body is None
        assert headers is None
        assert content_type is None


class Test_PostFormat_topicDerive:
    """Tests for PostFormat.topicDerive() static method."""

    def test_topicDerive_amqp_with_topic_list(self):
        msg = {'topic': ['v03', 'data', 'file']}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        assert topic == ['v03', 'data', 'file']

    def test_topicDerive_amqp_with_topic_string(self):
        msg = {'topic': 'v03.data.file'}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        assert topic == ['v03', 'data', 'file']

    def test_topicDerive_amqp_no_topic_with_relPath(self):
        msg = {'relPath': 'a/b/c/file.txt'}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        # Should be topicPrefix + relPath dirs (all but last part)
        assert topic == ['v03', 'a', 'b', 'c']

    def test_topicDerive_amqp_no_topic_with_subtopic(self):
        msg = {'subtopic': ['sub1', 'sub2']}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        assert topic == ['v03', 'sub1', 'sub2']

    def test_topicDerive_amqp_no_topic_no_relpath_no_subtopic(self):
        msg = {}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        assert topic == ['v03']

    def test_topicDerive_mqtt_with_exchange(self):
        msg = {'relPath': 'a/b/file.txt', 'identity': {'value': 'abc123'}}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='mqtt')),
                'topicPrefix': ['v03'],
                'exchange': ['xs_test'],
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        # For MQTT with exchange, topic_prefix includes exchange + topicPrefix
        assert topic[0] == 'xs_test'
        assert 'v03' in topic

    def test_topicDerive_mqtt_with_exchange_split(self):
        msg = {
            'relPath': 'data/file.txt',
            'identity': {'value': 'abc123'},
        }
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='mqtt')),
                'topicPrefix': ['v03'],
                'exchange': ['xs_test_0', 'xs_test_1'],
                'exchangeSplit': 2,
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        # Exchange is selected via identity hash modulo
        assert topic[0] in ['xs_test_0', 'xs_test_1']

    def test_topicDerive_publisher_topic_string(self):
        msg = {}
        options = {
            'publishers': [{
                'broker': MagicMock(url=MagicMock(scheme='amqp')),
                'topicPrefix': ['v03'],
                'exchange': None,
                'topic': 'v03.custom.topic',
            }],
            'publisher_index': 0,
        }
        topic = PostFormat.topicDerive(msg, options)
        assert topic == ['v03', 'custom', 'topic']
