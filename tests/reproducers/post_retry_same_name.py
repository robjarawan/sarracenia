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
        self.sent = 0

    def on_start(self):
        import sarracenia.flowcb.retry
        actual = hashlib.sha256(Path(sarracenia.flowcb.retry.__file__).read_bytes()).hexdigest()
        if actual != os.environ['POST_RETRY_EXPECTED_HASH']:
            raise RuntimeError('foreground process loaded a different retry implementation')
        LOGGER.info('foreground retry implementation sha256=%s', actual)

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


def run_foreground(command, env):
    """Own and reap this exact foreground process group on every exit."""
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True,
                               universal_newlines=True)
    try:
        output, _ = process.communicate(timeout=45)
        if process.returncode:
            raise RuntimeError('SR3 foreground process failed')
        return output
    finally:
        primary_error = sys.exc_info()[0] is not None
        try:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if not primary_error:
                raise
            LOGGER.exception('foreground cleanup failed while handling the original failure')


def close_resources(actions):
    """Attempt every release without replacing an active primary failure."""
    primary_error = sys.exc_info()[0] is not None
    errors = []
    for action in actions:
        try:
            action()
        except (amqp.exceptions.AMQPError, OSError) as error:
            errors.append(error)
            LOGGER.warning('reproduction cleanup failed: %s', error)
    if errors and not primary_error:
        raise errors[0]


def run_case(root, overwrite, inline=False):
    """Run the actual CLI twice, preserving only this config's retry state."""
    (root / 'input').mkdir()
    (root / 'output').mkdir()
    config_dir = root / 'config' / 'sr3' / 'flow'
    config_dir.mkdir(parents=True)
    conf = Path(__file__).with_suffix('.conf').read_text()
    if inline:
        conf += 'inline True\n'
    config = config_dir / 'probe.conf'
    config.write_text(conf)
    env = dict(os.environ, HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
               XDG_CACHE_HOME=str(root / 'cache'), POST_RETRY_CASE=str(root),
               POST_RETRY_EXPECTED_HASH=hashlib.sha256((Path(__file__).resolve().parents[2]
                   / 'sarracenia' / 'flowcb' / 'retry.py').read_bytes()).hexdigest(),
               PYTHONPATH=str(Path(__file__).resolve().parent))
    connection = amqp.Connection(host='localhost', userid='guest', password='guest')
    channel = None
    queue = None
    try:
        connection.connect()
        channel = connection.channel()
        channel.exchange_declare('x_post_retry_probe', type='topic', durable=True, auto_delete=False)
        queue, _, _ = channel.queue_declare('', exclusive=True, auto_delete=True)
        channel.queue_bind(queue, 'x_post_retry_probe', '#')
        phases = ('prepare', 'replace', 'reopen') if overwrite else ('prepare', 'reopen')
        for phase in phases:
            env['POST_RETRY_PHASE'] = phase
            config = config_dir / ('update.conf' if phase == 'replace' else 'probe.conf')
            config.write_text(conf + ('retryEmptyBeforeExit True\n' if phase == 'reopen' else ''))
            output = run_foreground(['sr3', 'foreground', 'flow/' + config.stem], env)
            sys.stdout.write(output)
            if 'foreground retry implementation sha256=' + env['POST_RETRY_EXPECTED_HASH'] not in output:
                raise RuntimeError('foreground implementation verification was not observed')
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
            if 'content' in message:
                content = message['content']
                body = (base64.b64decode(content['value']) if content['encoding'] == 'base64'
                        else content['value'].encode(content['encoding']))
            claimed = message['identity']['value']
            actual = base64.b64encode(hashlib.md5(body).digest()).decode('ascii')
            received.append({'claimed': claimed, 'actual': actual, 'matches': claimed == actual})
        expected = 2 if overwrite and inline else 1
        print(json.dumps({'overwrite': overwrite, 'inline': inline, 'received': received,
                          'module': sarracenia.__file__, 'version': sarracenia.__version__}))
        return len(received) == expected and all(row['matches'] for row in received)
    finally:
        actions = []
        if queue is not None:
            actions.append(lambda: channel.queue_delete(queue))
        if channel is not None:
            actions.append(channel.close)
        actions.append(connection.close)
        close_resources(actions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--inline', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sr3-post-retry-') as case:
        passed = run_case(Path(case), args.overwrite, args.inline)
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
