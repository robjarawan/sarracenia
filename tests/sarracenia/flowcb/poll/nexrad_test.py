import pytest
from tests.conftest import *
from unittest.mock import patch

import sarracenia.config
import sarracenia.flowcb.poll.nexrad


class FakeStationsResponse:

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def readlines(self):
        return [b'short line\n']


class EmptyS3:

    def list_objects(self, **kwargs):
        return {}


def test_first_station_response_records_metrics():
    options = sarracenia.config.default_config()
    nexrad = sarracenia.flowcb.poll.nexrad.Nexrad(options)

    with patch('sarracenia.flowcb.poll.nexrad.urllib.request.urlopen', return_value=FakeStationsResponse()), \
            patch('sarracenia.flowcb.poll.nexrad.boto3.client', return_value=EmptyS3()):
        gathered = nexrad.poll()

    assert gathered == []
    assert nexrad.metrics == {'transferRxBytes': 1}
