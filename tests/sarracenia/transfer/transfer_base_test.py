import io
import os
import pytest
import tempfile
import time
from unittest.mock import patch, MagicMock, PropertyMock
from tests.conftest import *

import logging
from sarracenia.transfer import Transfer, alarm_cancel, alarm_set, alarm_raise, TimeoutException
import sarracenia.identity

logger = logging.getLogger(__name__)


class MockOptions:
    def __init__(self):
        self.logLevel = 'DEBUG'
        self.settings = {}
        self.timeout = 0
        self.bufSize = 1024
        self.byteRateMax = 0
        self.acceptSizeWrong = False
        self.nofsetstat = False


class ConcreteTransfer(Transfer):
    """Concrete subclass for testing the abstract Transfer base class."""
    @staticmethod
    def registered_as():
        return ['test']

    def __init__(self, proto, options):
        super().__init__(proto, options)

    def getcwd(self):
        return '/mock/cwd'


class Test_TimeoutException:
    def test_timeout_exception_is_exception(self):
        with pytest.raises(TimeoutException):
            raise TimeoutException("timed out")


class Test_alarm_functions:
    def test_alarm_cancel(self):
        # Should not raise
        alarm_cancel()

    def test_alarm_raise(self):
        with pytest.raises(TimeoutException):
            alarm_raise(14, None)

    def test_alarm_set(self):
        # Should not raise; just sets a signal alarm
        alarm_set(5)
        alarm_cancel()


class Test_Transfer_factory:
    def test_factory_known_protocol(self):
        options = MockOptions()
        t = Transfer.factory('test', options)
        assert t is not None
        assert isinstance(t, ConcreteTransfer)

    def test_factory_unknown_protocol(self):
        options = MockOptions()
        t = Transfer.factory('nonexistent_protocol', options)
        assert t is None


