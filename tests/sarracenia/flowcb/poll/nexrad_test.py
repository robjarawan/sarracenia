import pytest
import datetime
from unittest.mock import patch, MagicMock, call

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.nexrad import Nexrad

# ---------------------------------------------------------------------------
# A valid US weather station line (81+ chars, ends "US\n", 'X' at pos 65,
# ICAO at positions 20-24).
# ---------------------------------------------------------------------------
# Valid US station: 87 chars, ICAO "KORD" at [20:24], 'X' at [65], ends "US\n"
STATION_LINE = (
    b"IL   CHICAGO/OHARE  KORD                                         X     T          7 US\n"
)
# Long enough but ends "CA\n" (not US) → should be skipped
NON_US_LINE = (
    b"ON   TORONTO/PEARSON CYYZ                                         X     T          7 CA\n"
)
# A short line that should always be ignored
SHORT_LINE = b"SHORT LINE\n"
# Valid-length US line but blanks at ICAO positions 20-24 → skipped
BLANK_ICAO_LINE = (
    b"IL   CHICAGO/OHARE                                               X     T          7 US\n"
)
# Valid-length US line but NO 'X' at position 65 → skipped
NO_X_LINE = (
    b"IL   CHICAGO/OHARE  KORD                                               T          7 US\n"
)

# Second valid US station with ICAO "KIAH" at [20:24]
STATION_LINE_2 = (
    b"TX   HOUSTON/INTERC KIAH                                         X     T          7 US\n"
)


# ---------------------------------------------------------------------------
# Helper: build a Nexrad instance with sensible defaults
# ---------------------------------------------------------------------------
def _make_nexrad(**overrides):
    options = sarracenia.config.default_config()
    options.pollUrl = "https://noaa-nexrad-level2.s3.amazonaws.com/"
    options.post_baseUrl = "https://noaa-nexrad-level2.s3.amazonaws.com/"
    options.publishers = [
        {"baseUrl": "https://noaa-nexrad-level2.s3.amazonaws.com/", "baseDir": None}
    ]
    options.poll_nexrad_day = ""  # empty → minute mode
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch("sarracenia.flowcb.FlowCB.__init__", return_value=None):
        inst = Nexrad.__new__(Nexrad)
        inst.o = options
        inst.stop_requested = False
        inst.metrics = {
            "transferRxBytes": 0,
            "transferRxFiles": 0,
            "transferTxBytes": 0,
            "transferTxFiles": 0,
        }
        Nexrad.__init__(inst, options)
    return inst


# ---------------------------------------------------------------------------
# Helpers for mocking external boundaries
# ---------------------------------------------------------------------------
def _mock_urlopen(lines):
    """Return a context-manager mock whose readlines() returns *lines*."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.readlines.return_value = lines
    return ctx


def _mock_s3(objects_by_prefix=None):
    """Return a mock boto3 S3 client.

    *objects_by_prefix* maps a prefix string → list of
    ``{'Key': ..., 'Size': ...}`` dicts.  Any prefix not present raises
    ``KeyError`` (simulating 'Contents' missing from the response).
    """
    client = MagicMock()
    if objects_by_prefix is None:
        objects_by_prefix = {}

    def _list_objects(Bucket, Prefix):
        if Prefix in objects_by_prefix:
            return {"Contents": objects_by_prefix[Prefix]}
        # Simulate missing 'Contents' key by returning a dict without it
        return {}

    client.list_objects.side_effect = _list_objects
    return client


# ===================================================================
# Test_Nexrad_init
# ===================================================================
class Test_Nexrad_init:
    def test_init_sets_minutetracker(self):
        inst = _make_nexrad()
        assert hasattr(inst, "minutetracker")
        assert isinstance(inst.minutetracker, datetime.datetime)

    def test_init_registers_poll_nexrad_day_option(self):
        inst = _make_nexrad()
        # add_option should have set the attribute on options
        assert hasattr(inst.o, "poll_nexrad_day")

    def test_init_minutetracker_is_past(self):
        before = datetime.datetime.utcnow() + datetime.timedelta(minutes=-8)
        inst = _make_nexrad()
        after = datetime.datetime.utcnow() + datetime.timedelta(minutes=-6)
        # minutetracker should be roughly utcnow() − 7 min
        assert before <= inst.minutetracker <= after


# ===================================================================
# Test_Nexrad_poll_day_mode
# ===================================================================
class Test_Nexrad_poll_day_mode:
    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_day_mode_happy_path(self, mock_urlopen_fn, mock_boto_client):
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")

        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE])
        s3 = _mock_s3({
            "2023/06/15/KORD/": [
                {"Key": "2023/06/15/KORD/KORD20230615_000228_V06", "Size": 12345},
            ]
        })
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        assert len(msgs) >= 1
        # The hardcoded ICAOs that have no data won't produce messages,
        # but KORD (parsed from the station line) should.
        keys_found = [m["new_file"] for m in msgs]
        assert any("KORD" in k for k in keys_found)

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_day_mode_no_contents(self, mock_urlopen_fn, mock_boto_client):
        """s3.list_objects raises KeyError (no 'Contents') → skip gracefully."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE])
        # Empty dict for every prefix → KeyError on ['Contents']
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        assert msgs == []

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_day_mode_empty_stations_file(self, mock_urlopen_fn, mock_boto_client):
        """urlopen returns no matching lines → only hardcoded ICAOs queried."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        mock_urlopen_fn.return_value = _mock_urlopen([SHORT_LINE])
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        assert msgs == []
        # S3 still called for the hardcoded ICAOs
        assert s3.list_objects.call_count > 0

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_day_mode_multiple_stations(self, mock_urlopen_fn, mock_boto_client):
        """Multiple ICAOs have data → all gathered."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE, STATION_LINE_2])
        s3 = _mock_s3({
            "2023/06/15/KORD/": [
                {"Key": "2023/06/15/KORD/KORD20230615_000228_V06", "Size": 1111},
            ],
            "2023/06/15/KIAH/": [
                {"Key": "2023/06/15/KIAH/KIAH20230615_000300_V06", "Size": 2222},
            ],
        })
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        keys = [m["new_file"] for m in msgs]
        assert any("KORD" in k for k in keys)
        assert any("KIAH" in k for k in keys)


