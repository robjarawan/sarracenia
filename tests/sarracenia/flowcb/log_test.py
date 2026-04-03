import pytest
import types
import sarracenia
import sarracenia.config
from sarracenia.flowcb.log import Log
from sarracenia import nowflt, timeflt2str, timestr2flt


def make_options(component='subscribe'):
    options = sarracenia.config.default_config()
    options.component = component
    options.config = 'test'
    options.logLevel = 'info'
    options.download = True
    return options


def make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def make_message(pub_age=5.0, relPath='a/b/file.txt', size=1024):
    msg = sarracenia.Message()
    msg['pubTime'] = timeflt2str(nowflt() - pub_age)
    msg['baseUrl'] = 'https://example.com'
    msg['relPath'] = relPath
    if size is not None:
        msg['size'] = size
    return msg


class TestLogInit:
    def test_verb_sender(self):
        log = Log(make_options('sender'))
        assert log.action_verb == 'sent'

    def test_verb_subscribe(self):
        log = Log(make_options('subscribe'))
        assert log.action_verb == 'downloaded'

    def test_verb_sarra(self):
        log = Log(make_options('sarra'))
        assert log.action_verb == 'downloaded'

    def test_verb_post(self):
        log = Log(make_options('post'))
        assert log.action_verb == 'noticed'

    def test_verb_poll(self):
        log = Log(make_options('poll'))
        assert log.action_verb == 'noticed'

    def test_verb_watch(self):
        log = Log(make_options('watch'))
        assert log.action_verb == 'noticed'

    def test_verb_shovel(self):
        log = Log(make_options('shovel'))
        assert log.action_verb == 'shoveled'

    def test_verb_winnow(self):
        log = Log(make_options('winnow'))
        assert log.action_verb == 'winnowed'

    def test_verb_flow(self):
        log = Log(make_options('flow'))
        assert log.action_verb == 'flowed'

    def test_verb_unknown(self):
        log = Log(make_options('custom'))
        assert log.action_verb == 'done'

    def test_default_topic_separator(self):
        log = Log(make_options())
        assert log.rxTopicSeparator == '.'


class TestLogMetrics:
    def test_initial_metrics(self):
        log = Log(make_options())
        r = log.metricsReport()
        assert r['lagMax'] == 0
        assert r['lagTotal'] == 0
        assert r['lagMessageCount'] == 0
        assert r['rejectCount'] == 0

    def test_after_accept_counts(self):
        log = Log(make_options())
        wl = make_worklist()
        wl.incoming = [make_message(pub_age=5)]
        wl.rejected = [make_message(), make_message()]
        log.after_accept(wl)
        r = log.metricsReport()
        assert r['lagMessageCount'] == 1
        assert r['rejectCount'] == 2
        assert r['lagMax'] > 0
        assert r['lagTotal'] > 0

    def test_after_accept_retry_skips_lag(self):
        log = Log(make_options())
        wl = make_worklist()
        msg = make_message(pub_age=10)
        msg['_isRetry'] = True
        wl.incoming = [msg]
        log.after_accept(wl)
        assert log.lagTotal == 0
        assert log.lagMax == 0

    def test_after_work_counts(self):
        log = Log(make_options())
        wl = make_worklist()
        msg = make_message(size=1024)
        msg['new_dir'] = '/tmp'
        msg['new_file'] = 'file.txt'
        wl.ok = [msg]
        wl.rejected = [make_message()]
        log.after_work(wl)
        assert log.transferCount == 1
        assert log.fileBytes == 1024
        assert log.rejectCount == 1

    def test_after_work_no_size(self):
        log = Log(make_options())
        wl = make_worklist()
        msg = make_message(size=None)
        msg['new_dir'] = '/tmp'
        msg['new_file'] = 'file.txt'
        wl.ok = [msg]
        log.after_work(wl)
        assert log.fileBytes == 0

    def test_housekeeping_resets(self):
        log = Log(make_options())
        wl = make_worklist()
        wl.incoming = [make_message()]
        log.after_accept(wl)
        assert log.msgCount == 1
        log.on_housekeeping()
        r = log.metricsReport()
        assert r['lagMessageCount'] == 0
        assert r['rejectCount'] == 0


class TestLogMessageStr:
    def test_messageAcceptStr_basic(self):
        log = Log(make_options())
        msg = make_message()
        s = log._messageAcceptStr(msg)
        assert 'baseUrl' in s
        assert 'relPath' in s

    def test_messageAcceptStr_with_exchange(self):
        log = Log(make_options())
        msg = make_message()
        msg['exchange'] = 'xpublic'
        s = log._messageAcceptStr(msg)
        assert 'xpublic' in s

    def test_messageAcceptStr_with_subtopic(self):
        log = Log(make_options())
        msg = make_message()
        msg['subtopic'] = ['a', 'b', 'c']
        s = log._messageAcceptStr(msg)
        assert 'a.b.c' in s

    def test_messageAcceptStr_with_fileOp_link(self):
        log = Log(make_options())
        msg = make_message()
        msg['fileOp'] = {'link': '/target'}
        s = log._messageAcceptStr(msg)
        assert 'link' in s
        assert '/target' in s

    def test_messageAcceptStr_with_fileOp_rename(self):
        log = Log(make_options())
        msg = make_message()
        msg['fileOp'] = {'rename': '/old/path'}
        s = log._messageAcceptStr(msg)
        assert 'rename' in s

    def test_messageAcceptStr_with_fileOp_remove(self):
        log = Log(make_options())
        msg = make_message()
        msg['fileOp'] = {'remove': ''}
        s = log._messageAcceptStr(msg)
        assert 'remove' in s

    def test_messageAcceptStr_with_identity(self):
        log = Log(make_options())
        msg = make_message()
        msg['identity'] = {'method': 'sha512', 'value': 'abcdef1234567890'}
        s = log._messageAcceptStr(msg)
        assert 'id: abcdef1' in s

    def test_messageAcceptStr_with_sundew(self):
        log = Log(make_options())
        msg = make_message()
        msg['sundew_extension'] = 'ext'
        s = log._messageAcceptStr(msg)
        assert 'sundew_extension' in s

    def test_messageAcceptStr_with_retrievePath(self):
        log = Log(make_options())
        msg = make_message()
        msg['retrievePath'] = '/alt/path'
        s = log._messageAcceptStr(msg)
        assert 'retrievePath' in s

    def test_messagePostStr_with_posts(self):
        log = Log(make_options())
        msg = make_message()
        msg['posts'] = [{'broker': 'amqp://broker', 'topic': 'v03.data'}]
        s = log._messagePostStr(msg)
        assert 'amqp://broker' in s

    def test_messagePostStr_without_posts(self):
        log = Log(make_options())
        msg = make_message()
        s = log._messagePostStr(msg)
        assert 'nowhere?' in s

    def test_messagePostStr_with_fileOp(self):
        log = Log(make_options())
        msg = make_message()
        msg['fileOp'] = {'link': '/target'}
        msg['posts'] = [{'broker': 'amqp://b', 'topic': 'v03'}]
        s = log._messagePostStr(msg)
        assert 'link' in s

    def test_gather_returns_tuple(self):
        log = Log(make_options())
        result = log.gather(100)
        assert result == (True, [])
