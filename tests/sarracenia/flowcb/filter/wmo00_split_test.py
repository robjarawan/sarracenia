import base64
import os
import types

import pytest

import sarracenia
import sarracenia.config
import sarracenia.flowcb.filter.wmo00_split


def make_worklist(content):
    message = sarracenia.Message()
    message['baseUrl'] = 'file:'
    message['relPath'] = '/grouped.wmo'
    message['content'] = {'encoding': 'base64', 'value': base64.b64encode(content)}
    return types.SimpleNamespace(incoming=[message], rejected=[], ok=[], failed=[])


@pytest.mark.parametrize('digits', [b'123', b'00123'])
def test_after_accept_preserves_ahl_for_supported_inner_header_widths(tmp_path, digits):
    options = sarracenia.config.default_config()
    options.wmo00_work_directory = str(tmp_path)
    options.wmo00_tree = False
    options.wmo00_encapsulate = False
    options.publishers = [{'baseDir': None, 'broker': 'amqp://localhost', 'baseUrl': 'file:/'}]
    options.post_baseUrl = 'file://'

    ahl_and_payload = b'SACN37 CWAO 300104\r\r\nBULLETIN DATA\r\r\n'
    inner = b'\x01\r\r\n' + digits + b'\r\r\n' + ahl_and_payload + b'\x03'
    grouped = f'{len(inner):08d}'.encode('ascii') + b'\x00\x00' + inner
    worklist = make_worklist(grouped)

    splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)
    splitter.after_accept(worklist)

    assert len(worklist.incoming) == 1
    output_message = worklist.incoming[0]
    output_path = os.sep + output_message['relPath']
    with open(output_path, 'rb') as output_file:
        assert output_file.read() == ahl_and_payload