# ===================================================================
# Test_Nexrad_poll_minute_mode
# ===================================================================
class Test_Nexrad_poll_minute_mode:
    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_minute_mode_happy_path(self, mock_urlopen_fn, mock_boto_client):
        inst = _make_nexrad(poll_nexrad_day="")
        tracker_before = inst.minutetracker

        # Pre-compute what the tracker will be after +1 min
        expected_tracker = tracker_before + datetime.timedelta(minutes=1)
        YYYY = str(expected_tracker.year)
        MM = str(expected_tracker.month).zfill(2)
        DD = str(expected_tracker.day).zfill(2)
        HH = str(expected_tracker.hour).zfill(2)
        mm = str(expected_tracker.minute).zfill(2)

        # Build a prefix that the code will query for KORD
        prefix = f"{YYYY}/{MM}/{DD}/KORD/KORD{YYYY}{MM}{DD}_{HH}{mm}"
        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE])
        s3 = _mock_s3({
            prefix: [
                {"Key": f"{YYYY}/{MM}/{DD}/KORD/KORD{YYYY}{MM}{DD}_{HH}{mm}01_V06", "Size": 5555},
            ],
        })
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        assert len(msgs) >= 1
        # minutetracker advanced by 1 minute
        assert inst.minutetracker == expected_tracker

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_minute_mode_minutetracker_advances(self, mock_urlopen_fn, mock_boto_client):
        """Calling poll() twice advances minutetracker by 2 minutes total."""
        inst = _make_nexrad(poll_nexrad_day="")
        original = inst.minutetracker

        mock_urlopen_fn.return_value = _mock_urlopen([])
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        inst.poll()
        inst.poll()
        assert inst.minutetracker == original + datetime.timedelta(minutes=2)

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_minute_mode_no_contents(self, mock_urlopen_fn, mock_boto_client):
        """S3 returns no Contents → gracefully returns empty list."""
        inst = _make_nexrad(poll_nexrad_day="")
        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE])
        s3 = _mock_s3()  # all prefixes → KeyError
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        assert msgs == []

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_minute_mode_s3_called_with_correct_prefix(self, mock_urlopen_fn, mock_boto_client):
        """Verify prefix includes station + date + time."""
        inst = _make_nexrad(poll_nexrad_day="")
        tracker_after = inst.minutetracker + datetime.timedelta(minutes=1)
        YYYY = str(tracker_after.year)
        MM = str(tracker_after.month).zfill(2)
        DD = str(tracker_after.day).zfill(2)
        HH = str(tracker_after.hour).zfill(2)
        mm_str = str(tracker_after.minute).zfill(2)

        # Only provide one station line so we can check the prefix
        mock_urlopen_fn.return_value = _mock_urlopen([])
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        inst.poll()

        # Check calls for hardcoded ICAOs – pick one we know is always there
        all_calls = s3.list_objects.call_args_list
        prefixes_used = [c.kwargs["Prefix"] for c in all_calls]
        # e.g. "2023/07/01/KGRK/KGRK20230701_1430"
        kgrk_prefixes = [p for p in prefixes_used if "KGRK" in p]
        assert len(kgrk_prefixes) == 1
        expected = (
            f"{YYYY}/{MM}/{DD}/KGRK/KGRK{YYYY}{MM}{DD}_{HH}{mm_str}"
        )
        assert kgrk_prefixes[0] == expected


