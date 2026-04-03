import pytest
import os
from hashlib import md5, sha512
from base64 import b64encode

from sarracenia import Message as SR3Message
from sarracenia.flowcb.v2wrapper import sumstrFromMessage, sum_algo_v3tov2, sum_algo_v2tov3


def make_message(relPath='dir/file.txt', identity=None, fileOp=None):
    m = SR3Message()
    m['pubTime'] = '20240101T000000'
    m['baseUrl'] = 'https://example.com'
    m['relPath'] = relPath
    if identity:
        m['identity'] = identity
    if fileOp:
        m['fileOp'] = fileOp
    return m


def test_v3tov2_mapping():
    assert sum_algo_v3tov2['arbitrary'] == 'a'
    assert sum_algo_v3tov2['md5'] == 'd'
    assert sum_algo_v3tov2['sha512'] == 's'
    assert sum_algo_v3tov2['md5name'] == 'n'
    assert sum_algo_v3tov2['random'] == '0'
    assert sum_algo_v3tov2['link'] == 'L'
    assert sum_algo_v3tov2['remove'] == 'R'
    assert sum_algo_v3tov2['cod'] == 'z'


def test_v2tov3_is_inverse():
    for k, v in sum_algo_v3tov2.items():
        assert sum_algo_v2tov3[v] == k


def test_no_identity_md5name_fallback():
    msg = make_message(relPath='dir/myfile.dat')
    result = sumstrFromMessage(msg)
    expected_hash = md5(b'myfile.dat').hexdigest()
    assert result == f'n,{expected_hash}'


def test_identity_arbitrary():
    msg = make_message(identity={'method': 'arbitrary', 'value': 'some_arb_value'})
    result = sumstrFromMessage(msg)
    assert result == 'a,some_arb_value'


def test_identity_random():
    msg = make_message(identity={'method': 'random', 'value': 'random_val'})
    result = sumstrFromMessage(msg)
    assert result == '0,random_val'


def test_identity_md5():
    raw_hash = md5(b'test content').digest()
    b64_value = b64encode(raw_hash).decode('utf-8')
    hex_value = raw_hash.hex()
    msg = make_message(identity={'method': 'md5', 'value': b64_value})
    result = sumstrFromMessage(msg)
    assert result == f'd,{hex_value}'


def test_identity_sha512():
    raw_hash = sha512(b'test content').digest()
    b64_value = b64encode(raw_hash).decode('utf-8')
    hex_value = raw_hash.hex()
    msg = make_message(identity={'method': 'sha512', 'value': b64_value})
    result = sumstrFromMessage(msg)
    assert result == f's,{hex_value}'


def test_identity_cod():
    msg = make_message(identity={'method': 'cod', 'value': 'md5'})
    result = sumstrFromMessage(msg)
    assert result == 'z,d'


def test_identity_unknown_method():
    raw_hash = md5(b'something').digest()
    b64_value = b64encode(raw_hash).decode('utf-8')
    hex_value = raw_hash.hex()
    msg = make_message(relPath='dir/somefile.csv', identity={'method': 'totally_unknown', 'value': b64_value})
    result = sumstrFromMessage(msg)
    assert result == f'n,{hex_value}'


def test_fileOp_link():
    link_target = '/path/to/link_target'
    msg = make_message(fileOp={'link': link_target}, identity={'method': 'arbitrary', 'value': 'x'})
    result = sumstrFromMessage(msg)
    expected_hash = sha512(link_target.encode('utf-8')).hexdigest()
    assert result == f'L,{expected_hash}'


def test_fileOp_remove():
    msg = make_message(relPath='dir/removed.txt', fileOp={'remove': ''}, identity={'method': 'arbitrary', 'value': 'x'})
    result = sumstrFromMessage(msg)
    expected_hash = sha512(b'removed.txt').hexdigest()
    assert result == f'R,{expected_hash}'


def test_fileOp_directory_create():
    msg = make_message(relPath='some/newdir', fileOp={'directory': ''}, identity={'method': 'arbitrary', 'value': 'x'})
    result = sumstrFromMessage(msg)
    expected_hash = sha512(b'newdir').hexdigest()
    assert result == f'm,{expected_hash}'


def test_fileOp_rename_sets_oldname():
    msg = make_message(fileOp={'rename': '/old/path/file.txt'}, identity={'method': 'arbitrary', 'value': 'x'})
    sumstrFromMessage(msg)
    assert msg['oldname'] == '/old/path/file.txt'


def test_fileOp_overrides_identity():
    link_target = '/link/target'
    msg = make_message(
        identity={'method': 'sha512', 'value': b64encode(sha512(b'something').digest()).decode()},
        fileOp={'link': link_target}
    )
    result = sumstrFromMessage(msg)
    assert result.startswith('L,')
