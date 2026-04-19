import pytest
import types
import re
from unittest.mock import patch, MagicMock
from tests.conftest import *

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.usgs import Usgs


POLL_URL = 'http://waterservices.usgs.gov/nwis/iv/?format=waterml,2.0&site={0:}&period=PT3H'

SINGLE_STATION = '7|70026|9014087|Dry Dock, MI|US|MI|-5.0'

MULTI_STATIONS = [
    '7|70026|9014087|Dry Dock, MI|US|MI|-5.0',
    '7|70614|9440083|Vancouver|US|WA|-8.0',
    '7|70555|8461490|New London, CT|US|CT|-5.0',
]


def _mock_urlopen(status_code=200):
    """Return a mock for urlopen that returns the given status code."""
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = status_code
    return mock_resp


def _make_usgs(**overrides):
    """Helper to create a Usgs instance with mocked parent init."""
    options = sarracenia.config.default_config()
    options.pollUrl = POLL_URL
    options.post_baseUrl = 'http://waterservices.usgs.gov/'
    options.publishers = [{'baseUrl': 'http://waterservices.usgs.gov/', 'baseDir': None}]
    options.poll_usgs_station = [SINGLE_STATION]
    options.batch = 1
    options.msg = types.SimpleNamespace(new_baseurl='')
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = Usgs.__new__(Usgs)
        inst.o = options
        inst.stop_requested = False
        Usgs.__init__(inst, options)
    return inst


# -------------------------------------------------------------------
# Initialization tests
# -------------------------------------------------------------------

class Test_Usgs_init:

    def test_init_parses_station_pipe_delimited(self):
        """Station string is parsed by pipe delimiter, extracting sitecode at index 2."""
        inst = _make_usgs()
        assert inst.sitecodes == ['9014087']

    def test_init_multiple_stations(self):
        """Multiple station declarations produce matching sitecodes list."""
        inst = _make_usgs(poll_usgs_station=MULTI_STATIONS)
        assert len(inst.sitecodes) == 3

    def test_init_extracts_sitecode_at_index_2(self):
        """Sitecode is always the third pipe-delimited field (index 2)."""
        inst = _make_usgs(poll_usgs_station=MULTI_STATIONS)
        assert inst.sitecodes == ['9014087', '9440083', '8461490']

    def test_init_registers_poll_usgs_station_option(self):
        """add_option is called for poll_usgs_station during init."""
        inst = _make_usgs()
        assert hasattr(inst.o, 'poll_usgs_station')

    def test_init_sitecodes_is_list(self):
        """sitecodes is always a list after init."""
        inst = _make_usgs()
        assert isinstance(inst.sitecodes, list)


# -------------------------------------------------------------------
# poll – single station mode (batch <= 1)
# -------------------------------------------------------------------

class Test_Usgs_poll_single:

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_single_station_200_returns_message(self, mock_urlopen):
        """A single station with 200 response returns one message."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1)
        msgs = inst.poll()
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_single_station_403_returns_empty(self, mock_urlopen):
        """403 blocked response returns no messages."""
        mock_urlopen.return_value = _mock_urlopen(403)
        inst = _make_usgs(batch=1)
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_single_station_404_returns_empty(self, mock_urlopen):
        """Non-200/non-403 response returns no messages."""
        mock_urlopen.return_value = _mock_urlopen(404)
        inst = _make_usgs(batch=1)
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_single_multiple_stations_200_all(self, mock_urlopen):
        """Multiple stations all returning 200 produce one message per station."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        assert len(msgs) == 3


# -------------------------------------------------------------------
# poll – batch mode (batch > 1)
# -------------------------------------------------------------------

