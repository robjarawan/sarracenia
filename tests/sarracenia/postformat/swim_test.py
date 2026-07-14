import gzip

from sarracenia.postformat import PostFormat


def test_export_returns_body_headers_and_content_type():
    content = '<iwxxm:METAR xmlns:iwxxm="http://icao.int/iwxxm/3.0" />'
    message = {
        'baseUrl': 'https://example.test/data/',
        'relPath': 'metar.xml',
        'contentType': 'application/xml',
        'content': {'encoding': 'utf-8', 'value': content},
        'identity': {'method': 'sha512', 'value': 'abc123'},
    }
    options = {'post_topicPrefix': ['origin', 'a', 'wis2', 'ca-test']}

    payload, headers, content_type = PostFormat.exportAny(message, 'swim', options=options)

    assert gzip.decompress(payload).decode('utf-8') == content
    assert content_type == 'application/xml'
    assert 'body' not in headers
    assert 'amqp1_content_type' not in headers
    assert headers['topic'] == 'origin.a.wis2.ca-test.weather.metar'
    assert headers['amqp1_address'] == headers['topic']
    assert headers['properties.integrity.value'] == 'abc123'
    assert 'amqp1_content_type' not in message
