import socket
from unittest.mock import Mock

import sarracenia.flowcb.send.am


class SocketRecordingWrites:

    def __init__(self):
        self.send_calls = []
        self.sendall_calls = []

    def send(self, data):
        self.send_calls.append(data)
        return len(data) - 1

    def sendall(self, data):
        self.sendall_calls.append(data)


def test_send_uses_all_bytes_socket_operation():
    bulletin = object()
    packed_bulletin = b'complete AM frame'
    sender = object.__new__(sarracenia.flowcb.send.am.Am)
    sender.wrapbulletin = Mock(return_value=packed_bulletin)
    sender.s = SocketRecordingWrites()

    assert sender.send(bulletin) is True
    assert sender.s.send_calls == []
    assert sender.s.sendall_calls == [packed_bulletin]


def test_send_reconnects_before_retry_after_socket_error():
    packed_bulletin = b'complete AM frame'
    initial_socket = Mock()
    initial_socket.sendall.side_effect = socket.error('connection lost during send')
    replacement_socket = Mock()
    sender = object.__new__(sarracenia.flowcb.send.am.Am)
    sender.wrapbulletin = Mock(return_value=packed_bulletin)
    sender.s = initial_socket

    def replace_socket():
        sender.s = replacement_socket

    sender.reEstablishConnection = Mock(side_effect=replace_socket)

    assert sender.send(object()) is True
    initial_socket.sendall.assert_called_once_with(packed_bulletin)
    initial_socket.close.assert_called_once_with()
    sender.reEstablishConnection.assert_called_once_with()
    replacement_socket.sendall.assert_called_once_with(packed_bulletin)
