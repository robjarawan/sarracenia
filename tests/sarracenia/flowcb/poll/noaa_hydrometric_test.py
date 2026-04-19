import pytest
import datetime
from unittest.mock import patch, mock_open, MagicMock, PropertyMock
from tests.conftest import *

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.noaa_hydrometric import Noaa_hydrometric


STATION_FILE_CONTENT = (
    "7|70678|9751639|Charlotte Amalie|US|VI|-4.0\n"
    "7|70614|9440083|Vancouver|US|WA|-8.0\n"
)

SINGLE_STATION = "7|70678|9751639|Charlotte Amalie|US|VI|-4.0\n"


def _make_noaa(**overrides):
    """Helper to create a Noaa_hydrometric instance with mocked parent init."""
    options = sarracenia.config.default_config()
    options.pollUrl = 'https://tidesandcurrents.noaa.gov/api/'
    options.post_baseUrl = 'https://tidesandcurrents.noaa.gov/'
    options.identity_method = 'cod,s'
    options.retrievePathPattern = (
        'datagetter?range=1&station={0:}&product={1:}'
        '&units=metric&time_zone=gmt&application=web_services&format=csv'
    )
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Noaa_hydrometric.__new__(Noaa_hydrometric)
        inst.o = options
        inst.stop_requested = False
        inst.identity = {'method': 'cod', 'value': 's'}
        Noaa_hydrometric.__init__(inst, options)
    return inst


def _mock_urlopen_200():
    """Return a mock for urlopen that always returns status 200."""
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    return mock_resp


# -------------------------------------------------------------------
# Initialization tests
# -------------------------------------------------------------------

class Test_init:

    def test_init_sets_identity_for_cod(self):
        """When identity_method='cod,s', identity dict is populated."""
        inst = _make_noaa()
        assert inst.identity == {'method': 'cod', 'value': 's'}

    def test_init_identity_cod_other_value(self):
        """When identity_method='cod,md5', value part should be 'md5'."""
        inst = _make_noaa(identity_method='cod,md5')
        assert inst.identity == {'method': 'cod', 'value': 'md5'}

    def test_init_non_cod_identity_no_identity_attr(self):
        """When identity_method doesn't start with 'cod,', identity is not set by __init__."""
        with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
            options = sarracenia.config.default_config()
            options.pollUrl = 'https://tidesandcurrents.noaa.gov/api/'
            options.post_baseUrl = 'https://tidesandcurrents.noaa.gov/'
            options.identity_method = 'sha512'
            options.retrievePathPattern = 'datagetter?range=1&station={0:}&product={1:}'
            inst = Noaa_hydrometric.__new__(Noaa_hydrometric)
            inst.o = options
            inst.stop_requested = False
            # no pre-set identity
            Noaa_hydrometric.__init__(inst, options)
        assert not hasattr(inst, 'identity')

    def test_init_registers_poll_noaa_stn_file_option(self):
        """add_option is called for poll_noaa_stn_file during init."""
        inst = _make_noaa()
        # After init, the option should be registered (as None by default)
        assert hasattr(inst.o, 'poll_noaa_stn_file')

    def test_init_registers_retrievePathPattern_option(self):
        """add_option is called for retrievePathPattern during init."""
        inst = _make_noaa()
        assert hasattr(inst.o, 'retrievePathPattern')


# -------------------------------------------------------------------
# poll – station file path
# -------------------------------------------------------------------

class Test_poll_with_station_file:

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=STATION_FILE_CONTENT)
    def test_poll_reads_station_file(self, mock_file, mock_urlopen):
        """poll opens the station file specified in poll_noaa_stn_file."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        inst.poll()
        mock_file.assert_called_once_with('/path/to/stations.txt')

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=STATION_FILE_CONTENT)
    def test_poll_parses_pipe_delimited_station_codes(self, mock_file, mock_urlopen):
        """Station codes (3rd column) are extracted from pipe-delimited lines."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        # 2 stations × 2 products = 4 messages
        assert len(msgs) == 4

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_poll_single_station_two_messages(self, mock_file, mock_urlopen):
        """One station should produce exactly 2 messages (water_temp + water_level)."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        assert len(msgs) == 2

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=STATION_FILE_CONTENT)
    def test_poll_multiple_stations_message_count(self, mock_file, mock_urlopen):
        """Two stations should produce 4 messages."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        assert len(msgs) == 4


# -------------------------------------------------------------------
# poll – IOError on station file
# -------------------------------------------------------------------

class Test_poll_station_file_ioerror:

    @patch('builtins.open', side_effect=IOError("file not found"))
    def test_poll_ioerror_returns_empty_list(self, mock_file):
        """When station file can't be opened, poll returns an empty list."""
        inst = _make_noaa(poll_noaa_stn_file='/nonexistent/file.txt')
        msgs = inst.poll()
        assert msgs == []

    @patch('builtins.open', side_effect=IOError("file not found"))
    def test_poll_ioerror_does_not_raise(self, mock_file):
        """IOError is caught gracefully; no exception propagates."""
        inst = _make_noaa(poll_noaa_stn_file='/nonexistent/file.txt')
        try:
            inst.poll()
        except IOError:
            pytest.fail("poll should not propagate IOError")


