#!/usr/bin/env python3
"""
Layer 1b: exercise Sarracenia's own Sftp class against the atmoz/sftp
container, with useCompression on and off. This is one step up from
layer1_paramiko_smoke.py: it goes through our code (the add_option,
the compress kwarg wiring, and the INFO log) rather than raw paramiko.

Pre-req: docker running, atmoz/sftp image pulled, and this laptop's
Sarracenia dev install at /home/empz/src/sarracenia.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import time

# Make Sarracenia importable without `pip install -e .` (for dev-loop speed)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import sarracenia.config                # noqa: E402
import sarracenia.transfer.sftp         # noqa: E402


CONTAINER = 'sftp-compression-smoke'
HOST = 'localhost'
PORT = 2222
USER = 'tester'
PASSWORD = 'tester'


def _docker_run() -> None:
    subprocess.run(['docker', 'rm', '-f', CONTAINER],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call([
        'docker', 'run', '-d',
        '--name', CONTAINER,
        '-p', f'{PORT}:22',
        'atmoz/sftp:latest',
        f'{USER}:{PASSWORD}:1001::upload',
    ], stdout=subprocess.DEVNULL)


def _docker_stop() -> None:
    subprocess.run(['docker', 'rm', '-f', CONTAINER],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for_sshd(deadline_s: float = 30.0) -> None:
    import socket
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            with socket.create_connection((HOST, PORT), timeout=1.0) as s:
                banner = s.recv(128)
                if banner.startswith(b'SSH-'):
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError('sshd never came up in container')


def _build_sftp(use_compression: bool) -> sarracenia.transfer.sftp.Sftp:
    """Build the Sarracenia Sftp instance the way connect() expects it."""
    options = sarracenia.config.default_config()
    xfer = sarracenia.transfer.sftp.Sftp('sftp', options)
    xfer.o.useCompression = use_compression
    xfer.o.timeout = 15
    xfer.host = HOST
    xfer.port = PORT
    xfer.user = USER
    xfer.password = PASSWORD
    xfer.ssh_keyfile = None
    xfer.sendTo = f'sftp://{USER}@{HOST}:{PORT}/'
    xfer.o.sendTo = xfer.sendTo
    # Short-circuit credential lookup; we feed them directly.
    xfer.credentials = lambda: True
    return xfer


def scenario(label: str, use_compression: bool,
              expect_algo: str, expect_info_fragment: str | None) -> tuple[bool, str]:
    xfer = _build_sftp(use_compression=use_compression)

    # capture INFO from the sftp module logger
    captured: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    mod_logger = logging.getLogger('sarracenia.transfer.sftp')
    old_level = mod_logger.level
    mod_logger.setLevel(logging.INFO)
    handler = _Collector()
    handler.setLevel(logging.INFO)
    mod_logger.addHandler(handler)

    try:
        ok_connect = xfer.connect()
        if not ok_connect:
            return False, f'{label}: Sftp.connect() returned False'

        algo = xfer.ssh.get_transport().remote_compression
        if algo != expect_algo:
            return False, f'{label}: negotiated={algo!r}, expected {expect_algo!r}'

        if expect_info_fragment is None:
            if any('compression' in m for m in captured):
                return False, f'{label}: unexpected compression INFO: {captured}'
        else:
            if not any(expect_info_fragment in m for m in captured):
                return False, (
                    f'{label}: expected INFO containing {expect_info_fragment!r}, '
                    f'got {captured}'
                )

        # round-trip a compressible payload through Sarracenia's sftp.file API
        payload = (b'lorem ipsum dolor sit amet ' * 4096)
        src_sha = hashlib.sha512(payload).hexdigest()
        remote_name = 'upload/layer1b-payload.txt'
        with xfer.sftp.file(remote_name, 'wb', 1024 * 1024) as fp:
            fp.write(payload)
        with xfer.sftp.file(remote_name, 'rb', 1024 * 1024) as fp:
            got = fp.read()
        try:
            xfer.sftp.remove(remote_name)
        except Exception:
            pass
        dst_sha = hashlib.sha512(got).hexdigest()
        if src_sha != dst_sha or len(got) != len(payload):
            return False, f'{label}: roundtrip mismatch (len={len(got)} vs {len(payload)})'

        return True, f'{label}: negotiated={algo!r}, roundtrip_ok=True, info_lines={len(captured)}'
    finally:
        try:
            xfer.close()
        except Exception:
            pass
        mod_logger.removeHandler(handler)
        mod_logger.setLevel(old_level)


def main() -> int:
    print('[layer1b] starting atmoz/sftp container ...')
    _docker_run()
    try:
        _wait_for_sshd()
        print('[layer1b] sshd is up on port', PORT)

        cases = [
            ('useCompression=False', False, 'none', None),
            ('useCompression=True',  True,  'zlib@openssh.com',
             'compression requested, negotiated=zlib@openssh.com'),
        ]
        rc = 0
        for label, use_comp, algo, info in cases:
            try:
                ok, detail = scenario(label, use_comp, algo, info)
            except Exception as exc:
                ok, detail = False, f'{label}: EXCEPTION {exc!r}'
            print(f'  [{"PASS" if ok else "FAIL"}] {detail}')
            if not ok:
                rc = 1
        return rc
    finally:
        print('[layer1b] tearing down container')
        _docker_stop()


if __name__ == '__main__':
    sys.exit(main())
