"""Configuration-level checks of ordinary local-file download integrity."""
import pytest

from tests.reliability.file_download_proof import run_case


@pytest.mark.parametrize('announced,accept_wrong', [(10, False), (3, False), (3, True)])
def test_file_download_respects_whole_file_and_size_policy(tmp_path, monkeypatch, announced, accept_wrong):
    # Flow changes its process working directory; pytest restores it after the case.
    monkeypatch.chdir(tmp_path)
    outcome = run_case(tmp_path / 'case', announced, accept_wrong)
    assert outcome['destination_bytes'] == 10
    assert outcome['accepted'] == (1 if announced == 10 or accept_wrong else 0)
    assert not outcome['accepted_truncated']
