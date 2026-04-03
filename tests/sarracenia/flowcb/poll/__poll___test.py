import pytest
import sarracenia.config
import sarracenia.flowcb.poll
from sarracenia.flowcb.poll import file_size_fix, modstr2num


# ── file_size_fix ──────────────────────────────────────────────────────────────

def test_plain_integer():
    assert file_size_fix('1024') == 1024

def test_suffix_b_lowercase():
    assert file_size_fix('512b') == 512

def test_suffix_B_uppercase():
    assert file_size_fix('512B') == 512

def test_suffix_k_lowercase():
    assert file_size_fix('1k') == 1024

def test_suffix_K_uppercase():
    assert file_size_fix('2K') == 2048

def test_suffix_m_lowercase():
    assert file_size_fix('1m') == 1024 * 1024

def test_suffix_M_uppercase():
    assert file_size_fix('2M') == 2 * 1024 * 1024

def test_suffix_g_lowercase():
    assert file_size_fix('1g') == 1024 * 1024 * 1024

def test_suffix_G_uppercase():
    assert file_size_fix('1G') == 1024 * 1024 * 1024

def test_suffix_t_lowercase():
    assert file_size_fix('1t') == 1024 * 1024 * 1024 * 1024

def test_suffix_T_uppercase():
    assert file_size_fix('1T') == 1024 * 1024 * 1024 * 1024

def test_decimal_with_suffix():
    assert file_size_fix('1.5k') == int(1.5 * 1024)

def test_invalid_string_returns_minus_one():
    assert file_size_fix('abc') == -1

def test_empty_string_returns_minus_one():
    assert file_size_fix('') == -1

def test_zero_bytes():
    assert file_size_fix('0') == 0

def test_large_number_no_suffix():
    assert file_size_fix('1000000') == 1000000


# ── modstr2num ─────────────────────────────────────────────────────────────────

def test_rwx_is_7():
    assert modstr2num('rwx') == 7

def test_r_only_is_4():
    assert modstr2num('r--') == 4

def test_w_only_is_2():
    assert modstr2num('-w-') == 2

def test_x_only_is_1():
    assert modstr2num('--x') == 1

def test_no_permissions_is_0():
    assert modstr2num('---') == 0

def test_rw_is_6():
    assert modstr2num('rw-') == 6

def test_rx_is_5():
    assert modstr2num('r-x') == 5

def test_wx_is_3():
    assert modstr2num('-wx') == 3