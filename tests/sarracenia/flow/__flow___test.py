import pytest
from tests.conftest import *

import sarracenia.config
import sarracenia.flow
import copy

__COMPONENT="subscribe"
__CONFIG="flow_class_test"

def __make_fake_config(lines=[]):
    """ build and return a fake config object
    """
    options = copy.deepcopy(sarracenia.config.default_config())
    options.component = __COMPONENT
    options.config = __CONFIG
    options.action = 'start'
    for line in lines:
        options.parse_line(__COMPONENT, __CONFIG, f"{__COMPONENT}/{__CONFIG}", 1, line)
    options.metricsFilename = '/tmp/fake_filename_nothing_here'
    options.novipFilename = options.metricsFilename
    options.acceptUnmatched = False
    options.finalize()
    return options


def test_msg_accepted_with_sundew_extension():
    """
    Regression test for https://github.com/MetPX/sarracenia/issues/1573
    sundew_extension should be included in filtering, even when URL has a port
    """
    options = __make_fake_config(lines=["accept .*rxer:CCCC:TT:3:Direct.*"])

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'http://dms-dev1.domain:8180/data/msc/'
    msg['relPath'] = 'forecast/atmospheric/aviation/file.txt'
    msg['sundew_extension'] = 'rxer:CCCC:TT:3:Direct'

    flow.worklist.incoming.append(msg)

    # based on the accept statement, the message should only be accepted when the sundew_extension is correctly added
    flow.filter()
    assert(len(flow.worklist.rejected) == 0)
    assert(msg not in flow.worklist.rejected)
    assert(msg in flow.worklist.incoming)

def test_msg_rejected_when_sundew_extension_already_present():
    """
    If the path itself already contains a different sundew extension,
    the sundew_extension header in the msg should not be used for filtering
    """
    options = __make_fake_config(lines=["accept .*rxer:CCCC:TT:3:Direct.*"])

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'http://dms-dev1.domain:8180/data/msc/'
    msg['relPath'] = 'forecast/atmospheric/aviation/file.txt:something:CWAO:SA:3:Direct'
    msg['sundew_extension'] = 'rxer:CCCC:TT:3:Direct'

    flow.worklist.incoming.append(msg)

    # msg already has a different sundew extension that does not match the accept, it should be rejected
    flow.filter()

    # worklist.rejected gets acked and set to [] at the end of filter
    assert(len(flow.worklist.incoming) == 0)
    assert(len(flow.worklist.rejected) == 0)
    assert(msg not in flow.worklist.incoming)


@pytest.mark.parametrize('preceding_rule', [
    None,
    'reject file:/root/elsewhere/.*',
    'accept file:/root/elsewhere/.*',
])
def test_rename_old_path_checks_later_matching_mask(preceding_rule):
    """A rename out of an accepted path must remove the old local copy."""
    lines = ['accept file:/root/kept/.*']
    if preceding_rule:
        lines.insert(0, preceding_rule)
    options = __make_fake_config(lines=lines)

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'file:/root'
    msg['relPath'] = 'outside/item.bin'
    msg['fileOp'] = {'rename': 'kept/item.bin'}
    flow.worklist.incoming.append(msg)

    flow.filter()

    assert msg.get('renameUnlink') is True
    assert msg in flow.worklist.incoming


def test_rename_old_path_stops_at_first_matching_reject():
    """A matching reject must take precedence over a later accept."""
    options = __make_fake_config(lines=[
        'reject file:/root/kept/.*',
        'accept file:/root/kept/.*',
    ])

    flow = sarracenia.flow.Flow(options)
    flow.have_vip = True
    msg = sarracenia.Message()
    msg['pubTime'] = '20260101T010203.123'
    msg['baseUrl'] = 'file:/root'
    msg['relPath'] = 'outside/item.bin'
    msg['fileOp'] = {'rename': 'kept/item.bin'}
    flow.worklist.incoming.append(msg)

    flow.filter()

    assert 'renameUnlink' not in msg
    assert msg not in flow.worklist.incoming
