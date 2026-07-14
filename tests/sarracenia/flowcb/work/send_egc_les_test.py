from types import SimpleNamespace
from urllib.parse import urlparse

import sarracenia.flowcb.work.send_egc_les


class TextTelnet:

    def __init__(self, server, port, timeout):
        self.connection = (server, port, timeout)

    def read_until(self, marker, timeout):
        return marker

    def write(self, data):
        return len(data)

    def read_all(self):
        return 'Storing Submitted Reference'

    def close(self):
        pass


def test_after_work_reads_downloaded_file_not_posting_path(tmp_path, monkeypatch):
    bulletin_path = tmp_path / 'bulletin.txt'
    bulletin_path.write_text('FQCN01 CWAO forecast\nbody', encoding='ascii')
    message = {
        'new_dir': str(tmp_path),
        'new_file': bulletin_path.name,
        'new_relPath': 'remote/posting/path/bulletin.txt',
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
    monkeypatch.setattr(sarracenia.flowcb.work.send_egc_les.telnetlib, 'Telnet', TextTelnet)

    callback.after_work(worklist)

    assert worklist.ok == [message]
    assert worklist.rejected == []
    assert not bulletin_path.exists()