class Test_Transfer_init:
    def test_init_default_values(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        assert t.sumalgo is None
        assert t.checksum is None
        assert t.data_sumalgo is None
        assert t.data_checksum is None
        assert t.fpos == 0
        assert t.tbytes == 0
        assert t.byteRate == 0


class Test_Transfer_on_data:
    def test_on_data_passthrough(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        data = b'hello world'
        assert t.on_data(data) == data

    def test_on_data_empty(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        assert t.on_data(b'') == b''


class Test_Transfer_throttle:
    def test_throttle_no_limit(self):
        options = MockOptions()
        options.byteRateMax = 0
        t = ConcreteTransfer('test', options)
        t.tbytes = 0
        t.tbegin = time.time()
        # Should not sleep
        t.throttle(b'x' * 1000)
        assert t.tbytes == 1000

    def test_throttle_with_limit_no_sleep(self):
        options = MockOptions()
        options.byteRateMax = 1000000  # Very high limit
        t = ConcreteTransfer('test', options)
        t.tbytes = 0
        t.tbegin = time.time()
        t.throttle(b'x' * 100)
        assert t.tbytes == 100

    def test_throttle_updates_byte_rate(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.tbytes = 0
        t.tbegin = time.time() - 1.0  # Started 1 second ago
        t.throttle(b'x' * 1000)
        assert t.byteRate > 0


class Test_Transfer_local_io:
    def test_local_write_open_creates_file(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            fname = tmp.name
        try:
            os.unlink(fname)  # Remove so local_write_open creates it
            dst = t.local_write_open(fname)
            assert dst is not None
            dst.write(b'test data')
            t.local_write_close(dst)
            assert os.path.exists(fname)
            with open(fname, 'rb') as f:
                assert f.read() == b'test data'
        finally:
            if os.path.exists(fname):
                os.unlink(fname)

    def test_local_write_open_existing_file(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'existing content')
            fname = tmp.name
        try:
            dst = t.local_write_open(fname)
            dst.write(b'new')
            t.local_write_close(dst)
            with open(fname, 'rb') as f:
                content = f.read()
            assert content == b'new'  # Truncated after write
        finally:
            os.unlink(fname)

    def test_local_write_open_with_offset(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'0123456789')
            fname = tmp.name
        try:
            dst = t.local_write_open(fname, local_offset=5)
            dst.write(b'ABCDE')
            t.local_write_close(dst)
            with open(fname, 'rb') as f:
                content = f.read()
            assert content == b'01234ABCDE'
        finally:
            os.unlink(fname)

    def test_local_read_open_and_close(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'read this content')
            fname = tmp.name
        try:
            src = t.local_read_open(fname)
            data = src.read()
            assert data == b'read this content'
            t.local_read_close(src)
        finally:
            os.unlink(fname)

    def test_local_read_open_with_offset(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'0123456789')
            fname = tmp.name
        try:
            src = t.local_read_open(fname, local_offset=5)
            data = src.read()
            assert data == b'56789'
            t.local_read_close(src)
        finally:
            os.unlink(fname)


class Test_Transfer_read_write:
    def test_read_write_full_copy(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        src = io.BytesIO(b'hello world test data')
        dst = io.BytesIO()
        length = t.read_write(src, dst, length=0)
        assert length == len(b'hello world test data')
        assert dst.getvalue() == b'hello world test data'

    def test_read_write_exact_length(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        src = io.BytesIO(b'hello world test data')
        dst = io.BytesIO()
        length = t.read_write(src, dst, length=5)
        assert length == 5
        assert dst.getvalue() == b'hello'

    def test_read_write_empty_source(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        src = io.BytesIO(b'')
        dst = io.BytesIO()
        length = t.read_write(src, dst, length=0)
        assert length == 0

    def test_read_write_with_sumalgo(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.sumalgo = MagicMock()
        src = io.BytesIO(b'data')
        dst = io.BytesIO()
        t.read_write(src, dst, length=0)
        assert t.sumalgo.update.called

    def test_read_write_large_exact_length(self):
        """Test reading exact length larger than bufSize."""
        options = MockOptions()
        options.bufSize = 4
        t = ConcreteTransfer('test', options)
        src = io.BytesIO(b'abcdefghij')  # 10 bytes
        dst = io.BytesIO()
        length = t.read_write(src, dst, length=10)
        assert length == 10
        assert dst.getvalue() == b'abcdefghij'


class Test_Transfer_read_writelocal:
    def test_read_writelocal(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            fname = tmp.name
        try:
            src = io.BytesIO(b'test content for local write')
            rw_length = t.read_writelocal('remote/path', src, fname)
            assert rw_length == 28
            with open(fname, 'rb') as f:
                assert f.read() == b'test content for local write'
        finally:
            os.unlink(fname)

    def test_read_writelocal_length_mismatch(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            fname = tmp.name
        try:
            src = io.BytesIO(b'short')
            # Expect 100 bytes but only get 5
            rw_length = t.read_writelocal('remote/path', src, fname, length=100)
            assert rw_length == 5
        finally:
            os.unlink(fname)


class Test_Transfer_readlocal_write:
    def test_readlocal_write(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'local file content')
            fname = tmp.name
        try:
            dst = io.BytesIO()
            rw_length = t.readlocal_write(fname, dst=dst)
            assert rw_length == 18
            assert dst.getvalue() == b'local file content'
        finally:
            os.unlink(fname)


class Test_Transfer_checksum:
    def test_set_sumalgo(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.set_sumalgo('md5')
        assert t.sumalgo is not None
        assert t.data_sumalgo is not None

    def test_set_path(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.set_sumalgo('md5')
        t.set_path('/some/file.txt')
        # Should not raise

    def test_get_sumstr(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.set_sumalgo('md5')
        t.set_path('/some/file.txt')
        result = t.get_sumstr()
        assert result is not None
        assert 'method' in result
        assert 'value' in result

    def test_get_sumstr_no_algo(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        assert t.get_sumstr() is None

    def test_set_sumArbitrary(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.set_sumalgo('arbitrary')
        t.set_sumArbitrary('custom_value')
        assert t.sumalgo.value == 'custom_value'
        assert t.data_sumalgo.value == 'custom_value'

    def test_update_file(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.set_sumalgo('md5')
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'file content')
            fname = tmp.name
        try:
            t.update_file(fname)
            result = t.get_sumstr()
            assert result is not None
            assert result['value'] is not None
        finally:
            os.unlink(fname)


class Test_Transfer_metricsReport:
    def test_metrics_report(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        report = t.metricsReport()
        assert 'byteRateInstant' in report
        assert report['byteRateInstant'] == 0


class Test_Transfer_write_chunk:
    def test_write_chunk_init_and_end(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        assert t.rw_length == 0
        t.write_chunk(b'hello')
        assert t.rw_length == 5
        t.write_chunk(b' world')
        assert t.rw_length == 11
        result = t.write_chunk_end()
        assert result == 11
        assert dst.getvalue() == b'hello world'

    def test_write_chunk_with_sumalgo(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.sumalgo = MagicMock()
        dst = io.BytesIO()
        t.write_chunk_init(dst)
        t.write_chunk(b'data')
        assert t.sumalgo.update.called


class Test_Transfer_gethttpsUrl:
    def test_gethttpsurl_returns_none(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        assert t.gethttpsUrl('/any/path') is None


class Test_Transfer_logProgress:
    def test_logProgress_no_log_if_recent(self):
        options = MockOptions()
        t = ConcreteTransfer('test', options)
        t.lastLog = time.time()  # Just now
        # Should not log (and not raise)
        t.logProgress(1000)