# ===================================================================
# Test_Nexrad_poll_edge
# ===================================================================
class Test_Nexrad_poll_edge:
    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_always_returns_list(self, mock_urlopen_fn, mock_boto_client):
        inst = _make_nexrad(poll_nexrad_day="")
        mock_urlopen_fn.return_value = _mock_urlopen([])
        mock_boto_client.return_value = _mock_s3()
        result = inst.poll()
        assert isinstance(result, list)

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_message_has_size_from_s3(self, mock_urlopen_fn, mock_boto_client):
        """st_size on the generated message comes from obj['Size']."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        mock_urlopen_fn.return_value = _mock_urlopen([STATION_LINE])
        s3 = _mock_s3({
            "2023/06/15/KORD/": [
                {"Key": "2023/06/15/KORD/KORD20230615_000228_V06", "Size": 99999},
            ],
        })
        mock_boto_client.return_value = s3

        msgs = inst.poll()
        kord_msgs = [m for m in msgs if "KORD" in m.get("new_file", "")]
        assert len(kord_msgs) >= 1
        assert kord_msgs[0]["size"] == 99999

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_metrics_updated(self, mock_urlopen_fn, mock_boto_client):
        """transferRxBytes is updated from the number of lines read."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        lines = [STATION_LINE, SHORT_LINE, NON_US_LINE]
        mock_urlopen_fn.return_value = _mock_urlopen(lines)
        mock_boto_client.return_value = _mock_s3()

        inst.poll()
        assert inst.metrics["transferRxBytes"] == len(lines)

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_hardcoded_ICAOs_added(self, mock_urlopen_fn, mock_boto_client):
        """RKJK, PAEC, RODN, RKSG, KGRK, FOP1, NOP4 always queried."""
        inst = _make_nexrad(poll_nexrad_day="2023-01-01")
        mock_urlopen_fn.return_value = _mock_urlopen([])  # no parsed stations
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        inst.poll()
        all_prefixes = [c.kwargs["Prefix"] for c in s3.list_objects.call_args_list]
        for icao in ["RKJK", "PAEC", "RODN", "RKSG", "KGRK", "FOP1", "NOP4"]:
            assert any(icao in p for p in all_prefixes), f"{icao} not queried"

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_station_parsing_from_lines(self, mock_urlopen_fn, mock_boto_client):
        """Only lines matching all criteria produce parsed ICAOs."""
        inst = _make_nexrad(poll_nexrad_day="2023-01-01")
        lines = [
            STATION_LINE,       # valid → KORD
            STATION_LINE_2,     # valid → KIAH
            NON_US_LINE,        # not US → skip
            SHORT_LINE,         # too short → skip
            BLANK_ICAO_LINE,    # blank ICAO → skip
            NO_X_LINE,          # no X at 65 → skip
        ]
        mock_urlopen_fn.return_value = _mock_urlopen(lines)
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        inst.poll()
        all_prefixes = [c.kwargs["Prefix"] for c in s3.list_objects.call_args_list]
        # KORD and KIAH should appear (from parsed lines)
        assert any("KORD" in p for p in all_prefixes)
        assert any("KIAH" in p for p in all_prefixes)
        # CYYZ should NOT appear (Canadian station)
        assert not any("CYYZ" in p for p in all_prefixes)

    @patch("sarracenia.flowcb.poll.nexrad.boto3.client")
    @patch("sarracenia.flowcb.poll.nexrad.urllib.request.urlopen")
    def test_day_mode_prefix_format(self, mock_urlopen_fn, mock_boto_client):
        """Day mode prefix ends with station + '/'."""
        inst = _make_nexrad(poll_nexrad_day="2023-06-15")
        mock_urlopen_fn.return_value = _mock_urlopen([])
        s3 = _mock_s3()
        mock_boto_client.return_value = s3

        inst.poll()
        all_prefixes = [c.kwargs["Prefix"] for c in s3.list_objects.call_args_list]
        kgrk = [p for p in all_prefixes if "KGRK" in p]
        assert kgrk[0] == "2023/06/15/KGRK/"