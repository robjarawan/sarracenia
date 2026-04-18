#!/usr/bin/env python3
"""
Layer 1 smoke test for useCompression / Paramiko SSH transport compression.

Runs against a LOCAL OpenSSH-based SFTP container (atmoz/sftp). No Sarracenia
dependencies — this isolates paramiko's compression negotiation. The goal is
to prove that compress=True actually negotiates zlib@openssh.com against a
real OpenSSH server (not just the mock used in unit tests).

Companion to MetPX/sarracenia#1681 / robjarawan/sarracenia#3.

Pre-req: docker running, atmoz/sftp image pulled.

Scenarios exercised:
  1. compress=False  -> expect remote_compression == 'none'
  2. compress=True   -> expect remote_compression == 'zlib@openssh.com'
  3. Real file upload under compress=True (byte-for-byte identical round trip)

Exit code 0 if every scenario passes, non-zero otherwise.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import time

import paramiko

CONTAINER = 'sftp-compression-smoke'
HOST = 'localhost'
PORT = 2222
USER = 'tester'
PASSWORD = 'tester'


def _docker_run() -> None:
    subprocess.run(['docker', 'rm', '-f', CONTAINER],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # atmoz/sftp chroots the user to a root-owned home dir; extra dir tokens
    # after the uid create writable subdirs the user actually owns.
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
    """Poll the SSH banner until the container is ready."""
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


def _connect(compress: bool) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        allow_agent=False,
        look_for_keys=False,
        compress=compress,
        timeout=10,
    )
    return client


def scenario_compress_off() -> tuple[bool, str]:
    c = _connect(compress=False)
    try:
        algo = c.get_transport().remote_compression
        ok = algo == 'none'
        return ok, f'compress=False -> remote_compression={algo!r} (expected none)'
    finally:
        c.close()


def scenario_compress_on() -> tuple[bool, str]:
    c = _connect(compress=True)
    try:
        algo = c.get_transport().remote_compression
        ok = algo == 'zlib@openssh.com'
        return ok, f'compress=True  -> remote_compression={algo!r} (expected zlib@openssh.com)'
    finally:
        c.close()


def scenario_roundtrip_under_compression() -> tuple[bool, str]:
    """Upload + download a compressible payload; verify byte-for-byte."""
    payload = (b'The quick brown fox jumps over the lazy dog.\n' * 8192)
    src_sha = hashlib.sha512(payload).hexdigest()
    remote_name = 'upload/smoke-payload.txt'

    c = _connect(compress=True)
    try:
        sftp = c.open_sftp()
        try:
            with sftp.file(remote_name, 'wb', 1024 * 1024) as fp:
                fp.write(payload)

            with sftp.file(remote_name, 'rb', 1024 * 1024) as fp:
                got = fp.read()

            dst_sha = hashlib.sha512(got).hexdigest()
            ok = (src_sha == dst_sha) and (len(got) == len(payload))
            return ok, (
                f'roundtrip compress=True: {len(payload)} bytes, '
                f'sha512 match={src_sha == dst_sha}'
            )
        finally:
            try:
                sftp.remove(remote_name)
            except Exception:
                pass
            sftp.close()
    finally:
        c.close()


def main() -> int:
    print('[layer1] starting atmoz/sftp container ...')
    _docker_run()
    try:
        _wait_for_sshd()
        print('[layer1] sshd is up on port', PORT)

        scenarios = [
            ('compress=False negotiates none', scenario_compress_off),
            ('compress=True  negotiates zlib@openssh.com', scenario_compress_on),
            ('roundtrip under compression preserves bytes', scenario_roundtrip_under_compression),
        ]

        rc = 0
        for label, fn in scenarios:
            try:
                ok, detail = fn()
            except Exception as exc:
                ok, detail = False, f'EXCEPTION: {exc!r}'
            print(f'  [{"PASS" if ok else "FAIL"}] {label}: {detail}')
            if not ok:
                rc = 1
        return rc
    finally:
        print('[layer1] tearing down container')
        _docker_stop()


if __name__ == '__main__':
    sys.exit(main())
