"""Exercise real failed downloads and retry consolidation on a bounded full tmpfs.

Run only in a disposable container with a 1 MiB tmpfs mounted at /retry:
python -m tests.reliability.housekeeping_proof
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.diskqueue import DiskQueue
from sarracenia.flow import Flow


def configuration(root: Path):
    destination = root / 'destination'
    destination.mkdir(exist_ok=True)
    path = root / 'subscribe.conf'
    path.write_text('download True\nretry_ttl 0\nretryCountMax 0\n'
                    'inflight None\naccelThreshold 0\npermCopy False\n'
                    'directory ' + str(destination) + '\naccept .*\n')
    cfg = no_file_config()
    cfg.component = 'subscribe'
    cfg.config = 'housekeeping-proof'
    cfg.no = 1
    cfg.logLevel = 'info'
    cfg.pid_filename = str(root / 'instance.pid')
    cfg.novipFilename = str(root / 'novip')
    cfg.metricsFilename = str(root / 'metrics.json')
    cfg.parse_file(str(path), component='subscribe')
    return cfg


def fill_filesystem(root: Path) -> None:
    stat = os.statvfs(root)
    assert os.path.ismount('/retry') and stat.f_blocks * stat.f_frsize <= 2 * 1024 * 1024
    with (root / 'filler').open('xb', buffering=0) as stream:
        try:
            while True:
                stream.write(b'x' * 4096)
        except OSError as error:
            if error.errno != errno.ENOSPC:
                raise


def worker(root: Path, fault: bool) -> None:
    cfg = configuration(root)
    flow = Flow(cfg)
    flow.have_vip = True
    assert flow.loadCallbacks(['sarracenia.flowcb.retry.Retry'])
    flow.runCallbacksTime('on_start')
    retry = flow.plugins['after_work'][0].__self__
    messages = []
    for name in ('first.bin', 'second.bin'):
        message = sarracenia.Message()
        message.update(baseUrl='file:' + str(root / 'missing-source'), relPath=name,
                       pubTime=sarracenia.nowstr(), size=10, local_offset=0,
                       identity={'method': 'arbitrary', 'value': name})
        messages.append(message)
    flow.worklist.incoming = messages
    flow.filter()
    flow.work()
    queue = retry.download_retry
    before = Path(queue.new_path).read_bytes()
    assert queue._count_msgs(queue.new_path) == 2, 'failed downloads did not enter retry storage'
    if fault:
        fill_filesystem(root)
    retry.on_housekeeping()
    after = Path(queue.new_path).read_bytes() if Path(queue.new_path).exists() else None
    print(json.dumps(dict(before_records=2, full_filesystem=fault,
                          retry_input_preserved=(after == before)), sort_keys=True), flush=True)
    # Emulate interruption after the callback; deliberately bypass graceful queue cleanup.
    os._exit(0)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3] == 'full')
        return
    outcomes = []
    for fault in (False, True):
        with tempfile.TemporaryDirectory(prefix='consolidation-', dir='/retry') as temporary:
            root = Path(temporary)
            command = [sys.executable, '-m', 'tests.reliability.housekeeping_proof',
                       '--worker', str(root), 'full' if fault else 'healthy']
            child = subprocess.run(command,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True, timeout=20)
            print(child.stderr, file=sys.stderr, end='')
            assert child.returncode == 0, child.stdout
            if (root / 'filler').exists():
                (root / 'filler').unlink()
            restarted = DiskQueue(configuration(root), 'work_retry_01')
            restarted.on_housekeeping()
            recovered = sorted(message['relPath'] for message in restarted.get(10))
            restarted.close()
            outcomes.append(dict(full_filesystem=fault, recovered=recovered,
                                 worker_observation=json.loads(child.stdout)))
    print(json.dumps(dict(module=sarracenia.__file__, python=sys.version, cases=outcomes), indent=2))
    assert all(case['recovered'] == ['first.bin', 'second.bin'] for case in outcomes), 'retry records lost on restart'


if __name__ == '__main__':
    main()
