import errno
import os
from unittest.mock import patch

import pytest

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.flow import Flow


def options(tmp_path):
    current = no_file_config()
    current.component = 'subscribe'
    current.config = 'directory-retry-test'
    current.no = 1
    current.download = True
    current.logLevel = 'info'
    current.batch = 1
    current.attempts = 1
    current.bufSize = 1024
    current.retry_ttl = 0
    current.retryCountMax = 0
    current.timeout = 1
    current.pid_filename = str(tmp_path / 'test.pid')
    current.metricsFilename = str(tmp_path / 'metrics.json')
    current.novipFilename = str(tmp_path / 'novip')
    current.publishers = []
    return current


def message(source, destination):
    current = sarracenia.Message()
    current.update(
        baseUrl='file:' + str(source.parent),
        relPath=source.name,
        pubTime=sarracenia.nowstr(),
        size=source.stat().st_size,
        identity={'method': 'arbitrary', 'value': source.name},
        new_dir=str(destination),
        new_file=source.name,
        local_offset=0,
    )
    return current


@pytest.mark.parametrize('error_number', [errno.ENOSPC, errno.EACCES])
def test_directory_creation_failure_is_retried_after_recovery(
    tmp_path,
    error_number,
):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'recoverable payload')
    destination = tmp_path / 'destination'
    current = message(source, destination)
    flow = Flow(options(tmp_path))
    original_makedirs = os.makedirs
    failed_once = False

    def recoverable_makedirs(name, mode, exist_ok):
        nonlocal failed_once
        if name == str(destination) and not failed_once:
            failed_once = True
            raise OSError(error_number, os.strerror(error_number), name)
        return original_makedirs(name, mode, exist_ok)

    original_directory = os.getcwd()
    try:
        flow.worklist.incoming = [current]
        with patch(
            'sarracenia.flow.os.makedirs',
            side_effect=recoverable_makedirs,
        ):
            flow.do_download()
            initial_failed = list(flow.worklist.failed)
            initial_rejected = list(flow.worklist.rejected)

            flow.worklist.incoming = [current]
            flow.worklist.failed = []
            flow.worklist.ok = []
            flow.worklist.rejected = []
            flow.worklist.directories_ok = []
            flow.do_download()
    finally:
        os.chdir(original_directory)

    assert initial_failed == [current]
    assert initial_rejected == []
    assert flow.worklist.ok == [current]
    assert flow.worklist.failed == []
    assert flow.worklist.rejected == []
    assert destination.joinpath(source.name).read_bytes() == source.read_bytes()


def test_missing_remove_directory_remains_rejected(tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'not downloaded')
    destination = tmp_path / 'missing'
    current = message(source, destination)
    current['fileOp'] = {'remove': ''}
    flow = Flow(options(tmp_path))
    flow.worklist.incoming = [current]

    with patch('sarracenia.flow.os.makedirs') as makedirs:
        flow.do_download()

    makedirs.assert_not_called()
    assert flow.worklist.failed == []
    assert flow.worklist.rejected == [current]
    assert flow.worklist.ok == []
