import pytest
import types
import time
from unittest.mock import patch, mock_open, MagicMock

from sarracenia.bulletin import Bulletin


class MockOptions:
    """Minimal options object for Bulletin tests."""
    def __init__(self):
        self.inputCharset = 'utf-8'
        self.binaryInitialCharacters = [b'GRIB', b'BUFR', b'\x00\x00']


def make_bulletin(options=None):
    if options is None:
        options = MockOptions()
    return Bulletin(options)


# ── _verifyYear ──────────────────────────────────────────────────────────

class Test_verifyYear:
    def test_current_year_is_valid(self):
        b = make_bulletin()
        current = time.strftime('%Y', time.localtime())
        assert b._verifyYear(current) is True

    def test_previous_year_is_valid(self):
        b = make_bulletin()
        prev = str(int(time.strftime('%Y', time.localtime())) - 1)
        assert b._verifyYear(prev) is True

    def test_year_wrong_length_is_invalid(self):
        b = make_bulletin()
        assert b._verifyYear('20') is False

    def test_year_not_starting_with_2_is_invalid(self):
        b = make_bulletin()
        assert b._verifyYear('1999') is False

    def test_future_year_starting_with_2_is_valid(self):
        b = make_bulletin()
        # A 4-digit year starting with '2' that isn't current/previous is still True
        assert b._verifyYear('2099') is True


# ── verifyHeader ─────────────────────────────────────────────────────────

