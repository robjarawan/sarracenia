from types import SimpleNamespace
from urllib.parse import urlparse

import sarracenia.flowcb.work.send_egc_les


class BytesOnlyTelnet:

    instances = []

    def __init__(self, server, port, timeout):
        self.connection = (server, port, timeout)
        self.read_markers = []
        self.writes = []
        self.__class__.instances.append(self)

    def read_until(self, marker, timeout):
        if not isinstance(marker, bytes):
            raise TypeError('read_until marker must be bytes')
        self.read_markers.append(marker)
        return marker

    def write(self, data):
        if not isinstance(data, bytes):
            raise TypeError('write data must be bytes')
        self.writes.append(data)
        return len(data)

    def read_all(self):
        return b'Storing Submitted Reference'

    def close(self):
        pass


def test_after_work_uses_bytes_for_telnet_protocol(tmp_path, monkeypatch):
    bulletin_path = tmp_path / 'bulletin.txt'
    bulletin_path.write_text('FQCN01 CWAO forecast\nbody', encoding='ascii')
    message = {
        'new_dir': str(tmp_path),
        'new_file': bulletin_path.name,
        'new_relPath': str(bulletin_path),
    }
    worklist = SimpleNamespace(ok=[message], rejected=[])
    details = SimpleNamespace(url=urlparse('telnet://user:password@les.example'))
    options = SimpleNamespace(
        credentials=SimpleNamespace(get=lambda setting: (True, details)),
        file_send_egc_les_telnet='telnet://user@les.example',
        file_send_egc_les_timeout=5,
    )
    callback = object.__new__(sarracenia.flowcb.work.send_egc_les.Send_egc_les)
    callback.o = options
    BytesOnlyTelnet.instances.clear()
    monkeypatch.setattr(sarracenia.flowcb.work.send_egc_les.telnetlib, 'Telnet', BytesOnlyTelnet)

    callback.after_work(worklist)

    telnet = BytesOnlyTelnet.instances[0]
    assert telnet.read_markers == [b'username:', b'password:', b'>', b'Text:']
    assert telnet.writes == [
        b'user\r\n',
        b'password\r\n',
        b'Egc 2 1 4 66n171w11053 1 0\r\n',
        b'FQCN01 CWAO forecast\r\nbody.S\r\n',
        b'quit\r\n',
    ]
    assert worklist.ok == [message]
    assert worklist.rejected == []
