from types import SimpleNamespace
from unittest.mock import Mock

import sarracenia.flowcb.work.send_egc_les


def test_after_work_stops_after_invalid_credentials(monkeypatch):
    message = {'new_relPath': 'must-not-be-opened'}
    worklist = SimpleNamespace(ok=[message], rejected=[])
    options = SimpleNamespace(
        credentials=SimpleNamespace(get=lambda setting: (False, None)),
        file_send_egc_les_telnet='telnet://invalid@les.example',
        file_send_egc_les_timeout=5,
    )
    callback = object.__new__(sarracenia.flowcb.work.send_egc_les.Send_egc_les)
    callback.o = options
    open_file = Mock(side_effect=AssertionError('Local file must not be opened'))
    telnet = Mock(side_effect=AssertionError('Telnet must not be opened'))
    monkeypatch.setattr('builtins.open', open_file)
    monkeypatch.setattr(sarracenia.flowcb.work.send_egc_les.telnetlib, 'Telnet', telnet)

    callback.after_work(worklist)

    assert worklist.ok == []
    assert worklist.rejected == [message]
    open_file.assert_not_called()
    telnet.assert_not_called()