# -------------------------------------------------------------------
# poll – empty station list
# -------------------------------------------------------------------

class Test_poll_empty:

    @patch('builtins.open', new_callable=mock_open, read_data='')
    def test_poll_empty_station_file_returns_empty(self, mock_file):
        """An empty station file means no sitecodes, hence empty message list."""
        inst = _make_noaa(poll_noaa_stn_file='/path/to/empty.txt')
        msgs = inst.poll()
        assert msgs == []


# -------------------------------------------------------------------
# poll – message construction details
# -------------------------------------------------------------------

class Test_poll_message_fields:

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_water_temp_fname_pattern(self, mock_file, mock_urlopen):
        """Water temperature message fname has format noaa_YYYYMMDD_HHMM_<site>_WT.csv."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        wt_msg = msgs[0]
        fname = wt_msg['new_file']
        assert fname.startswith('noaa_')
        assert fname.endswith('_9751639_WT.csv')

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_water_level_fname_pattern(self, mock_file, mock_urlopen):
        """Water level message fname has format noaa_YYYYMMDD_HHMM_<site>_WL.csv."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        wl_msg = msgs[1]
        fname = wl_msg['new_file']
        assert fname.startswith('noaa_')
        assert fname.endswith('_9751639_WL.csv')

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_identity_set_on_messages(self, mock_file, mock_urlopen):
        """Each message carries the identity dict from __init__."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        for m in msgs:
            assert m['identity'] == {'method': 'cod', 'value': 's'}

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_water_temp_retrievePath(self, mock_file, mock_urlopen):
        """Water temp retrievePath uses product=water_temperature."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        rp = msgs[0]['retrievePath']
        assert 'product=water_temperature' in rp
        assert 'station=9751639' in rp
        assert '&datum=STND' not in rp

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_water_level_retrievePath_has_datum(self, mock_file, mock_urlopen):
        """Water level retrievePath uses product=water_level and has &datum=STND."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        rp = msgs[1]['retrievePath']
        assert 'product=water_level' in rp
        assert 'station=9751639' in rp
        assert rp.endswith('&datum=STND')

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_urlopen_called_with_correct_urls(self, mock_file, mock_urlopen):
        """urlopen is called twice per station: water_temp then water_level."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        inst.poll()
        assert mock_urlopen.call_count == 2
        first_call_url = mock_urlopen.call_args_list[0][0][0]
        second_call_url = mock_urlopen.call_args_list[1][0][0]
        assert 'water_temperature' in first_call_url
        assert 'water_level' in second_call_url

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=STATION_FILE_CONTENT)
    def test_multiple_stations_alternating_wt_wl(self, mock_file, mock_urlopen):
        """Messages alternate WT/WL for each station in order."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        # Station 1: WT, WL; Station 2: WT, WL
        assert msgs[0]['new_file'].endswith('_9751639_WT.csv')
        assert msgs[1]['new_file'].endswith('_9751639_WL.csv')
        assert msgs[2]['new_file'].endswith('_9440083_WT.csv')
        assert msgs[3]['new_file'].endswith('_9440083_WL.csv')

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_messages_are_sarracenia_messages(self, mock_file, mock_urlopen):
        """poll returns proper sarracenia.Message instances."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        msgs = inst.poll()
        for m in msgs:
            assert isinstance(m, dict)
            assert 'new_file' in m
            assert 'retrievePath' in m
            assert 'identity' in m


# -------------------------------------------------------------------
# poll – without station file (XML from web)
# -------------------------------------------------------------------

