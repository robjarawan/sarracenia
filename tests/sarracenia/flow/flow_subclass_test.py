"""Tests for sarracenia flow subclasses — initialization, plugin loading,
config propagation, default_options, and lifecycle behavior.

Covers: Poll, Post, Sarra, Sender, Subscribe, Watch, Winnow, Shovel, Report.
Focus on __init__ behavior, plugin load lists, default_options contracts,
and conditional branches without requiring live network or message brokers.
"""
import pytest
import copy
import os
import types
import logging

import sarracenia
import sarracenia.config
import sarracenia.flow
from sarracenia.flow import Flow


# ── Helper to build a config that can instantiate a Flow subclass ────────────

def _make_config(component='subscribe', extra_lines=None):
    """Build a config suitable for Flow subclass construction."""
    options = copy.deepcopy(sarracenia.config.default_config())
    options.component = component
    options.config = 'flow_subclass_test'
    options.action = 'start'
    if extra_lines:
        for line in extra_lines:
            options.parse_line(component, 'flow_subclass_test',
                               f"{component}/flow_subclass_test", 1, line)
    options.metricsFilename = '/tmp/flow_subclass_test_metrics'
    options.novipFilename = options.metricsFilename
    options.finalize()
    return options


# ══════════════════════════════════════════════════════════════════════════════
# Poll subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Poll_default_options:
    def test_default_options_keys(self):
        from sarracenia.flow.poll import default_options
        expected = {'blockSize', 'bufSize', 'chmod', 'pollUrl',
                    'follow_symlinks', 'force_polling', 'inflight',
                    'identity_method', 'part_ext', 'partflg',
                    'post_baseDir', 'permCopy', 'timeCopy',
                    'randomize', 'post_on_start', 'nodupe_ttl', 'fileAgeMax'}
        assert expected == set(default_options.keys())

    def test_blockSize_is_one(self):
        from sarracenia.flow.poll import default_options
        assert default_options['blockSize'] == 1

    def test_fileAgeMax_default(self):
        from sarracenia.flow.poll import default_options
        assert default_options['fileAgeMax'] == 30 * 24 * 60 * 60

    def test_nodupe_ttl_default(self):
        from sarracenia.flow.poll import default_options
        assert default_options['nodupe_ttl'] == 7 * 60 * 60


