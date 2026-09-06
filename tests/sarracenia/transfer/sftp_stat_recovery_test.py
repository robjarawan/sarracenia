"""SFTP metadata failures through Sender and a local socket-pair SFTP server."""

import errno
import os
import socket
import sys
import threading
import time

import paramiko
import pytest

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.flow.sender import Sender
from sarracenia.transfer.sftp import Sftp


class FixtureServer(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        if username == 'fixture' and password == 'fixture':
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, channel_id):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class LocalFiles(paramiko.SFTPServerInterface):
    def __init__(self, server, root, fault, ready, calls):
        super().__init__(server)
        self.root = root
        self.fault = fault
        self.ready = ready
        self.calls = calls

    def local_path(self, path):
        return self.root / path.lstrip('/')

    def stat(self, path):
        self.calls.append(('stat', path))
        if path.endswith('payload.bin') and self.fault:
            fault, self.fault = self.fault, None
            if fault == 'delay':
                time.sleep(3)
                self.ready.set()
            else:
                self.ready.set()
                return paramiko.SFTPServer.convert_errno(
                    errno.EIO if fault == 'io' else errno.EACCES)
        try:
            return paramiko.SFTPAttributes.from_stat(self.local_path(path).stat())
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    lstat = stat

    def open(self, path, flags, attr):
        self.calls.append(('open', path, flags))
        try:
            descriptor = os.open(self.local_path(path), flags, 0o600)
            stream = os.fdopen(descriptor, 'r+b' if flags & os.O_RDWR else 'wb')
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)
        handle = paramiko.SFTPHandle(flags)
        handle.readfile = stream
        handle.writefile = stream
        return handle


@pytest.mark.parametrize('compat_mode', [False, True])
@pytest.mark.parametrize('fault', [None, 'io', 'denied', 'delay', 'missing'])
def test_sender_preserves_target_and_recovers_after_stat_failure(
        tmp_path, fault, compat_mode):
    if fault == 'delay' and sys.platform == 'win32':
        pytest.skip('SFTP transfer alarms are not implemented on Windows')
    source_dir = tmp_path / 'input'
    remote_dir = tmp_path / 'remote'
    source_dir.mkdir()
    remote_dir.mkdir()
    (source_dir / 'payload.bin').write_bytes(b'AAAAxxxxCCCC')
    target = remote_dir / 'payload.bin'
    original = b'AAAABBBBCCCC'
    if fault != 'missing':
        target.write_bytes(original)

    options = no_file_config()
    options.component = 'sender'
    options.config = 'stat_recovery'
    options.action = 'start'
    options.no = 1
    options.metricsFilename = str(tmp_path / 'metrics')
    options.novipFilename = str(tmp_path / 'novip')
    for number, line in enumerate([
            'baseDir ' + str(source_dir), 'sendTo sftp://fixture:fixture@localhost/',
            'inflight None', 'timeout 1', 'permDefault 0000',
            'permCopy False', 'timeCopy False',
    ], 1):
        options.parse_line('sender', 'stat_recovery', 'stat_recovery.conf', number, line)
    options.finalize()

    # Keys and authentication values exist only inside this disposable fixture.
    # The transport is an AF_UNIX socket pair; no server port is exposed.
    server_socket, client_socket = socket.socketpair()
    server_transport = paramiko.Transport(server_socket)
    client_transport = paramiko.Transport(client_socket)
    ready = threading.Event()
    calls = []
    sftp = None
    try:
        server_transport.add_server_key(paramiko.RSAKey.generate(2048))
        server_transport.set_subsystem_handler(
            'sftp', paramiko.SFTPServer, LocalFiles,
            root=remote_dir, fault=fault if fault not in (None, 'missing') else None,
            ready=ready, calls=calls)
        server_transport.start_server(event=threading.Event(), server=FixtureServer())
        client_transport.connect(username='fixture', password='fixture')
        sftp = paramiko.SFTPClient.from_transport(client_transport)
        sftp.chdir('/')

        transfer = Sftp('sftp', options)
        transfer.sftp = sftp
        transfer.connected = True
        transfer.sendTo = options.sendTo
        transfer.originalDir = '/'
        transfer.compat_mode = compat_mode
        sender = Sender(options)
        sender.proto['sftp'] = transfer
        message = sarracenia.Message()
        message.update(
            relPath='payload.bin', new_file='payload.bin', new_dir='/',
            local_dir=str(source_dir), local_offset=4, size=4,
            blocks={'method': 'inplace', 'number': 1,
                    'manifest': {index: {'size': 4} for index in range(3)}})

        result = sender.send(message, options)
        if fault in ('io', 'denied', 'delay'):
            assert result == 0, (result, target.read_bytes(), calls)
            assert target.read_bytes() == original
            assert not any(call[0] == 'open' for call in calls)
            assert ready.wait(4), 'the fixture did not finish the transient fault'
            assert sender.send(message, options) == 1
            assert target.read_bytes() == b'AAAAxxxxCCCC'
        elif fault == 'missing':
            assert result == 1
            assert target.read_bytes() == b'\x00\x00\x00\x00xxxx'
        else:
            assert result == 1
            assert target.read_bytes() == b'AAAAxxxxCCCC'
    finally:
        if sftp is not None:
            sftp.close()
        client_transport.close()
        server_transport.close()
        client_socket.close()
        server_socket.close()
