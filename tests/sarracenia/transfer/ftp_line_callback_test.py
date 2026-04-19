import pytest
from tests.conftest import *

import logging
import sarracenia.config
from sarracenia.transfer.ftp import Ftp

logger = logging.getLogger('sarracenia.transfer.ftp')
logger.setLevel('DEBUG')


def _make_ftp():
    """Return an Ftp instance suitable for testing line_callback."""
    options = sarracenia.config.default_config()
    options.timeout = 300
    options.bufSize = 1024 * 1024
    ftp = Ftp('ftp', options)
    ftp.entries = {}
    return ftp


class Test_registered_as:
    def test_returns_ftp(self):
        assert Ftp.registered_as() == ['ftp']


class Test_line_callback_linux:
    def test_normal_linux_line(self):
        ftp = _make_ftp()
        line = "-rw-r--r-- 1 user group 12345 Jan 15 10:30 myfile.txt"
        ftp.line_callback(line)
        assert 'myfile.txt' in ftp.entries
        assert 'myfile.txt' == ftp.entries['myfile.txt'].split()[-1]

    def test_linux_spaces_in_filename(self):
        ftp = _make_ftp()
        line = "-rw-r--r-- 1 user group 12345 Jan 15 10:30 my file with spaces.txt"
        ftp.line_callback(line)
        assert 'my file with spaces.txt' in ftp.entries
        assert ftp.entries['my file with spaces.txt'].endswith('my file with spaces.txt')

    def test_symlink_line(self):
        ftp = _make_ftp()
        line = "lrwxrwxrwx 1 user group 15 Jan 15 10:30 link -> target"
        ftp.line_callback(line)
        assert 'link -> target' in ftp.entries


class Test_line_callback_wisconsin:
    def test_wisconsin_extra_auth_field(self):
        """Wisconsin FTP server has an extra non-numeric auth field at index 4."""
        # Normal linux: perm links user group SIZE month day time filename
        #   index:       0     1     2    3     4    5     6   7   8+
        # Wisconsin:    perm links user group AUTH SIZE month day time filename
        #   index:       0     1     2    3    4    5     6    7   8   9+
        ftp = _make_ftp()
        line = "-rw-r--r-- 1 user group auth 12345 Jan 15 10:30 wisconsin_file.txt"
        ftp.line_callback(line)
        assert 'wisconsin_file.txt' in ftp.entries

    def test_wisconsin_spaces_in_filename(self):
        ftp = _make_ftp()
        line = "-rw-r--r-- 1 user group auth 12345 Jan 15 10:30 my wisconsin file.txt"
        ftp.line_callback(line)
        assert 'my wisconsin file.txt' in ftp.entries


class Test_line_callback_windows:
    def test_windows_format(self):
        ftp = _make_ftp()
        line = "01-15-26  10:30AM       12345 filename.dat"
        ftp.line_callback(line)
        assert 'filename.dat' in ftp.entries

    def test_windows_spaces_in_filename(self):
        ftp = _make_ftp()
        line = "01-15-26  10:30AM       12345 my windows file.dat"
        ftp.line_callback(line)
        assert 'my windows file.dat' in ftp.entries


class Test_line_callback_whitespace:
    def test_tabs_replaced_with_spaces(self):
        ftp = _make_ftp()
        line = "-rw-r--r--\t1\tuser\tgroup\t12345\tJan\t15\t10:30\ttabfile.txt"
        ftp.line_callback(line)
        assert 'tabfile.txt' in ftp.entries

    def test_leading_trailing_whitespace_stripped(self):
        ftp = _make_ftp()
        line = "   -rw-r--r-- 1 user group 12345 Jan 15 10:30 padded.txt   "
        ftp.line_callback(line)
        assert 'padded.txt' in ftp.entries

    def test_trailing_newline_stripped(self):
        ftp = _make_ftp()
        line = "-rw-r--r-- 1 user group 12345 Jan 15 10:30 newline.txt\n"
        ftp.line_callback(line)
        assert 'newline.txt' in ftp.entries


class Test_line_callback_short_lines:
    def test_single_field(self):
        """A line with a single word results in an empty-string key."""
        ftp = _make_ftp()
        ftp.line_callback("onlyfield")
        # With < 8 fields, code does opart2[3:] which is empty -> ''
        assert '' in ftp.entries

    def test_four_fields(self):
        ftp = _make_ftp()
        ftp.line_callback("a b c myname")
        assert 'myname' in ftp.entries

    def test_three_fields_empty_filename(self):
        """Three fields means opart2[3:] is empty -> key is ''."""
        ftp = _make_ftp()
        ftp.line_callback("a b c")
        assert '' in ftp.entries


class Test_line_callback_accumulates:
    def test_multiple_calls_grow_entries(self):
        ftp = _make_ftp()
        ftp.line_callback("-rw-r--r-- 1 user group 12345 Jan 15 10:30 file1.txt")
        ftp.line_callback("-rw-r--r-- 1 user group 67890 Feb 20 14:00 file2.txt")
        ftp.line_callback("01-15-26  10:30AM       99999 file3.dat")
        assert len(ftp.entries) == 3
        assert 'file1.txt' in ftp.entries
        assert 'file2.txt' in ftp.entries
        assert 'file3.dat' in ftp.entries

    def test_duplicate_filename_overwrites(self):
        ftp = _make_ftp()
        ftp.line_callback("-rw-r--r-- 1 user group 100 Jan 15 10:30 same.txt")
        ftp.line_callback("-rw-r--r-- 1 user group 200 Feb 20 14:00 same.txt")
        assert len(ftp.entries) == 1
        assert '200' in ftp.entries['same.txt']


class Test_line_callback_entry_value:
    def test_value_is_normalized_line(self):
        """The stored value should be a single-space-separated version of all fields."""
        ftp = _make_ftp()
        line = "-rw-r--r--  1  user  group  12345  Jan  15  10:30  valuefile.txt"
        ftp.line_callback(line)
        value = ftp.entries['valuefile.txt']
        # No double spaces in the value
        assert '  ' not in value
        assert value == "-rw-r--r-- 1 user group 12345 Jan 15 10:30 valuefile.txt"