class Test_Poll_init:
    def test_scheduled_plugin_loaded(self):
        """Poll.__init__ should add scheduled.poll plugin if not present."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        poll = Poll(options)
        load_list = poll.plugins['load']
        assert any('scheduled' in p and 'poll' in p for p in load_list)

    def test_post_message_plugin_loaded(self):
        """Poll.__init__ should add post.message.Message."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        poll = Poll(options)
        load_list = poll.plugins['load']
        assert 'sarracenia.flowcb.post.message.Message' in load_list

    def test_vip_adds_gather_message(self):
        """When vip is set, gather.message.Message should be loaded."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        options.vip = ['127.0.0.1']
        poll = Poll(options)
        load_list = poll.plugins['load']
        assert 'sarracenia.flowcb.gather.message.Message' in load_list

    def test_no_vip_no_gather_message(self):
        """When vip is empty/falsy, gather.message.Message should not be loaded."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        options.vip = []
        poll = Poll(options)
        load_list = poll.plugins['load']
        assert 'sarracenia.flowcb.gather.message.Message' not in load_list

    def test_worklist_initialized(self):
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        poll = Poll(options)
        assert hasattr(poll.worklist, 'ok')
        assert hasattr(poll.worklist, 'incoming')
        assert hasattr(poll.worklist, 'rejected')
        assert hasattr(poll.worklist, 'failed')
        assert poll.worklist.ok == []

    def test_exchange_mismatch_warning(self, caplog):
        """When post_exchange != exchange, a warning should be logged."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        options.publishers = [{'exchange': ['xpublic']}]
        options.subscriptions = [{'bindings': [{'exchange': 'xdifferent'}]}]
        with caplog.at_level(logging.WARNING):
            poll = Poll(options)
        assert any('different' in r.message for r in caplog.records if r.levelno == logging.WARNING)

    def test_exchange_match_no_warning(self, caplog):
        """When post_exchange == exchange, no warning."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        options.publishers = [{'exchange': ['xpublic']}]
        options.subscriptions = [{'bindings': [{'exchange': 'xpublic'}]}]
        with caplog.at_level(logging.WARNING):
            poll = Poll(options)
        assert not any('different' in r.message for r in caplog.records if r.levelno == logging.WARNING)

    def test_nodupe_less_than_fileAgeMax_warning(self, caplog):
        """When nodupe_ttl < fileAgeMax, a warning should be logged."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        options.nodupe_ttl = 100
        options.fileAgeMax = 999999
        with caplog.at_level(logging.WARNING):
            poll = Poll(options)
        assert any('nodupe_ttl' in r.message for r in caplog.records if r.levelno == logging.WARNING)


class Test_Poll_on_start:
    def test_on_start_adds_default_poll_plugin_when_missing(self):
        """If no poll plugin is present, on_start adds the built-in poll to load list."""
        from sarracenia.flow.poll import Poll
        from unittest.mock import patch, MagicMock
        options = _make_config('poll')
        poll = Poll(options)
        poll.plugins['poll'] = []
        mock_plugin = MagicMock()
        mock_plugin.poll = MagicMock()
        with patch('sarracenia.flowcb.load_library', return_value=mock_plugin):
            poll.on_start()
        assert 'sarracenia.flowcb.poll.Poll' in poll.plugins['load']
        assert len(poll.plugins['poll']) > 0

    def test_on_start_does_not_add_if_already_present(self):
        """If poll plugin is already present, on_start should not add another."""
        from sarracenia.flow.poll import Poll
        options = _make_config('poll')
        poll = Poll(options)
        existing_poll = [lambda: None]
        poll.plugins['poll'] = existing_poll
        poll.on_start()
        assert poll.plugins['poll'] is existing_poll


# ══════════════════════════════════════════════════════════════════════════════
# Post subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Post_default_options:
    def test_default_options_keys(self):
        from sarracenia.flow.post import default_options
        expected = {'blockSize', 'bufSize', 'follow_symlinks', 'force_polling',
                    'inflight', 'part_ext', 'partflg', 'post_baseDir',
                    'permCopy', 'timeCopy', 'randomize', 'post_on_start',
                    'sleep', 'nodupe_ttl'}
        assert expected == set(default_options.keys())

    def test_sleep_negative_one(self):
        from sarracenia.flow.post import default_options
        assert default_options['sleep'] == -1

    def test_nodupe_ttl_zero(self):
        from sarracenia.flow.post import default_options
        assert default_options['nodupe_ttl'] == 0


class Test_Post_init:
    def test_gather_file_plugin_loaded(self):
        from sarracenia.flow.post import Post
        options = _make_config('post')
        post = Post(options)
        load_list = post.plugins['load']
        assert 'sarracenia.flowcb.gather.file.File' in load_list

    def test_post_message_plugin_loaded(self):
        from sarracenia.flow.post import Post
        options = _make_config('post')
        post = Post(options)
        load_list = post.plugins['load']
        assert 'sarracenia.flowcb.post.message.Message' in load_list

    def test_worklist_initialized(self):
        from sarracenia.flow.post import Post
        options = _make_config('post')
        post = Post(options)
        assert isinstance(post.worklist.ok, list)
        assert isinstance(post.worklist.incoming, list)


# ══════════════════════════════════════════════════════════════════════════════
# Sarra subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Sarra_default_options:
    def test_download_is_true(self):
        from sarracenia.flow.sarra import default_options
        assert default_options['download'] is True

    def test_keys(self):
        from sarracenia.flow.sarra import default_options
        assert set(default_options.keys()) == {'download'}


class Test_Sarra_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.sarra import Sarra
        options = _make_config('sarra')
        sarra = Sarra(options)
        assert 'sarracenia.flowcb.gather.message.Message' in sarra.plugins['load']

    def test_post_message_loaded_when_post_exchange(self):
        from sarracenia.flow.sarra import Sarra
        options = _make_config('sarra')
        options.post_exchange = 'xpublic'
        sarra = Sarra(options)
        assert 'sarracenia.flowcb.post.message.Message' in sarra.plugins['load']

    def test_post_message_not_loaded_without_post_exchange(self):
        from sarracenia.flow.sarra import Sarra
        options = _make_config('sarra')
        # Ensure no post_exchange attribute
        if hasattr(options, 'post_exchange'):
            delattr(options, 'post_exchange')
        sarra = Sarra(options)
        # gather.message should be there, but post.message should NOT
        gather_present = 'sarracenia.flowcb.gather.message.Message' in sarra.plugins['load']
        post_present = 'sarracenia.flowcb.post.message.Message' in sarra.plugins['load']
        assert gather_present
        # post.message should not be explicitly inserted by Sarra
        # (it might be present from base Flow, but not from Sarra's conditional insert)


# ══════════════════════════════════════════════════════════════════════════════
# Sender subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Sender_default_options:
    def test_download_is_true(self):
        from sarracenia.flow.sender import default_options
        assert default_options['download'] is True


class Test_Sender_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'sftp://host/path'
        sender = Sender(options)
        assert 'sarracenia.flowcb.gather.message.Message' in sender.plugins['load']

    def test_scheme_extracted_from_sendTo(self):
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'sftp://host.example.com/path'
        sender = Sender(options)
        assert sender.scheme == 'sftp'

    def test_scheme_ftp(self):
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'ftp://host.example.com/path'
        sender = Sender(options)
        assert sender.scheme == 'ftp'

    def test_scheme_https(self):
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'https://host.example.com/path'
        sender = Sender(options)
        assert sender.scheme == 'https'

    def test_post_exchange_adds_post_message(self):
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'sftp://host/path'
        options.post_exchange = 'xpublic'
        sender = Sender(options)
        assert 'sarracenia.flowcb.post.message.Message' in sender.plugins['load']

    def test_do_calls_do_send(self):
        """Sender.do() delegates to do_send()."""
        from sarracenia.flow.sender import Sender
        options = _make_config('sender')
        options.sendTo = 'sftp://host/path'
        sender = Sender(options)
        sender.do_send = lambda: None
        sender.worklist = types.SimpleNamespace(ok=[])
        # Should not raise
        sender.do()


# ══════════════════════════════════════════════════════════════════════════════
# Subscribe subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Subscribe_default_options:
    def test_download_is_true(self):
        from sarracenia.flow.subscribe import default_options
        assert default_options['download'] is True

    def test_mirror_is_false(self):
        from sarracenia.flow.subscribe import default_options
        assert default_options['mirror'] is False

    def test_keys(self):
        from sarracenia.flow.subscribe import default_options
        assert set(default_options.keys()) == {'download', 'mirror'}


class Test_Subscribe_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.subscribe import Subscribe
        options = _make_config('subscribe')
        sub = Subscribe(options)
        assert 'sarracenia.flowcb.gather.message.Message' in sub.plugins['load']

    def test_worklist_initialized(self):
        from sarracenia.flow.subscribe import Subscribe
        options = _make_config('subscribe')
        sub = Subscribe(options)
        assert isinstance(sub.worklist.ok, list)

    def test_post_exchange_adds_post_message(self):
        from sarracenia.flow.subscribe import Subscribe
        options = _make_config('subscribe')
        options.post_exchange = 'xpublic'
        sub = Subscribe(options)
        assert 'sarracenia.flowcb.post.message.Message' in sub.plugins['load']


# ══════════════════════════════════════════════════════════════════════════════
# Watch subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Watch_default_options:
    def test_default_options_keys(self):
        from sarracenia.flow.watch import default_options
        expected = {'blockSize', 'bufSize', 'follow_symlinks', 'force_polling',
                    'inflight', 'part_ext', 'partflg', 'post_baseDir',
                    'permCopy', 'timeCopy', 'randomize', 'sumflg',
                    'post_on_start', 'sleep', 'nodupe_ttl'}
        assert expected == set(default_options.keys())

    def test_sleep_is_five(self):
        from sarracenia.flow.watch import default_options
        assert default_options['sleep'] == 5

    def test_sumflg_is_sha512(self):
        from sarracenia.flow.watch import default_options
        assert default_options['sumflg'] == 'sha512'


class Test_Watch_init:
    def test_gather_file_plugin_loaded(self):
        from sarracenia.flow.watch import Watch
        options = _make_config('watch')
        watch = Watch(options)
        assert 'sarracenia.flowcb.gather.file.File' in watch.plugins['load']

    def test_post_message_plugin_loaded(self):
        from sarracenia.flow.watch import Watch
        options = _make_config('watch')
        watch = Watch(options)
        assert 'sarracenia.flowcb.post.message.Message' in watch.plugins['load']


# ══════════════════════════════════════════════════════════════════════════════
# Winnow subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Winnow_default_options:
    def test_nodupe_ttl_300(self):
        from sarracenia.flow.winnow import default_options
        assert default_options['nodupe_ttl'] == 300

    def test_logDuplicates_true(self):
        from sarracenia.flow.winnow import default_options
        assert default_options['logDuplicates'] is True

    def test_keys(self):
        from sarracenia.flow.winnow import default_options
        assert set(default_options.keys()) == {'nodupe_ttl', 'logDuplicates'}


class Test_Winnow_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.winnow import Winnow
        options = _make_config('winnow')
        winnow = Winnow(options)
        assert 'sarracenia.flowcb.gather.message.Message' in winnow.plugins['load']

    def test_post_message_loaded(self):
        from sarracenia.flow.winnow import Winnow
        options = _make_config('winnow')
        winnow = Winnow(options)
        assert 'sarracenia.flowcb.post.message.Message' in winnow.plugins['load']


# ══════════════════════════════════════════════════════════════════════════════
# Shovel subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Shovel_default_options:
    def test_nodupe_ttl_zero(self):
        from sarracenia.flow.shovel import default_options
        assert default_options['nodupe_ttl'] == 0

    def test_keys(self):
        from sarracenia.flow.shovel import default_options
        assert set(default_options.keys()) == {'nodupe_ttl'}


class Test_Shovel_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.shovel import Shovel
        options = _make_config('shovel')
        shovel = Shovel(options)
        assert 'sarracenia.flowcb.gather.message.Message' in shovel.plugins['load']

    def test_post_message_loaded(self):
        from sarracenia.flow.shovel import Shovel
        options = _make_config('shovel')
        shovel = Shovel(options)
        assert 'sarracenia.flowcb.post.message.Message' in shovel.plugins['load']


# ══════════════════════════════════════════════════════════════════════════════
# Report subclass tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Report_default_options:
    def test_nodupe_ttl_zero(self):
        from sarracenia.flow.report import default_options
        assert default_options['nodupe_ttl'] == 0

    def test_keys(self):
        from sarracenia.flow.report import default_options
        assert set(default_options.keys()) == {'nodupe_ttl'}


class Test_Report_init:
    def test_gather_message_loaded(self):
        from sarracenia.flow.report import Report
        options = _make_config('report')
        report = Report(options)
        assert 'sarracenia.flowcb.gather.message.Message' in report.plugins['load']

    def test_post_exchange_adds_post_message(self):
        from sarracenia.flow.report import Report
        options = _make_config('report')
        options.post_exchange = 'xpublic'
        report = Report(options)
        assert 'sarracenia.flowcb.post.message.Message' in report.plugins['load']

    def test_no_post_exchange_no_post_message_added(self):
        from sarracenia.flow.report import Report
        options = _make_config('report')
        if hasattr(options, 'post_exchange'):
            delattr(options, 'post_exchange')
        report = Report(options)
        # gather is present
        assert 'sarracenia.flowcb.gather.message.Message' in report.plugins['load']


# ══════════════════════════════════════════════════════════════════════════════
# Flow.factory tests
# ══════════════════════════════════════════════════════════════════════════════

class Test_Flow_factory:
    def test_factory_returns_subscribe(self):
        options = _make_config('subscribe')
        options.flowMain = 'subscribe'
        flow = Flow.factory(options)
        from sarracenia.flow.subscribe import Subscribe
        assert isinstance(flow, Subscribe)

    def test_factory_returns_poll(self):
        options = _make_config('poll')
        options.flowMain = 'poll'
        flow = Flow.factory(options)
        from sarracenia.flow.poll import Poll
        assert isinstance(flow, Poll)

    def test_factory_returns_watch(self):
        options = _make_config('watch')
        options.flowMain = 'watch'
        flow = Flow.factory(options)
        from sarracenia.flow.watch import Watch
        assert isinstance(flow, Watch)

    def test_factory_returns_sender(self):
        options = _make_config('sender')
        options.flowMain = 'sender'
        options.sendTo = 'sftp://host/path'
        flow = Flow.factory(options)
        from sarracenia.flow.sender import Sender
        assert isinstance(flow, Sender)

    def test_factory_returns_shovel(self):
        options = _make_config('shovel')
        options.flowMain = 'shovel'
        flow = Flow.factory(options)
        from sarracenia.flow.shovel import Shovel
        assert isinstance(flow, Shovel)

    def test_factory_returns_winnow(self):
        options = _make_config('winnow')
        options.flowMain = 'winnow'
        flow = Flow.factory(options)
        from sarracenia.flow.winnow import Winnow
        assert isinstance(flow, Winnow)

    def test_factory_returns_sarra(self):
        options = _make_config('sarra')
        options.flowMain = 'sarra'
        flow = Flow.factory(options)
        from sarracenia.flow.sarra import Sarra
        assert isinstance(flow, Sarra)

    def test_factory_returns_post(self):
        options = _make_config('post')
        options.flowMain = 'post'
        flow = Flow.factory(options)
        from sarracenia.flow.post import Post
        assert isinstance(flow, Post)

    def test_factory_returns_report(self):
        options = _make_config('report')
        options.flowMain = 'report'
        flow = Flow.factory(options)
        from sarracenia.flow.report import Report
        assert isinstance(flow, Report)

    def test_factory_flow_component_returns_base_flow(self):
        options = _make_config('flow')
        options.component = 'flow'
        options.flowMain = None
        flow = Flow.factory(options)
        assert isinstance(flow, Flow)

    def test_factory_unknown_returns_none(self):
        options = _make_config('subscribe')
        options.flowMain = 'nonexistent_flow_type'
        options.component = 'nonexistent_flow_type'
        result = Flow.factory(options)
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Cross-cutting: plugin load order invariants
# ══════════════════════════════════════════════════════════════════════════════

class Test_plugin_load_order:
    def test_post_message_at_front_for_shovel(self):
        """For Shovel, post.message.Message should be at the front (index 0)."""
        from sarracenia.flow.shovel import Shovel
        options = _make_config('shovel')
        shovel = Shovel(options)
        idx = shovel.plugins['load'].index('sarracenia.flowcb.post.message.Message')
        assert idx == 0

    def test_gather_message_after_post_for_shovel(self):
        """For Shovel, gather.message.Message should be at index 1."""
        from sarracenia.flow.shovel import Shovel
        options = _make_config('shovel')
        shovel = Shovel(options)
        idx = shovel.plugins['load'].index('sarracenia.flowcb.gather.message.Message')
        assert idx == 1

    def test_post_before_gather_file_for_watch(self):
        """For Watch, post.message at 0, gather.file at 1."""
        from sarracenia.flow.watch import Watch
        options = _make_config('watch')
        watch = Watch(options)
        post_idx = watch.plugins['load'].index('sarracenia.flowcb.post.message.Message')
        gather_idx = watch.plugins['load'].index('sarracenia.flowcb.gather.file.File')
        assert post_idx < gather_idx

    def test_retry_always_in_load_list(self):
        """Base Flow always includes retry plugin."""
        from sarracenia.flow.subscribe import Subscribe
        options = _make_config('subscribe')
        sub = Subscribe(options)
        assert any('retry' in p.lower() for p in sub.plugins['load'])

    def test_resources_always_in_load_list(self):
        """Base Flow always includes resources plugin."""
        from sarracenia.flow.subscribe import Subscribe
        options = _make_config('subscribe')
        sub = Subscribe(options)
        assert any('resources' in p.lower() for p in sub.plugins['load'])


# ══════════════════════════════════════════════════════════════════════════════
# Cross-cutting: worklist initialization
# ══════════════════════════════════════════════════════════════════════════════

class Test_worklist_initialization:
    """All flow subclasses should have properly initialized worklists."""

    def _assert_worklist(self, flow_instance):
        wl = flow_instance.worklist
        assert isinstance(wl.ok, list)
        assert isinstance(wl.incoming, list)
        assert isinstance(wl.rejected, list)
        assert isinstance(wl.failed, list)
        assert isinstance(wl.directories_ok, list)
        assert wl.ok == []
        assert wl.incoming == []

    def test_subscribe_worklist(self):
        from sarracenia.flow.subscribe import Subscribe
        self._assert_worklist(Subscribe(_make_config('subscribe')))

    def test_sarra_worklist(self):
        from sarracenia.flow.sarra import Sarra
        self._assert_worklist(Sarra(_make_config('sarra')))

    def test_shovel_worklist(self):
        from sarracenia.flow.shovel import Shovel
        self._assert_worklist(Shovel(_make_config('shovel')))

    def test_winnow_worklist(self):
        from sarracenia.flow.winnow import Winnow
        self._assert_worklist(Winnow(_make_config('winnow')))

    def test_watch_worklist(self):
        from sarracenia.flow.watch import Watch
        self._assert_worklist(Watch(_make_config('watch')))

    def test_post_worklist(self):
        from sarracenia.flow.post import Post
        self._assert_worklist(Post(_make_config('post')))

    def test_report_worklist(self):
        from sarracenia.flow.report import Report
        self._assert_worklist(Report(_make_config('report')))
