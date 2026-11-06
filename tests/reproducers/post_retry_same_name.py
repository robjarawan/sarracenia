"""Rob Jarawan: reproduce a same-name post retry with a running SR3 flow.

Run inside a disposable test environment with a local RabbitMQ broker.
The configured callback injects a failed first announcement, while successful
announcements use the built-in post.message callback and a real AMQP queue.
"""

import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import amqp
import sarracenia
from sarracenia.flowcb import FlowCB


LOGGER = logging.getLogger(__name__)
BODY_A = b"first version\n"
BODY_B = b"other version\n"


class Probe(FlowCB):
    """Supply local inputs and fail announcement A during the prepare process."""

    def __init__(self, options):
        super().__init__(options, LOGGER)
        self.root = Path(os.environ['POST_RETRY_CASE'])
        self.phase = os.environ['POST_RETRY_PHASE']
        self.overwrite = os.environ['POST_RETRY_OVERWRITE'] == '1'
        self.sent = 0

    def gather(self, qty):
        bodies = (BODY_A,) if self.phase == 'prepare' else (BODY_B,)
        if self.phase == 'reopen' or self.sent >= len(bodies):
            return True, []
        body = bodies[self.sent]
        self.sent += 1
        source = self.root / 'input' / 'latest.txt'
        source.write_bytes(body)
        message = sarracenia.Message()
        message.update(baseUrl='file:///', relPath=str(source).lstrip('/'),
                       pubTime=sarracenia.nowstr(), mtime=sarracenia.nowstr(),
                       size=len(body), mode='644', local_offset=0,
                       identity={'method': 'md5', 'value': base64.b64encode(
                           hashlib.md5(body).digest()).decode('ascii')})
        message['_deleteOnPost'].add('local_offset')
        return True, [message]

    def post(self, worklist):
        if self.phase != 'prepare':
            return
        good = []
        for message in worklist.ok:
            if message['identity']['value'] == base64.b64encode(
                    hashlib.md5(BODY_A).digest()).decode('ascii'):
                LOGGER.info('injecting announcement failure for A')
                worklist.failed.append(message)
            else:
                good.append(message)
        worklist.ok = good


def run_case(root, overwrite):
    """Run the actual CLI twice, preserving only this config's retry state."""
    (root / 'input').mkdir()
    (root / 'output').mkdir()
    config_dir = root / 'config' / 'sr3' / 'flow'
    config_dir.mkdir(parents=True)
    conf = '\n'.join((
        'download True', 'nodupe_ttl 0', 'mirror False', 'filename None',
        'inflight .tmp', 'sleep -1', 'housekeeping 300', 'batch 10',
        'post_broker amqp://guest:guest@localhost/',
        'post_exchange x_post_retry_probe', 'post_baseUrl http://localhost/',
        'post_baseDir ' + str(root / 'output'),
        'directory ' + str(root / 'output'),
        'flowCallback post_retry_same_name.Probe',
        'flowCallback sarracenia.flowcb.post.message.Message',
        'accept .*', ''))
    config = config_dir / 'probe.conf'
    config.write_text(conf)
    env = dict(os.environ, HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
               XDG_CACHE_HOME=str(root / 'cache'), POST_RETRY_CASE=str(root),
               POST_RETRY_OVERWRITE='1' if overwrite else '0',
               PYTHONPATH=str(Path(__file__).resolve().parent))
    connection = amqp.Connection(host='localhost', userid='guest', password='guest')
    connection.connect()
    channel = connection.channel()
    queue = None
    try:
        channel.exchange_declare('x_post_retry_probe', type='topic', durable=True, auto_delete=False)
        queue, _, _ = channel.queue_declare('', exclusive=True, auto_delete=True)
        channel.queue_bind(queue, 'x_post_retry_probe', '#')
        phases = ('prepare', 'replace', 'reopen') if overwrite else ('prepare', 'reopen')
        for phase in phases:
            env['POST_RETRY_PHASE'] = phase
            config = config_dir / ('update.conf' if phase == 'replace' else 'probe.conf')
            config.write_text(conf + ('retryEmptyBeforeExit True\n' if phase == 'reopen' else ''))
            process = subprocess.Popen(['sr3', 'foreground', 'flow/' + config.stem], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, universal_newlines=True)
            try:
                output, _ = process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    output, _ = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    output, _ = process.communicate(timeout=5)
                sys.stdout.write(output)
                raise
            sys.stdout.write(output)
            if process.returncode:
                raise RuntimeError('SR3 process failed: ' + phase)
            if phase == 'prepare':
                pending = list((root / 'cache').rglob('diskqueue_post_retry_*'))
                assert pending and any(p.stat().st_size for p in pending), 'retry was not persisted'
        received = []
        while True:
            notice = channel.basic_get(queue, no_ack=True)
            if notice is None:
                break
            message = json.loads(notice.body)
            body = (root / 'output' / 'latest.txt').read_bytes()
            claimed = message['identity']['value']
            actual = base64.b64encode(hashlib.md5(body).digest()).decode('ascii')
            received.append({'claimed': claimed, 'actual': actual, 'matches': claimed == actual})
        expected = 1
        print(json.dumps({'overwrite': overwrite, 'received': received,
                          'module': sarracenia.__file__, 'version': sarracenia.__version__}))
        return len(received) == expected and all(row['matches'] for row in received)
    finally:
        if queue is not None:
            channel.queue_delete(queue)
        channel.close()
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sr3-post-retry-') as case:
        passed = run_case(Path(case), args.overwrite)
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
