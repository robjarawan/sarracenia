import sys
import types

import sarracenia.config
import sarracenia.flowcb.clamav


def test_constructor_uses_registered_clamd_feature(monkeypatch):
    scanner = object()
    pyclamd = types.SimpleNamespace(ClamdAgnostic=lambda: scanner)
    monkeypatch.setitem(sys.modules, 'pyclamd', pyclamd)
    monkeypatch.setitem(sarracenia.features['clamd'], 'present', True)

    callback = sarracenia.flowcb.clamav.Clamav(sarracenia.config.no_file_config())

    assert callback.av is scanner


def test_constructor_allows_unavailable_clamd_feature(monkeypatch):
    monkeypatch.setitem(sarracenia.features['clamd'], 'present', False)

    callback = sarracenia.flowcb.clamav.Clamav(sarracenia.config.no_file_config())

    assert not hasattr(callback, 'av')
