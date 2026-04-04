"""
Tests for sarracenia.flowcb.poll.s3bucket (S3bucket poll plugin).

S3bucket is a FlowCB subclass that:
  - Parses pollUrl to extract service, region, bucket, prefix
  - Calls boto3 s3.list_objects to enumerate keys
  - Strips the prefix from each key and builds sarracenia Messages
  - Handles missing 'Contents' gracefully (KeyError)
  - Always returns a list of messages (possibly empty)

All external boundaries (boto3, paramiko) are mocked.
"""

import pytest
import datetime
from unittest.mock import patch, MagicMock

import sarracenia
import sarracenia.config
from sarracenia.flowcb.poll.s3bucket import S3bucket
from botocore import UNSIGNED


# ---------------------------------------------------------------------------
# Helper: build an S3bucket instance with mocked parent init
# ---------------------------------------------------------------------------
def _make_s3bucket(**overrides):
    options = sarracenia.config.default_config()
    # Standard pollUrl: https://s3-us-west-1.amazonaws.com//mybucket/some/prefix
    options.pollUrl = overrides.pop(
        'pollUrl',
        'https://s3-us-west-1.amazonaws.com//mybucket/some/prefix'
    )
    options.post_baseUrl = overrides.pop(
        'post_baseUrl',
        'https://s3-us-west-1.amazonaws.com/'
    )
    options.publishers = [
        {'baseUrl': 'https://s3-us-west-1.amazonaws.com/', 'baseDir': None}
    ]
    for k, v in overrides.items():
        setattr(options, k, v)
    with patch('sarracenia.flowcb.FlowCB.__init__', return_value=None):
        inst = S3bucket.__new__(S3bucket)
        inst.o = options
        inst.stop_requested = False
        inst.metrics = {
            'transferRxBytes': 0, 'transferRxFiles': 0,
            'transferTxBytes': 0, 'transferTxFiles': 0,
        }
        S3bucket.__init__(inst, options)
    return inst


# ---------------------------------------------------------------------------
# Helper: mock boto3 S3 client
# ---------------------------------------------------------------------------
def _mock_s3_client(objects=None):
    """Return a mock boto3 S3 client.

    *objects* is a list of {'Key': ..., 'Size': ...} dicts,
    or None to simulate missing 'Contents'.
    """
    client = MagicMock()
    if objects is not None:
        client.list_objects.return_value = {'Contents': objects}
    else:
        # No 'Contents' key -> accessing ['Contents'] raises KeyError
        client.list_objects.return_value = {}
    return client


# ===================================================================
# Test_S3bucket_init
# ===================================================================
class Test_S3bucket_init:

    def test_init_parses_service(self):
        inst = _make_s3bucket()
        assert inst.service == 's3'

    def test_init_parses_region(self):
        inst = _make_s3bucket()
        assert inst.region == 'us-west-1'

    def test_init_parses_bucket(self):
        inst = _make_s3bucket()
        assert inst.bucket == 'mybucket'

    def test_init_parses_prefix(self):
        inst = _make_s3bucket()
        assert inst.prefix == 'some/prefix'

    def test_init_sets_minutetracker(self):
        inst = _make_s3bucket()
        assert hasattr(inst, 'minutetracker')
        assert isinstance(inst.minutetracker, datetime.datetime)

    def test_init_minutetracker_is_past(self):
        before = datetime.datetime.utcnow() + datetime.timedelta(minutes=-75)
        inst = _make_s3bucket()
        after = datetime.datetime.utcnow() + datetime.timedelta(minutes=-65)
        assert before <= inst.minutetracker <= after

    def test_init_different_url(self):
        """Different pollUrl yields different region/bucket/prefix."""
        inst = _make_s3bucket(
            pollUrl='https://s3-eu-central-1.amazonaws.com//other-bucket/data/2024'
        )
        assert inst.region == 'eu-central-1'
        assert inst.bucket == 'other-bucket'
        assert inst.prefix == 'data/2024'

    def test_init_empty_prefix(self):
        """pollUrl with no sub-path after bucket -> empty prefix."""
        inst = _make_s3bucket(
            pollUrl='https://s3-us-east-1.amazonaws.com//mybucket/'
        )
        assert inst.bucket == 'mybucket'
        assert inst.prefix == ''