class Test_poll_without_station_file:

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('sarracenia.flowcb.poll.noaa_hydrometric.ET.parse')
    def test_poll_fetches_xml_when_no_stn_file(self, mock_et_parse, mock_urlopen):
        """When poll_noaa_stn_file is not set, stations come from the XML endpoint."""
        mock_urlopen_resp = _mock_urlopen_200()
        mock_urlopen.return_value = mock_urlopen_resp

        mock_root = MagicMock()
        child1 = MagicMock()
        child1.attrib = {'ID': '1234567'}
        mock_root.__iter__ = MagicMock(return_value=iter([child1]))
        mock_tree = MagicMock()
        mock_tree.getroot.return_value = mock_root
        mock_et_parse.return_value = mock_tree

        with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
            options = sarracenia.config.default_config()
            options.pollUrl = 'https://tidesandcurrents.noaa.gov/api/'
            options.post_baseUrl = 'https://tidesandcurrents.noaa.gov/'
            options.identity_method = 'cod,s'
            options.retrievePathPattern = (
                'datagetter?range=1&station={0:}&product={1:}'
                '&units=metric&time_zone=gmt&application=web_services&format=csv'
            )
            inst = Noaa_hydrometric.__new__(Noaa_hydrometric)
            inst.o = options
            inst.stop_requested = False
            inst.identity = {'method': 'cod', 'value': 's'}
            Noaa_hydrometric.__init__(inst, options)

        # Remove poll_noaa_stn_file so the else branch triggers
        if hasattr(inst.o, 'poll_noaa_stn_file'):
            delattr(inst.o, 'poll_noaa_stn_file')

        msgs = inst.poll()
        mock_et_parse.assert_called_once()
        assert len(msgs) == 2
        assert '1234567' in msgs[0]['new_file']

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('sarracenia.flowcb.poll.noaa_hydrometric.ET.parse')
    def test_poll_xml_multiple_stations(self, mock_et_parse, mock_urlopen):
        """XML with multiple station elements produces 2 messages each."""
        mock_urlopen.return_value = _mock_urlopen_200()

        child1 = MagicMock()
        child1.attrib = {'ID': 'AAA'}
        child2 = MagicMock()
        child2.attrib = {'ID': 'BBB'}
        child3 = MagicMock()
        child3.attrib = {'ID': 'CCC'}
        mock_root = MagicMock()
        mock_root.__iter__ = MagicMock(return_value=iter([child1, child2, child3]))
        mock_tree = MagicMock()
        mock_tree.getroot.return_value = mock_root
        mock_et_parse.return_value = mock_tree

        with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
            options = sarracenia.config.default_config()
            options.pollUrl = 'https://tidesandcurrents.noaa.gov/api/'
            options.post_baseUrl = 'https://tidesandcurrents.noaa.gov/'
            options.identity_method = 'cod,s'
            options.retrievePathPattern = (
                'datagetter?range=1&station={0:}&product={1:}'
                '&units=metric&time_zone=gmt&application=web_services&format=csv'
            )
            inst = Noaa_hydrometric.__new__(Noaa_hydrometric)
            inst.o = options
            inst.stop_requested = False
            inst.identity = {'method': 'cod', 'value': 's'}
            Noaa_hydrometric.__init__(inst, options)

        if hasattr(inst.o, 'poll_noaa_stn_file'):
            delattr(inst.o, 'poll_noaa_stn_file')

        msgs = inst.poll()
        assert len(msgs) == 6  # 3 stations × 2

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('sarracenia.flowcb.poll.noaa_hydrometric.ET.parse')
    def test_poll_xml_empty_root(self, mock_et_parse, mock_urlopen):
        """XML with no station elements yields empty message list."""
        mock_root = MagicMock()
        mock_root.__iter__ = MagicMock(return_value=iter([]))
        mock_tree = MagicMock()
        mock_tree.getroot.return_value = mock_root
        mock_et_parse.return_value = mock_tree

        with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
            options = sarracenia.config.default_config()
            options.pollUrl = 'https://tidesandcurrents.noaa.gov/api/'
            options.post_baseUrl = 'https://tidesandcurrents.noaa.gov/'
            options.identity_method = 'cod,s'
            options.retrievePathPattern = 'datagetter?station={0:}&product={1:}'
            inst = Noaa_hydrometric.__new__(Noaa_hydrometric)
            inst.o = options
            inst.stop_requested = False
            inst.identity = {'method': 'cod', 'value': 's'}
            Noaa_hydrometric.__init__(inst, options)

        if hasattr(inst.o, 'poll_noaa_stn_file'):
            delattr(inst.o, 'poll_noaa_stn_file')

        msgs = inst.poll()
        assert msgs == []


# -------------------------------------------------------------------
# poll – retrievePathPattern formatting
# -------------------------------------------------------------------

class Test_poll_retrieve_path_formatting:

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_custom_retrievePathPattern(self, mock_file, mock_urlopen):
        """A custom retrievePathPattern is used for formatting retrievePath."""
        mock_urlopen.return_value = _mock_urlopen_200()
        custom_pattern = 'custom?stn={0:}&prod={1:}&extra=yes'
        inst = _make_noaa(
            poll_noaa_stn_file='/path/to/stations.txt',
            retrievePathPattern=custom_pattern,
        )
        msgs = inst.poll()
        assert msgs[0]['retrievePath'] == 'custom?stn=9751639&prod=water_temperature&extra=yes'
        assert msgs[1]['retrievePath'] == 'custom?stn=9751639&prod=water_level&extra=yes&datum=STND'

    @patch('sarracenia.flowcb.poll.noaa_hydrometric.urllib.request.urlopen')
    @patch('builtins.open', new_callable=mock_open, read_data=SINGLE_STATION)
    def test_pollUrl_prepended_to_retrievePath(self, mock_file, mock_urlopen):
        """The URL passed to urlopen is pollUrl + retrievePath."""
        mock_urlopen.return_value = _mock_urlopen_200()
        inst = _make_noaa(poll_noaa_stn_file='/path/to/stations.txt')
        inst.poll()
        first_url = mock_urlopen.call_args_list[0][0][0]
        assert first_url.startswith('https://tidesandcurrents.noaa.gov/api/')