import pytest
from tests.conftest import *
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import sarracenia.config
import sarracenia.flowcb.poll.mail


class FakeImap:

    def login(self, user, password):
        pass

    def select(self, mailbox):
        pass

    def search(self, charset, criterion):
        return 'OK', [b'1']

    def fetch(self, index, query):
        return 'OK', [(None, b'Subject: test\n\nbody')]

    def close(self):
        pass

    def logout(self):
        pass


def test_first_imap_response_records_metrics():
    details = SimpleNamespace(url=urlparse('imaps://user:password@example.test/'))
    credentials = SimpleNamespace(get=lambda url: (True, details))
    options = SimpleNamespace(credentials=credentials, pollUrl=details.url)
    mail = sarracenia.flowcb.poll.mail.Mail(options)
    message = object()

    with patch('sarracenia.flowcb.poll.mail.imaplib.IMAP4_SSL', return_value=FakeImap()), \
            patch('sarracenia.flowcb.poll.mail.sarracenia.Message.fromFileInfo', return_value=message):
        gathered = mail.poll()

    assert gathered == [message]
    assert mail.metrics == {'transferRxBytes': 1}