# ===================================================================
# Test_S3bucket_poll_happy
# ===================================================================
class Test_S3bucket_poll_happy:

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_single_object(self, mock_boto):
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/file1.nc', 'Size': 1024},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()

        assert isinstance(msgs, list)
        assert len(msgs) == 1

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_multiple_objects(self, mock_boto):
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/a.nc', 'Size': 100},
            {'Key': 'some/prefix/b.nc', 'Size': 200},
            {'Key': 'some/prefix/c.nc', 'Size': 300},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert len(msgs) == 3

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_size_propagation(self, mock_boto):
        """st_size from S3 objects propagates to message 'size'."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/big.dat', 'Size': 99999},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert len(msgs) == 1
        assert msgs[0]['size'] == 99999

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_prefix_stripped_from_key(self, mock_boto):
        """Key passed to fromFileInfo has prefix removed."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/sub/dir/file.nc', 'Size': 500},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert len(msgs) == 1
        # After stripping 'some/prefix', the relPath should contain sub/dir/file.nc
        new_file = msgs[0].get('new_file', '') + msgs[0].get('relPath', '')
        assert 'sub/dir/file.nc' in new_file

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_messages_are_dict(self, mock_boto):
        """Returned items are dict subclass (sarracenia.Message)."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/data.nc', 'Size': 42},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()
        for m in msgs:
            assert isinstance(m, dict)

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_messages_have_baseUrl(self, mock_boto):
        """Each message has 'baseUrl' key."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/data.nc', 'Size': 42},
        ])
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert len(msgs) > 0
        for m in msgs:
            assert 'baseUrl' in m


# ===================================================================
# Test_S3bucket_poll_empty
# ===================================================================
class Test_S3bucket_poll_empty:

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_no_contents_returns_empty(self, mock_boto):
        """Missing 'Contents' key -> KeyError handled -> empty list."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=None)
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert msgs == []

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_empty_contents_returns_empty(self, mock_boto):
        """Empty contents list -> no messages."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=[])
        mock_boto.return_value = s3

        msgs = inst.poll()
        assert msgs == []


# ===================================================================
# Test_S3bucket_poll_s3_params
# ===================================================================
class Test_S3bucket_poll_s3_params:

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_boto_client_uses_correct_service_and_region(self, mock_boto):
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=[])
        mock_boto.return_value = s3

        inst.poll()

        mock_boto.assert_called_once()
        call_args = mock_boto.call_args
        assert call_args[0][0] == 's3'  # service name
        assert call_args[1]['region_name'] == 'us-west-1'

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_list_objects_uses_correct_bucket(self, mock_boto):
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=[])
        mock_boto.return_value = s3

        inst.poll()

        s3.list_objects.assert_called_once()
        call_kwargs = s3.list_objects.call_args[1]
        assert call_kwargs['Bucket'] == 'mybucket'

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_list_objects_uses_expanded_prefix(self, mock_boto):
        """Prefix is passed through variableExpansion."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=[])
        mock_boto.return_value = s3

        inst.poll()

        call_kwargs = s3.list_objects.call_args[1]
        assert 'Prefix' in call_kwargs

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_unsigned_config(self, mock_boto):
        """boto3.client is called with UNSIGNED signature."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client(objects=[])
        mock_boto.return_value = s3

        inst.poll()

        call_kwargs = mock_boto.call_args[1]
        config = call_kwargs['config']
        assert config.signature_version == UNSIGNED


# ===================================================================
# Test_S3bucket_poll_repeated
# ===================================================================
class Test_S3bucket_poll_repeated:

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_no_stale_state(self, mock_boto):
        """Two calls with same mock return same count (no accumulation)."""
        inst = _make_s3bucket()
        s3 = _mock_s3_client([
            {'Key': 'some/prefix/file.nc', 'Size': 100},
        ])
        mock_boto.return_value = s3

        msgs1 = inst.poll()
        msgs2 = inst.poll()
        assert len(msgs1) == len(msgs2) == 1

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_always_returns_list(self, mock_boto):
        """Return type is always list, never None."""
        inst = _make_s3bucket()
        mock_boto.return_value = _mock_s3_client(objects=None)

        result = inst.poll()
        assert isinstance(result, list)

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_empty_then_data(self, mock_boto):
        """First call empty, second call with data -> correct transition."""
        inst = _make_s3bucket()
        empty_s3 = _mock_s3_client(objects=None)
        data_s3 = _mock_s3_client([
            {'Key': 'some/prefix/new.nc', 'Size': 50},
        ])
        mock_boto.return_value = empty_s3
        r1 = inst.poll()
        assert r1 == []

        mock_boto.return_value = data_s3
        r2 = inst.poll()
        assert len(r2) == 1


# ===================================================================
# Test_S3bucket_poll_variable_expansion
# ===================================================================
class Test_S3bucket_poll_variable_expansion:

    @patch('sarracenia.flowcb.poll.s3bucket.boto3.client')
    def test_variable_expansion_called(self, mock_boto):
        """self.o.variableExpansion is called on prefix."""
        inst = _make_s3bucket()
        original_expand = inst.o.variableExpansion
        expand_called_with = []

        def spy_expand(val):
            expand_called_with.append(val)
            return original_expand(val)

        inst.o.variableExpansion = spy_expand

        mock_boto.return_value = _mock_s3_client(objects=[])
        inst.poll()
        assert len(expand_called_with) == 1
        assert expand_called_with[0] == inst.prefix