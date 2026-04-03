import pytest
import types
import os
import hashlib
import base64
import time
from curses.ascii import SOH, ETX

import sarracenia
import sarracenia.config
import sarracenia.flowcb.filter.wmo00_accumulate


def make_worklist():
    worklist = types.SimpleNamespace()
    worklist.ok = []
    worklist.incoming = []
    worklist.rejected = []
    worklist.failed = []
    worklist.directories_ok = []
    return worklist


def make_options(tmp_path):
    options = sarracenia.config.default_config()
    work_dir = str(tmp_path / 'output')
    options.batch = 50
    options.no = 1
    options.hostname = "testhost1.example.com"
    options.pid_filename = str(tmp_path / "myconfig_01.pid")
    options.wmo00_work_directory = work_dir
    options.wmo00_origin_CCCC = "CYKK"
    options.post_baseUrl = 'file://'
    os.makedirs(work_dir, exist_ok=True)
    return options


def make_message_with_content(data_bytes):
    m = sarracenia.Message()
    m['baseUrl'] = 'file:'
    m['relPath'] = '/foo/bar'
    m['content'] = {
        'encoding': 'base64',
        'value': base64.b64encode(data_bytes).decode('ascii'),
    }
    return m


class Test_Wmo00_accumulate:

    def test_empty_incoming(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        worklist = make_worklist()
        acc.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_file_too_small(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(b'tiny')]
        acc.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_file_too_large(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        acc.o.wmo00_byteCountMax = 20

        large_data = b'SACN37 CWAO 300104\r\r\n' + b'X' * 100
        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(large_data)]
        acc.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_valid_single_record(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)

        original = b'SACN37 CWAO 300104\r\r\nSome data'
        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(original)]
        acc.after_accept(worklist)

        assert len(worklist.incoming) == 1
        assert os.path.isfile(acc.accumulated_file)

        with open(acc.accumulated_file, 'rb') as f:
            content = f.read()

        # Bare AHL gets wrapped: SOH \r\r\n nnnnn \r\r\n data ETX
        inner_len = len(original) + 13
        wrapped = (f"\x01\r\r\n{inner_len:05d}\r\r\n".encode('ascii')
                   + original + b'\x03')
        expected = (f"{len(wrapped):08d}".encode('ascii')
                    + b'\x00\x00' + wrapped)
        assert content == expected

    def test_sequence_wraps(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        acc.sequence = 999999
        af = acc.open_accumulated_file()
        af.close()

        assert '999999' in acc.accumulated_file
        assert acc.sequence == 0

    def test_on_stop_writes_sequence(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        acc.sequence = 42
        acc.thisday = 15
        acc.on_stop()

        assert os.path.isfile(acc.sequence_file)
        with open(acc.sequence_file, 'r') as f:
            content = f.read()
        assert content == "15 42"

    def test_open_accumulated_file_naming(self, tmp_path):
        options = make_options(tmp_path)
        acc = sarracenia.flowcb.filter.wmo00_accumulate.Wmo00_accumulate(options)
        acc.sequence = 123
        af = acc.open_accumulated_file()
        af.close()

        expected_name = (
            f"CYKK{acc.sequence_first_digit}"
            f"{acc.sequence_second_digit}000123.a"
        )
        assert acc.accumulated_file.endswith(expected_name)
        assert acc.sequence == 124
