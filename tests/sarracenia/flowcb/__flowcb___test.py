import pytest
import sarracenia.config
import sarracenia.flowcb
from sarracenia.flowcb import FlowCB, load_library


def _make_opts():
    return sarracenia.config.default_config()


def test_flowcb_init_sets_options():
    opts = _make_opts()
    cb = FlowCB(opts)
    assert cb.o is opts


def test_flowcb_stop_requested_initially_false():
    cb = FlowCB(_make_opts())
    assert cb.stop_requested is False


def test_please_stop_sets_flag():
    cb = FlowCB(_make_opts())
    cb.please_stop()
    assert cb.stop_requested is True


def test_load_library_returns_flowcb_instance():
    """load_library with a simple plugin that needs minimal config."""
    opts = _make_opts()
    # Use httptohttps which has a simple __init__
    result = load_library('sarracenia.flowcb.accept.httptohttps.HttpToHttps', opts)
    assert isinstance(result, FlowCB)


def test_load_library_dotted_path():
    opts = _make_opts()
    result = load_library('sarracenia.flowcb.accept.sftp_absolute.Sftp_absolute', opts)
    assert result is not None


def test_load_library_destfn_replace():
    opts = _make_opts()
    opts.destfn_replace = []
    result = load_library('sarracenia.flowcb.destfn.replace.Replace', opts)
    assert isinstance(result, FlowCB)


def test_load_library_nonexistent_raises():
    opts = _make_opts()
    with pytest.raises(Exception):
        load_library('sarracenia.flowcb.nonexistent_module_xyz.NonExistent', opts)


def test_flowcb_entry_points_list_is_non_empty():
    assert len(sarracenia.flowcb.entry_points) > 0
    assert 'after_accept' in sarracenia.flowcb.entry_points
