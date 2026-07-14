import logging
import os
import types

import pytest

import sarracenia
import sarracenia.config
import sarracenia.flowcb.filter.wmo2msc


def run_filter(tmp_path, caplog, header, body):
    input_dir = tmp_path / 'original'
    output_dir = tmp_path / 'converted'
    input_dir.mkdir()
    output_dir.mkdir()
    input_file = input_dir / 'bulletin.wmo'
    input_file.write_bytes(header + body)

    options = sarracenia.config.default_config()
    options.currentDir = str(output_dir)
    options.filter_wmo2msc_replace_dir = f'{input_dir},{output_dir}'
    options.filter_wmo2msc_treeify = False
    options.filter_wmo2msc_convert = True
    options.filter_wmo2msc_uniquify = 'hash'
    options.post_baseDir = str(output_dir)

    message = sarracenia.Message()
    message['baseUrl'] = 'file:'
    message['relPath'] = str(input_file)
    worklist = types.SimpleNamespace(ok=[message], rejected=[])
    callback = sarracenia.flowcb.filter.wmo2msc.Wmo2msc(options)

    caplog.set_level(logging.INFO, logger='sarracenia.flowcb.filter.wmo2msc')
    callback.after_work(worklist)

    assert worklist.rejected == []
    assert worklist.ok == [message]
    output_file = output_dir / message['relPath']
    assert os.path.isfile(output_file)
    return output_file.read_bytes()


@pytest.mark.parametrize('marker', [b'BUFR', b'GRIB', b'\x89PNG'])
def test_after_work_detects_binary_markers_as_bytes(tmp_path, caplog, marker):
    output = run_filter(
        tmp_path,
        caplog,
        b'SACN37 CWAO 300104\r\r\n',
        marker + b'\x03\rBINARY DATA',
    )

    assert b'\x03' in output
    assert '(wmo-binary)' in caplog.text


def test_after_work_detects_special_binary_header_as_bytes(tmp_path, caplog):
    output = run_filter(
        tmp_path,
        caplog,
        b'SFUK45 EGRR 300104\r\r\n',
        b'DATA\rONE\rTWO',
    )

    assert b'DATA\rONE\rTWO' in output
    assert '(unknown-binary)' in caplog.text
