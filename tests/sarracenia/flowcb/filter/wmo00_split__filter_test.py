import pytest
import types
import os
import hashlib
import base64
from curses.ascii import SOH, ETX

import sarracenia
import sarracenia.config
import sarracenia.flowcb.filter.wmo00_split


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
    options.wmo00_work_directory = work_dir
    options.wmo00_tree = True
    options.wmo00_encapsulate = True
    options.permDirDefault = 0o775
    options.post_baseUrl = 'file://'
    os.makedirs(work_dir, exist_ok=True)
    return options


def build_inner(ahl_line, data_body):
    """Build inner WMO data: SOH \\r\\r\\n nnnnn \\r\\r\\n AHL \\r\\r\\n DATA ETX"""
    ahl_bytes = ahl_line.encode('ascii')
    body = ahl_bytes + b'\r\r\n' + data_body
    inner_len = 13 + len(body)
    inner = (bytes([SOH]) + b'\r\r\n'
             + f"{inner_len:05d}".encode('ascii') + b'\r\r\n'
             + body + bytes([ETX]))
    return inner


def build_wmo00_record(ahl_line, data_body):
    """Build a complete WMO-00 outer record (8-byte length + \\0\\0 + inner)."""
    inner = build_inner(ahl_line, data_body)
    outer = f"{len(inner):08d}".encode('ascii') + b'\x00\x00' + inner
    return outer


def make_message_with_content(data_bytes):
    m = sarracenia.Message()
    m['baseUrl'] = 'file:'
    m['relPath'] = '/foo/bar'
    m['content'] = {
        'encoding': 'base64',
        'value': base64.b64encode(data_bytes).decode('ascii'),
    }
    return m


class Test_Wmo00_split:

    def test_empty_incoming(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)
        worklist = make_worklist()
        splitter.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_file_too_small(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)
        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(b'tiny')]
        splitter.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_corrupt_length_field(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)
        worklist = make_worklist()
        corrupt = b'ABCDEFGH\x00\x00' + b'padding data extra bytes'
        worklist.incoming = [make_message_with_content(corrupt)]
        splitter.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_length_past_eof(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)
        worklist = make_worklist()
        data = b'99999999\x00\x00' + b'short payload'
        worklist.incoming = [make_message_with_content(data)]
        splitter.after_accept(worklist)
        assert len(worklist.incoming) == 0

    def test_valid_single_record_tree(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)

        ahl = "SACN37 CWAO 300104"
        data_body = b'Some test data here'
        record = build_wmo00_record(ahl, data_body)

        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(record)]
        splitter.after_accept(worklist)

        assert len(worklist.incoming) == 1

        # TT=SA, CCCC=CWAO, GG=01
        work_dir = str(tmp_path / 'output')
        tree_dir = os.path.join(work_dir, 'SA', 'CWAO', '01')
        assert os.path.isdir(tree_dir)

        inner = build_inner(ahl, data_body)
        expected_hash = hashlib.md5(inner).hexdigest()
        # No RRR (len 18 ≤ 19), so extra underscore before hash
        expected_filename = f"SACN37_CWAO_300104__{expected_hash}"
        expected_path = os.path.join(tree_dir, expected_filename)
        assert os.path.isfile(expected_path)

        with open(expected_path, 'rb') as f:
            content = f.read()
        assert content == inner

    def test_valid_single_record_flat(self, tmp_path):
        options = make_options(tmp_path)
        options.wmo00_tree = False
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)

        ahl = "SACN37 CWAO 300104"
        data_body = b'Some test data here'
        record = build_wmo00_record(ahl, data_body)

        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(record)]
        splitter.after_accept(worklist)

        assert len(worklist.incoming) == 1

        work_dir = str(tmp_path / 'output')
        inner = build_inner(ahl, data_body)
        expected_hash = hashlib.md5(inner).hexdigest()
        expected_filename = f"SACN37_CWAO_300104__{expected_hash}"
        expected_path = os.path.join(work_dir, expected_filename)
        assert os.path.isfile(expected_path)

    def test_ahl_too_short(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)

        ahl = "SHORT"
        data_body = b'Some test data here'
        record = build_wmo00_record(ahl, data_body)

        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(record)]
        splitter.after_accept(worklist)

        assert len(worklist.incoming) == 0

    def test_multiple_records(self, tmp_path):
        options = make_options(tmp_path)
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)

        ahl1 = "SACN37 CWAO 300104"
        ahl2 = "UANT32 CWQQ 282015 AMD"
        data1 = b'First bulletin data'
        data2 = b'Second bulletin data'

        combined = build_wmo00_record(ahl1, data1) + build_wmo00_record(ahl2, data2)

        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(combined)]
        splitter.after_accept(worklist)

        assert len(worklist.incoming) == 2

        work_dir = str(tmp_path / 'output')
        assert os.path.isdir(os.path.join(work_dir, 'SA', 'CWAO', '01'))
        assert os.path.isdir(os.path.join(work_dir, 'UA', 'CWQQ', '20'))

        # Verify second record (with RRR) has correct filename format
        inner2 = build_inner(ahl2, data2)
        expected_hash2 = hashlib.md5(inner2).hexdigest()
        # Has RRR (len 22 > 19), no extra underscore
        expected_filename2 = f"UANT32_CWQQ_282015_AMD_{expected_hash2}"
        expected_path2 = os.path.join(work_dir, 'UA', 'CWQQ', '20', expected_filename2)
        assert os.path.isfile(expected_path2)

    def test_encapsulate_off(self, tmp_path):
        options = make_options(tmp_path)
        options.wmo00_encapsulate = False
        splitter = sarracenia.flowcb.filter.wmo00_split.Wmo00_split(options)

        ahl = "SACN37 CWAO 300104"
        data_body = b'Some test data here'
        record = build_wmo00_record(ahl, data_body)

        worklist = make_worklist()
        worklist.incoming = [make_message_with_content(record)]
        splitter.after_accept(worklist)

        assert len(worklist.incoming) == 1

        # Without encapsulation, payload is AHL + \r\r\n + data (no SOH/ETX)
        payload = ahl.encode('ascii') + b'\r\r\n' + data_body
        expected_hash = hashlib.md5(payload).hexdigest()

        work_dir = str(tmp_path / 'output')
        expected_filename = f"SACN37_CWAO_300104__{expected_hash}"
        expected_path = os.path.join(work_dir, 'SA', 'CWAO', '01', expected_filename)
        assert os.path.isfile(expected_path)

        with open(expected_path, 'rb') as f:
            content = f.read()
        assert content == payload
