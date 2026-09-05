"""Real AMQP delivery and failed file download with a full disposable retry filesystem.

Run against a disposable RabbitMQ on loopback, with a 1 MiB tmpfs at /retry:
python -m tests.reliability.retry_ack_proof
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import amqp
import sarracenia
from sarracenia.config import no_file_config
from sarracenia.diskqueue import DiskQueue
from sarracenia.flow import Flow
from sarracenia.flowcb.gather.message import Message as Gather


def configuration(root: Path):
    destination = root / 'destination'
    destination.mkdir(exist_ok=True)
    path = root / 'subscribe.conf'
    path.write_text(
        'broker amqp://guest:guest@127.0.0.1/\nexchange sr-proof\n'
        'prefetch 1\ndurable True\nauto_delete False\nexpire 0\n'
        'queueName ' + root.name + '\nsubtopic fixture\n'
        'download True\nretry_ttl 0\nretryCountMax 0\nbatch 10\n'
        'inflight None\naccelThreshold 0\npermCopy False\n'
        'directory ' + str(destination) + '\naccept .*\n')
    cfg = no_file_config()
    cfg.component = 'subscribe'
    cfg.config = 'retry-ack-proof'
    cfg.no = 1
    cfg.logLevel = 'info'
    cfg.pid_filename = str(root / 'instance.pid')
    cfg.novipFilename = str(root / 'novip')
    cfg.metricsFilename = str(Path('/tmp') / (root.name + '-metrics.json'))
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


def worker(root: Path, mode: str) -> None:
    flow = Flow(configuration(root))
    flow.have_vip = True
    assert flow.loadCallbacks(['sarracenia.flowcb.gather.message.Message', 'sarracenia.flowcb.retry.Retry'])
    flow.runCallbacksTime('on_start')
    flow.gather()
    assert len(flow.worklist.incoming) == 1, 'fixture did not receive its broker message'
    flow.filter()
    assert len(flow.worklist.incoming) == 1, 'fixture did not reach the download phase'
    if mode != 'healthy':
        fill_filesystem(root)
    flow.work()
    flow.post(sarracenia.nowflt())
    pending_during_failure = len(getattr(flow.worklist, 'failed_pending', []))
    if mode == 'recover':
        (root / 'filler').unlink()
        flow.work()
        flow.post(sarracenia.nowflt())
    print(json.dumps(dict(mode=mode, pending_during_failure=pending_during_failure,
                          failed_after_post=len(flow.worklist.failed),
                          pending_after_post=len(getattr(flow.worklist, 'failed_pending', [])))), flush=True)
    # Abrupt process interruption loses Python buffers and in-memory ownership.
    os._exit(0)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3])
        return
    broker = amqp.Connection('127.0.0.1', userid='guest', password='guest', virtual_host='/', connect_timeout=10)
    broker.connect()
    channel = broker.channel()
    channel.exchange_declare('sr-proof', type='topic', durable=False, auto_delete=False)
    outcomes = []
    try:
        for mode in ('healthy', 'full', 'recover'):
            with tempfile.TemporaryDirectory(prefix='ack-proof-', dir='/retry') as temporary:
                root = Path(temporary)
                cfg = configuration(root)
                setup = Gather(cfg)
                setup.consumers[0].getSetup()
                setup.on_stop()
                notification = dict(baseUrl='file:' + str(root / 'missing-source'), relPath='missing.bin',
                                    pubTime=sarracenia.nowstr(), size=10,
                                    identity={'method': 'arbitrary', 'value': 'fixture'})
                channel.basic_publish(amqp.Message(json.dumps(notification), content_type='application/json'),
                                      exchange='sr-proof', routing_key='v03.fixture')
                channel.queue_declare(root.name, passive=True)
                child = subprocess.run([sys.executable, '-m', 'tests.reliability.retry_ack_proof',
                                        '--worker', str(root), mode],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       universal_newlines=True, timeout=25)
                print(child.stderr, file=sys.stderr, end='')
                assert child.returncode == 0, child.stdout
                # Allow RabbitMQ to observe the worker's closed TCP connection.
                raw = None
                for attempt in range(20):
                    raw = channel.basic_get(root.name)
                    if raw is not None:
                        break
                    time.sleep(0.05)
                if raw is not None:
                    channel.basic_ack(raw.delivery_info['delivery_tag'])
                if (root / 'filler').exists():
                    (root / 'filler').unlink()
                restarted = DiskQueue(cfg, 'work_retry_01')
                restarted.on_housekeeping()
                recovered = len(restarted.get(10))
                restarted.close()
                outcomes.append(dict(mode=mode, broker_redelivered=raw is not None,
                                     retries_recovered=recovered, worker_observation=json.loads(child.stdout)))
                channel.queue_delete(root.name)
    finally:
        channel.exchange_delete('sr-proof')
        broker.close()
    print(json.dumps(dict(module=sarracenia.__file__, python=sys.version, cases=outcomes), indent=2))
    assert outcomes[0]['retries_recovered'] == 1 and not outcomes[0]['broker_redelivered']
    assert outcomes[1]['broker_redelivered'], 'failed source delivery was acknowledged before retry storage succeeded'
    assert outcomes[2]['retries_recovered'] == 1 and not outcomes[2]['broker_redelivered']


if __name__ == '__main__':
    main()