class Test_verifyHeader:
    def test_empty_header_sets_problem(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'', 'ascii')
        assert header == b''
        assert isProblem is True

    def test_incomplete_header_less_than_3_fields(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO', 'ascii')
        # Less than 3 tokens → returns without setting isProblem
        assert isProblem is False

    def test_valid_header_3_fields(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011200', 'ascii')
        assert isProblem is False
        assert header == b'SXCN40 CWAO 011200'

    def test_header_truncates_ddhhmm_longer_than_6(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011200Z', 'ascii')
        assert isProblem is False
        assert header == b'SXCN40 CWAO 011200'

    def test_malformed_first_field_sets_problem(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'!!! CWAO 011200', 'ascii')
        assert isProblem is True

    def test_malformed_timestamp_day_out_of_range(self):
        b = make_bulletin()
        # Day 00 is invalid (must be > 0)
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 001200', 'ascii')
        assert isProblem is True

    def test_malformed_timestamp_hour_out_of_range(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 012500', 'ascii')
        assert isProblem is True

    def test_malformed_timestamp_minute_out_of_range(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011261', 'ascii')
        assert isProblem is True

    def test_valid_bbb_field_preserved(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011200 CCA', 'ascii')
        assert isProblem is False
        assert header == b'SXCN40 CWAO 011200 CCA'

    def test_invalid_bbb_field_removed(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011200 ZZZ', 'ascii')
        assert isProblem is False
        assert header == b'SXCN40 CWAO 011200'

    def test_extra_fields_beyond_5_removed(self):
        b = make_bulletin()
        header, isProblem = b.verifyHeader(b'SXCN40 CWAO 011200 CCA AAB extra1 extra2', 'ascii')
        assert isProblem is False
        tokens = header.split(b' ')
        assert len(tokens) <= 5

    def test_duplicate_spaces_handled(self):
        b = make_bulletin()
        # Double spaces produce empty tokens that cause validation to see corruption
        header, isProblem = b.verifyHeader(b'SXCN40  CWAO  011200', 'ascii')
        # The current implementation joins tokens with single space but doesn't
        # filter empty strings, so validation fails on the empty tokens
        assert isProblem is True


# ── getData ──────────────────────────────────────────────────────────────

class Test_getData:
    def test_get_data_from_message_content(self):
        b = make_bulletin()
        msg = {'content': {'value': 'SXCN40 CWAO 011200\ndata line', 'encoding': 'text'}}
        result = b.getData(msg, '/fake/path')
        assert result == 'SXCN40 CWAO 011200\ndata line'

    def test_get_data_from_base64_content(self):
        b = make_bulletin()
        import base64
        raw = b'SXCN40 CWAO 011200\nsome binary data'
        encoded = base64.b64encode(raw).decode('ascii')
        msg = {'content': {'value': encoded, 'encoding': 'base64'}}
        result = b.getData(msg, '/fake/path')
        assert result == 'SXCN40 CWAO 011200'
        assert b.binary == 1

    def test_get_data_returns_none_on_error(self):
        b = make_bulletin()
        msg = {}  # no content
        result = b.getData(msg, '/nonexistent/path/file.txt')
        assert result is None


# ── getRandom ────────────────────────────────────────────────────────────

class Test_getRandom:
    def test_returns_5_digit_string(self):
        b = make_bulletin()
        r = b.getRandom()
        assert len(r) == 5
        assert r.isdigit()

    def test_returns_zero_padded(self):
        b = make_bulletin()
        with patch('random.randint', return_value=42):
            r = b.getRandom()
        assert r == '00042'


# ── getStation ───────────────────────────────────────────────────────────

class Test_getStation:
    def test_binary_bulletin_returns_empty(self):
        b = make_bulletin()
        b.binary = 1
        assert b.getStation('anything') == ''

    def test_sa_metar_single(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SA blah\nMETAR CYUL 011200Z rest'
        assert b.getStation(data) == 'CYUL'

    def test_sa_multiple_metar_returns_empty(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SA blah\nMETAR CYUL 011200Z rest\nMETAR CYOW 011200Z'
        assert b.getStation(data) == ''

    def test_sa_lwis_gets_second_token(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SA blah\nLWIS CYUL 011200Z rest'
        assert b.getStation(data) == 'CYUL'

    def test_sa_non_metar_gets_first_token(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SA blah\nCYUL 011200Z rest'
        assert b.getStation(data) == 'CYUL'

    def test_sp_gets_second_token(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SP blah\nfoo CYOW rest'
        assert b.getStation(data) == 'CYOW'

    def test_si_aaxx_gets_station_from_third_line(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SI blah\nAAXX\n71624 rest'
        assert b.getStation(data) == '71624'

    def test_si_non_aaxx(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SI blah\n71624 rest'
        assert b.getStation(data) == '71624'

    def test_fc_amd_gets_third_token(self):
        b = make_bulletin()
        b.binary = 0
        data = 'FC blah\nTAF AMD CYUL rest'
        assert b.getStation(data) == 'CYUL'

    def test_fc_non_amd_gets_second_token(self):
        b = make_bulletin()
        b.binary = 0
        data = 'FC blah\nTAF CYUL rest'
        assert b.getStation(data) == 'CYUL'

    def test_ra_station_before_slash(self):
        b = make_bulletin()
        b.binary = 0
        data = 'RA blah\nCYUL/data rest'
        assert b.getStation(data) == 'CYUL'

    def test_ue_ee_prefix_gets_second_part(self):
        b = make_bulletin()
        b.binary = 0
        data = 'UE blah\nEExx CYUL rest'
        assert b.getStation(data) == 'CYUL'

    def test_ue_pp_prefix_gets_third_part(self):
        b = make_bulletin()
        b.binary = 0
        data = 'UE blah\nPPxx foo CYUL rest'
        assert b.getStation(data) == 'CYUL'

    def test_srcn40_returns_empty(self):
        b = make_bulletin()
        b.binary = 0
        data = 'SRCN40 CWAO 011200\ndata'
        assert b.getStation(data) == ''

    def test_station_with_leading_question_marks_stripped(self):
        b = make_bulletin()
        b.binary = 0
        data = 'RA blah\n??CYUL rest'
        assert b.getStation(data) == 'CYUL'

    def test_station_with_trailing_equals_stripped(self):
        b = make_bulletin()
        b.binary = 0
        data = 'RA blah\nCYUL= rest'
        assert b.getStation(data) == 'CYUL'

    def test_station_too_long_returns_empty(self):
        b = make_bulletin()
        b.binary = 0
        data = 'RA blah\nTOOLONGSTATION rest'
        assert b.getStation(data) == ''

    def test_station_too_short_returns_empty(self):
        b = make_bulletin()
        b.binary = 0
        data = 'RA blah\nAB rest'
        assert b.getStation(data) == ''

    def test_exception_returns_empty(self):
        b = make_bulletin()
        b.binary = 0
        # Data that causes an index error in the parsing
        assert b.getStation('') == ''


# ── getBBB ───────────────────────────────────────────────────────────────

class Test_getBBB:
    def test_no_bbb_when_line_not_4_parts(self):
        b = make_bulletin()
        assert b.getBBB(['T1T2', 'CCCC', 'DDHHmm']) == ''

    def test_bbb_extracted_from_fourth_part(self):
        b = make_bulletin()
        assert b.getBBB(['T1T2', 'CCCC', 'DDHHmm', 'CCA']) == 'CCA'


# ── buildHeader ──────────────────────────────────────────────────────────

class Test_buildHeader:
    def test_header_from_3_fields(self):
        b = make_bulletin()
        result = b.buildHeader(['SXCN40', 'CWAO', '011200'])
        assert result == 'SXCN40_CWAO_011200'

    def test_header_from_2_fields(self):
        b = make_bulletin()
        result = b.buildHeader(['SXCN40', 'CWAO'])
        assert result == 'SXCN40_CWAO'

    def test_header_from_empty_returns_none(self):
        b = make_bulletin()
        result = b.buildHeader([])
        assert result is None


# ── getTime ──────────────────────────────────────────────────────────────

class Test_getTime:
    def test_valid_time_extraction(self):
        b = make_bulletin()
        current_year = time.strftime('%Y', time.localtime())
        data = f'CA,{current_year},001,1230,rest'
        result = b.getTime(data)
        assert result == '011230'

    def test_2400_wraps_to_next_day(self):
        b = make_bulletin()
        current_year = time.strftime('%Y', time.localtime())
        data = f'CA,{current_year},001,2400,rest'
        result = b.getTime(data)
        # Day 001 + 24 hours = Day 002, hour 00:00 → '020000'
        assert result == '020000'

    def test_short_hhmm_gets_zero_padded(self):
        b = make_bulletin()
        current_year = time.strftime('%Y', time.localtime())
        data = f'CA,{current_year},001,30,rest'
        result = b.getTime(data)
        assert result == '010030'

    def test_short_julian_day_gets_zero_padded(self):
        b = make_bulletin()
        current_year = time.strftime('%Y', time.localtime())
        data = f'CA,{current_year},1,1230,rest'
        result = b.getTime(data)
        assert result == '011230'

    def test_float_julian_day_returns_none(self):
        b = make_bulletin()
        current_year = time.strftime('%Y', time.localtime())
        data = f'CA,{current_year},1.5,1230,rest'
        result = b.getTime(data)
        assert result is None

    def test_invalid_year_for_ca_returns_none(self):
        b = make_bulletin()
        data = 'CA,19,001,1230,rest'
        result = b.getTime(data)
        assert result is None

    def test_non_ca_bulletin_skips_year_verify(self):
        b = make_bulletin()
        # Non-CA type shouldn't verify the year
        data = 'XX,2099,001,1230,rest'
        result = b.getTime(data)
        assert result == '011230'

    def test_too_few_parts_returns_none(self):
        b = make_bulletin()
        result = b.getTime('CA,2024')
        assert result is None

    def test_completely_invalid_data_returns_none(self):
        b = make_bulletin()
        result = b.getTime('not,a,valid,time,at,all')
        assert result is None