class Test_Usgs_poll_batch:

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_mode_200_returns_messages(self, mock_urlopen):
        """Batch mode with 200 returns messages (one per batch chunk)."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=5, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        # 3 stations with batch=5 -> 1 chunk -> 1 message
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_mode_groups_stations_correctly(self, mock_urlopen):
        """Batch mode with batch=2 and 3 stations produces 2 chunks."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=2, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        # 3 stations with batch=2 -> [2, 1] -> 2 messages
        assert len(msgs) == 2

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_mode_403_returns_empty(self, mock_urlopen):
        """Batch mode with 403 returns empty list."""
        mock_urlopen.return_value = _mock_urlopen(403)
        inst = _make_usgs(batch=5, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        assert len(msgs) == 0

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_mode_partial_success(self, mock_urlopen):
        """Batch mode: first chunk 200, second 403 produces only one message."""
        responses = [_mock_urlopen(200), _mock_urlopen(403)]
        mock_urlopen.side_effect = responses
        inst = _make_usgs(batch=2, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        assert len(msgs) == 1


# -------------------------------------------------------------------
# poll – edge cases
# -------------------------------------------------------------------

class Test_Usgs_poll_edge:

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_empty_sitecodes_returns_empty(self, mock_urlopen):
        """No sitecodes in single mode returns empty list without calling urlopen."""
        inst = _make_usgs(batch=1)
        inst.sitecodes = []
        msgs = inst.poll()
        assert msgs == []
        mock_urlopen.assert_not_called()

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_empty_sitecodes_batch_returns_empty(self, mock_urlopen):
        """No sitecodes in batch mode returns empty list without calling urlopen."""
        inst = _make_usgs(batch=5)
        inst.sitecodes = []
        msgs = inst.poll()
        assert msgs == []
        mock_urlopen.assert_not_called()

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_poll_returns_list_always(self, mock_urlopen):
        """poll always returns a list regardless of status code."""
        mock_urlopen.return_value = _mock_urlopen(500)
        inst = _make_usgs(batch=1)
        result = inst.poll()
        assert isinstance(result, list)

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_message_filename_contains_runtime_and_site(self, mock_urlopen):
        """Single mode message filename contains runtime timestamp and site code."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1, poll_usgs_station=[SINGLE_STATION])
        msgs = inst.poll()
        fname = msgs[0]['new_file']
        assert fname.startswith('usgs_')
        assert fname.endswith('_9014087.xml')
        # Verify timestamp format YYYYMMDD_HHMM in filename
        assert re.match(r'usgs_\d{8}_\d{4}_9014087\.xml', fname)

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_urlopen_called_with_formatted_url(self, mock_urlopen):
        """urlopen receives the pollUrl formatted with the site code."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1, poll_usgs_station=[SINGLE_STATION])
        inst.poll()
        called_url = mock_urlopen.call_args[0][0]
        assert '9014087' in called_url
        assert called_url == POLL_URL.format('9014087')

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_filename_contains_sites_counter(self, mock_urlopen):
        """Batch mode message filename contains 'sites' and a counter."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=5, poll_usgs_station=MULTI_STATIONS)
        msgs = inst.poll()
        fname = msgs[0]['new_file']
        assert fname.startswith('usgs_')
        assert 'sites1' in fname
        assert fname.endswith('.xml')

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_urlopen_joins_sites_with_comma(self, mock_urlopen):
        """Batch mode joins site codes with comma in URL."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=5, poll_usgs_station=MULTI_STATIONS)
        inst.poll()
        called_url = mock_urlopen.call_args[0][0]
        assert '9014087,9440083,8461490' in called_url

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_msg_new_baseurl_set_on_200(self, mock_urlopen):
        """On 200 response, self.o.msg.new_baseurl is set to the formatted URL."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1, poll_usgs_station=[SINGLE_STATION])
        inst.poll()
        expected_url = POLL_URL.format('9014087')
        assert inst.o.msg.new_baseurl == expected_url

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_single_station_urlopen_call_count(self, mock_urlopen):
        """Single mode calls urlopen once per station."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=1, poll_usgs_station=MULTI_STATIONS)
        inst.poll()
        assert mock_urlopen.call_count == 3

    @patch('sarracenia.flowcb.poll.usgs.urllib.request.urlopen')
    def test_batch_mode_urlopen_call_count(self, mock_urlopen):
        """Batch mode calls urlopen once per chunk, not per station."""
        mock_urlopen.return_value = _mock_urlopen(200)
        inst = _make_usgs(batch=2, poll_usgs_station=MULTI_STATIONS)
        inst.poll()
        # 3 stations with batch=2 -> 2 chunks -> 2 urlopen calls
        assert mock_urlopen.call_count == 2