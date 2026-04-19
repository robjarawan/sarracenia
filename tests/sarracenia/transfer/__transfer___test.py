import pytest
from tests.conftest import *
#from unittest.mock import Mock

import logging

import sarracenia
import sarracenia.config
import sarracenia.transfer

logger = logging.getLogger('sarracenia.config')
logger.setLevel('DEBUG')

def test_factory():
    options = sarracenia.config.default_config()
    transfer = sarracenia.transfer.Transfer.factory('http', options)

    assert type(transfer) is sarracenia.transfer.https.Https

    transfer = sarracenia.transfer.Transfer.factory('DoesNotExist', options)
    assert transfer == None

def test___init__():
    options = sarracenia.config.default_config()
    transfer = sarracenia.transfer.Transfer.factory('http', options)

    assert transfer.fpos == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
import io
import os
import time
from unittest.mock import patch, MagicMock

import sarracenia.identity


def _make_transfer():
    """Return a file-protocol Transfer (has getcwd()) with default config."""
    options = sarracenia.config.default_config()
    options.timeout = 0  # disable alarm_set during tests
    t = sarracenia.transfer.Transfer.factory('file', options)
    return t


# ---------------------------------------------------------------------------
# Test_init – init() resets state variables
# ---------------------------------------------------------------------------
class Test_init:
    def test_init_resets_fpos(self):
        t = _make_transfer()
        t.fpos = 999
        t.init()
        assert t.fpos == 0

    def test_init_resets_tbytes(self):
        t = _make_transfer()
        t.tbytes = 12345
        t.init()
        assert t.tbytes == 0

    def test_init_resets_byteRate(self):
        t = _make_transfer()
        t.byteRate = 42.0
        t.init()
        assert t.byteRate == 0

    def test_init_resets_sumalgo(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        assert t.sumalgo is not None
        t.init()
        assert t.sumalgo is None

    def test_init_resets_checksum(self):
        t = _make_transfer()
        t.checksum = 'stale'
        t.init()
        assert t.checksum is None

    def test_init_sets_tbegin(self):
        t = _make_transfer()
        t.init()
        assert t.tbegin > 0


# ---------------------------------------------------------------------------
# Test_on_data – identity transform
# ---------------------------------------------------------------------------
class Test_on_data:
    def test_returns_chunk_unchanged(self):
        t = _make_transfer()
        chunk = b'hello world'
        assert t.on_data(chunk) is chunk

    def test_empty_chunk(self):
        t = _make_transfer()
        chunk = b''
        assert t.on_data(chunk) is chunk

    def test_binary_data(self):
        t = _make_transfer()
        chunk = bytes(range(256))
        assert t.on_data(chunk) is chunk


# ---------------------------------------------------------------------------
# Test_set_sumalgo – sets checksum algorithm
# ---------------------------------------------------------------------------
class Test_set_sumalgo:
    def test_md5(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        assert t.sumalgo is not None
        assert t.sumalgo.get_method() == 'md5'

    def test_sha512(self):
        t = _make_transfer()
        t.set_sumalgo('sha512')
        assert t.sumalgo is not None
        assert t.sumalgo.get_method() == 'sha512'

    def test_data_sumalgo_also_set(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        assert t.data_sumalgo is not None
        assert t.data_sumalgo.get_method() == 'md5'


# ---------------------------------------------------------------------------
# Test_get_sumstr – returns identity dict or None
# ---------------------------------------------------------------------------
class Test_get_sumstr:
    def test_returns_none_when_no_sumalgo(self):
        t = _make_transfer()
        assert t.sumalgo is None
        assert t.get_sumstr() is None

    def test_returns_dict_with_method_and_value(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        t.sumalgo.set_path('test')
        t.sumalgo.update(b'data')
        result = t.get_sumstr()
        assert isinstance(result, dict)
        assert 'method' in result
        assert 'value' in result
        assert result['method'] == 'md5'
        assert isinstance(result['value'], str)
        assert len(result['value']) > 0


# ---------------------------------------------------------------------------
# Test_throttle – updates byteRate correctly
# ---------------------------------------------------------------------------
class Test_throttle:
    def test_updates_tbytes(self):
        t = _make_transfer()
        t.tbytes = 0
        t.tbegin = sarracenia.nowflt()
        buf = b'x' * 100
        t.throttle(buf)
        assert t.tbytes == 100

    def test_accumulates_tbytes(self):
        t = _make_transfer()
        t.tbytes = 0
        t.tbegin = sarracenia.nowflt()
        t.throttle(b'a' * 50)
        t.throttle(b'b' * 70)
        assert t.tbytes == 120

    def test_byteRate_positive_after_throttle(self):
        t = _make_transfer()
        t.tbytes = 0
        t.tbegin = sarracenia.nowflt() - 1.0  # pretend started 1 second ago
        t.throttle(b'x' * 1000)
        assert t.byteRate > 0

    def test_byteRate_no_max_no_sleep(self):
        t = _make_transfer()
        t.o.byteRateMax = 0
        t.tbytes = 0
        t.tbegin = sarracenia.nowflt()
        start = time.time()
        t.throttle(b'x' * 1000)
        elapsed = time.time() - start
        assert elapsed < 1.0  # should not sleep


# ---------------------------------------------------------------------------
# Test_metricsReport – returns correct structure
# ---------------------------------------------------------------------------
class Test_metricsReport:
    def test_structure(self):
        t = _make_transfer()
        report = t.metricsReport()
        assert isinstance(report, dict)
        assert 'byteRateInstant' in report

    def test_reflects_byteRate(self):
        t = _make_transfer()
        t.byteRate = 42.5
        report = t.metricsReport()
        assert report['byteRateInstant'] == 42.5


# ---------------------------------------------------------------------------
# Test_local_write_open – opens for writing, creates if not exists
# ---------------------------------------------------------------------------
class Test_local_write_open:
    def test_creates_file_if_not_exists(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'newfile.dat')
        assert not os.path.isfile(filepath)
        dst = t.local_write_open(filepath)
        dst.close()
        assert os.path.isfile(filepath)

    def test_opens_existing_file(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'existing.dat')
        with open(filepath, 'wb') as f:
            f.write(b'existing content')
        dst = t.local_write_open(filepath)
        assert dst.readable()
        dst.close()

    def test_seeks_to_offset(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'seekfile.dat')
        with open(filepath, 'wb') as f:
            f.write(b'0123456789')
        dst = t.local_write_open(filepath, local_offset=5)
        assert dst.tell() == 5
        dst.close()

    def test_resets_checksum_and_fpos(self, tmp_path):
        t = _make_transfer()
        t.checksum = 'old'
        t.fpos = 99
        filepath = str(tmp_path / 'resetfile.dat')
        dst = t.local_write_open(filepath)
        assert t.checksum is None
        assert t.fpos == 0
        dst.close()


# ---------------------------------------------------------------------------
# Test_local_write_close – flush, fsync, truncate, close, finalize checksum
# ---------------------------------------------------------------------------
class Test_local_write_close:
    def test_truncates_at_write_position(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'truncfile.dat')
        with open(filepath, 'wb') as f:
            f.write(b'0123456789')
        dst = t.local_write_open(filepath)
        dst.write(b'ABC')
        t.local_write_close(dst)
        assert t.fpos == 3
        with open(filepath, 'rb') as f:
            assert f.read() == b'ABC'

    def test_fpos_set_after_close(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'fposfile.dat')
        dst = t.local_write_open(filepath)
        dst.write(b'hello')
        t.local_write_close(dst)
        assert t.fpos == 5

    def test_finalizes_checksum(self, tmp_path):
        t = _make_transfer()
        t.set_sumalgo('md5')
        filepath = str(tmp_path / 'csumfile.dat')
        dst = t.local_write_open(filepath)
        data = b'test data for checksum'
        t.sumalgo.set_path(filepath)
        t.data_sumalgo.set_path(filepath)
        t.sumalgo.update(data)
        t.data_sumalgo.update(data)
        dst.write(data)
        t.local_write_close(dst)
        assert t.checksum is not None
        assert isinstance(t.checksum, str)


# ---------------------------------------------------------------------------
# Test_local_read_open – opens file for reading, seeks if offset given
# ---------------------------------------------------------------------------
class Test_local_read_open:
    def test_opens_file(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        filepath = str(tmp_path / 'readfile.dat')
        with open(filepath, 'wb') as f:
            f.write(b'hello world')
        src = t.local_read_open(filepath)
        assert src.read() == b'hello world'
        src.close()

    def test_seeks_to_offset(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        filepath = str(tmp_path / 'seekread.dat')
        with open(filepath, 'wb') as f:
            f.write(b'0123456789')
        src = t.local_read_open(filepath, local_offset=3)
        assert src.read() == b'3456789'
        src.close()

    def test_resets_checksum(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        t.checksum = 'stale'
        filepath = str(tmp_path / 'csumreset.dat')
        with open(filepath, 'wb') as f:
            f.write(b'data')
        src = t.local_read_open(filepath)
        assert t.checksum is None
        src.close()


# ---------------------------------------------------------------------------
# Test_local_read_close – closes file and finalizes checksum
# ---------------------------------------------------------------------------
class Test_local_read_close:
    def test_closes_file(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'closefile.dat')
        with open(filepath, 'wb') as f:
            f.write(b'data')
        src = open(filepath, 'rb')
        t.local_read_close(src)
        assert src.closed

    def test_finalizes_checksum(self, tmp_path):
        t = _make_transfer()
        t.set_sumalgo('sha512')
        t.sumalgo.set_path('test')
        t.data_sumalgo.set_path('test')
        t.sumalgo.update(b'data')
        t.data_sumalgo.update(b'data')
        filepath = str(tmp_path / 'csumclose.dat')
        with open(filepath, 'wb') as f:
            f.write(b'data')
        src = open(filepath, 'rb')
        t.local_read_close(src)
        assert t.checksum is not None
        assert isinstance(t.checksum, str)


# ---------------------------------------------------------------------------
# Test_read_write – copies data between file-like objects
# ---------------------------------------------------------------------------
class Test_read_write:
    def test_entire_file_length_zero(self):
        t = _make_transfer()
        data = b'The quick brown fox jumps over the lazy dog'
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=0)
        assert written == len(data)
        dst.seek(0)
        assert dst.read() == data

    def test_exact_length(self):
        t = _make_transfer()
        data = b'0123456789ABCDEF'
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=10)
        assert written == 10
        dst.seek(0)
        assert dst.read() == b'0123456789'

    def test_length_greater_than_data(self):
        t = _make_transfer()
        data = b'short'
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=1000)
        assert written == len(data)
        dst.seek(0)
        assert dst.read() == data

    def test_empty_source(self):
        t = _make_transfer()
        src = io.BytesIO(b'')
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=0)
        assert written == 0

    def test_updates_checksum_when_sumalgo_set(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        t.sumalgo.set_path('test')
        data = b'checksum test data'
        src = io.BytesIO(data)
        dst = io.BytesIO()
        t.read_write(src, dst, length=0)
        # sumalgo was updated; verify by getting the value
        val = t.sumalgo.value
        assert val is not None and len(val) > 0

    def test_small_bufSize(self):
        t = _make_transfer()
        t.o.bufSize = 4  # force multiple iterations
        data = b'ABCDEFGHIJKLMNOP'  # 16 bytes
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=0)
        assert written == 16
        dst.seek(0)
        assert dst.read() == data

    def test_exact_length_with_small_bufSize(self):
        t = _make_transfer()
        t.o.bufSize = 5
        data = b'0123456789AB'  # 12 bytes
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=12)
        assert written == 12
        dst.seek(0)
        assert dst.read() == data

    def test_length_with_remainder(self):
        t = _make_transfer()
        t.o.bufSize = 5
        data = b'0123456789ABC'  # 13 bytes, request 13 => nc=2, r=3
        src = io.BytesIO(data)
        dst = io.BytesIO()
        written = t.read_write(src, dst, length=13)
        assert written == 13
        dst.seek(0)
        assert dst.read() == data


# ---------------------------------------------------------------------------
# Test_read_writelocal – reads from src, writes to local file
# ---------------------------------------------------------------------------
class Test_read_writelocal:
    def test_writes_to_local_file(self, tmp_path):
        t = _make_transfer()
        data = b'remote data content'
        src = io.BytesIO(data)
        local_file = str(tmp_path / 'local_dest.dat')
        written = t.read_writelocal('remote_path', src, local_file, length=0)
        assert written == len(data)
        with open(local_file, 'rb') as f:
            assert f.read() == data

    def test_with_offset(self, tmp_path):
        t = _make_transfer()
        # Pre-fill file
        local_file = str(tmp_path / 'offset_dest.dat')
        with open(local_file, 'wb') as f:
            f.write(b'HEADER')
        data = b'APPENDED'
        src = io.BytesIO(data)
        written = t.read_writelocal('remote', src, local_file, local_offset=6, length=0)
        assert written == len(data)
        with open(local_file, 'rb') as f:
            assert f.read() == b'HEADERAPPENDED'

    def test_creates_file_if_not_exists(self, tmp_path):
        t = _make_transfer()
        local_file = str(tmp_path / 'brand_new.dat')
        src = io.BytesIO(b'new file data')
        t.read_writelocal('src', src, local_file)
        assert os.path.isfile(local_file)

    def test_sets_checksum_with_sumalgo(self, tmp_path):
        t = _make_transfer()
        t.set_sumalgo('sha512')
        data = b'checksum data'
        src = io.BytesIO(data)
        local_file = str(tmp_path / 'csum.dat')
        t.read_writelocal('remote', src, local_file)
        assert t.checksum is not None


# ---------------------------------------------------------------------------
# Test_readlocal_write – reads local file, writes to dst
# ---------------------------------------------------------------------------
class Test_readlocal_write:
    def test_reads_entire_file(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        local_file = str(tmp_path / 'source.dat')
        data = b'local file content'
        with open(local_file, 'wb') as f:
            f.write(data)
        dst = io.BytesIO()
        written = t.readlocal_write(local_file, dst=dst)
        assert written == len(data)
        dst.seek(0)
        assert dst.read() == data

    def test_with_offset(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        local_file = str(tmp_path / 'offsetsrc.dat')
        with open(local_file, 'wb') as f:
            f.write(b'0123456789')
        dst = io.BytesIO()
        written = t.readlocal_write(local_file, local_offset=5, dst=dst)
        assert written == 5
        dst.seek(0)
        assert dst.read() == b'56789'

    def test_with_length(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        local_file = str(tmp_path / 'lensrc.dat')
        with open(local_file, 'wb') as f:
            f.write(b'0123456789')
        dst = io.BytesIO()
        written = t.readlocal_write(local_file, length=5, dst=dst)
        assert written == 5
        dst.seek(0)
        assert dst.read() == b'01234'

    def test_truncates_dst_when_shorter(self, tmp_path):
        t = _make_transfer()
        t.cwd = str(tmp_path)
        t.o.nofsetstat = False
        local_file = str(tmp_path / 'shortsrc.dat')
        with open(local_file, 'wb') as f:
            f.write(b'short')
        dst = io.BytesIO(b'longer existing content here')
        written = t.readlocal_write(local_file, length=0, dst=dst)
        assert written == 5
        # dst should be truncated to 5 bytes
        dst.seek(0)
        assert dst.read() == b'short'


# ---------------------------------------------------------------------------
# Test_write_chunk lifecycle
# ---------------------------------------------------------------------------
class Test_write_chunk:
    def test_init_sets_state(self):
        t = _make_transfer()
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        assert t.chunk_iow is dst
        assert t.rw_length == 0
        assert t.tbytes == 0.0

    def test_write_chunk_writes_data(self):
        t = _make_transfer()
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        t.write_chunk(b'hello')
        t.write_chunk(b' world')
        assert t.rw_length == 11
        dst.seek(0)
        assert dst.read() == b'hello world'

    def test_write_chunk_end_returns_length(self):
        t = _make_transfer()
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        t.write_chunk(b'data')
        result = t.write_chunk_end()
        assert result == 4
        assert t.chunk_iow is None

    def test_write_chunk_updates_checksum(self):
        t = _make_transfer()
        t.set_sumalgo('md5')
        t.sumalgo.set_path('test')
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        t.write_chunk(b'chunk1')
        t.write_chunk(b'chunk2')
        t.write_chunk_end()
        val = t.sumalgo.value
        assert val is not None and len(val) > 0

    def test_full_lifecycle(self):
        t = _make_transfer()
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        for i in range(5):
            t.write_chunk(b'x' * 10)
        total = t.write_chunk_end()
        assert total == 50
        dst.seek(0)
        assert dst.read() == b'x' * 50


# ---------------------------------------------------------------------------
# Test_logProgress – logs if enough time passed
# ---------------------------------------------------------------------------
class Test_logProgress:
    def test_no_log_when_interval_not_elapsed(self, caplog):
        t = _make_transfer()
        t.lastLog = sarracenia.nowflt()
        t.logMinimumInterval = 60
        with caplog.at_level(logging.INFO, logger='sarracenia.transfer'):
            t.logProgress(1000)
        assert 'written so far' not in caplog.text

    def test_logs_when_interval_elapsed(self, caplog):
        t = _make_transfer()
        t.lastLog = sarracenia.nowflt() - 120  # 2 minutes ago
        t.logMinimumInterval = 60
        with caplog.at_level(logging.INFO, logger='sarracenia.transfer'):
            t.logProgress(1000)
        assert 'written so far' in caplog.text


# ---------------------------------------------------------------------------
# Test_set_sumArbitrary – sets arbitrary checksum value
# ---------------------------------------------------------------------------
class Test_set_sumArbitrary:
    def test_sets_value(self):
        t = _make_transfer()
        t.set_sumalgo('arbitrary')
        t.set_sumArbitrary('custom_value')
        assert t.sumalgo.value == 'custom_value'
        assert t.data_sumalgo.value == 'custom_value'


# ---------------------------------------------------------------------------
# Test_update_file and set_path – delegates to sumalgo
# ---------------------------------------------------------------------------
class Test_update_file:
    def test_update_file_with_real_file(self, tmp_path):
        t = _make_transfer()
        t.set_sumalgo('md5')
        filepath = str(tmp_path / 'hashme.dat')
        with open(filepath, 'wb') as f:
            f.write(b'file to hash')
        t.update_file(filepath)
        assert t.sumalgo.value is not None

    def test_update_file_noop_when_no_sumalgo(self, tmp_path):
        t = _make_transfer()
        filepath = str(tmp_path / 'noop.dat')
        with open(filepath, 'wb') as f:
            f.write(b'data')
        # Should not raise
        t.update_file(filepath)

    def test_set_path_delegates(self):
        t = _make_transfer()
        t.set_sumalgo('sha512')
        t.set_path('some/path')
        # Should not raise; sumalgo.set_path was called

    def test_set_path_noop_when_no_sumalgo(self):
        t = _make_transfer()
        # Should not raise
        t.set_path('some/path')


# ---------------------------------------------------------------------------
# Test_gethttpsUrl – returns None for base Transfer
# ---------------------------------------------------------------------------
class Test_gethttpsUrl:
    def test_returns_none(self):
        t = _make_transfer()
        assert t.gethttpsUrl('/some/path') is None
