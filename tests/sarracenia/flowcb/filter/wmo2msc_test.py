import hashlib
import os
import types

import pytest

import sarracenia
import sarracenia.config
from sarracenia.flowcb.filter.wmo2msc import Wmo2msc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_wmo2msc(tmp_path, uniquify='hash', treeify=True, convert=True):
    """Return a (Wmo2msc, options) tuple configured for *tmp_path*."""
    indir = str(tmp_path / 'input')
    outdir = str(tmp_path / 'output')
    os.makedirs(indir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)

    options = sarracenia.config.default_config()
    options.filter_wmo2msc_replace_dir = f'{indir},{outdir}'
    options.download = False
    options.filter_wmo2msc_uniquify = uniquify
    options.filter_wmo2msc_treeify = treeify
    options.filter_wmo2msc_convert = convert
    options.permDirDefault = 0o775
    options.baseDir = str(tmp_path)
    options.post_baseDir = str(tmp_path)
    options.currentDir = outdir

    w = Wmo2msc(options)
    return w, options


def _make_worklist():
    wl = types.SimpleNamespace()
    wl.ok = []
    wl.incoming = []
    wl.rejected = []
    wl.failed = []
    wl.directories_ok = []
    return wl


def _make_msg(relPath, baseUrl=None):
    m = sarracenia.Message()
    m['baseUrl'] = baseUrl or 'file:'
    m['relPath'] = relPath
    m['_deleteOnPost'] = set()
    return m


def _write_bulletin(path, ahl_line, body=b''):
    """Write a file that looks like a WMO bulletin.

    *ahl_line* should be a bytes string such as b'SACN37 CWAO 300104\\n'.
    *body* is the remainder of the file after the first line.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(ahl_line)
        f.write(body)


# ---------------------------------------------------------------------------
# replaceChar tests
# ---------------------------------------------------------------------------

class Test_replaceChar:

    def test_replaceChar_basic(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        w.bintxt = bytearray(b'hello world')
        w.replaceChar('o', 'x')
        assert w.bintxt == bytearray(b'hellx wxrld')

    def test_replaceChar_multiple_occurrences(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        w.bintxt = bytearray(b'aabaa')
        w.replaceChar('a', 'z')
        assert w.bintxt == bytearray(b'zzbzz')

    def test_replaceChar_char_not_found(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        w.bintxt = bytearray(b'hello')
        w.replaceChar('z', 'x')
        assert w.bintxt == bytearray(b'hello')

    def test_replaceChar_empty_bintxt(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        w.bintxt = bytearray(b'')
        w.replaceChar('a', 'b')
        assert w.bintxt == bytearray(b'')


# ---------------------------------------------------------------------------
# doSpecificProcessing tests
# ---------------------------------------------------------------------------

class Test_doSpecificProcessing:

    def _setup(self, tmp_path, ahl_prefix, body):
        """Set up instance with bulletin[0] starting with *ahl_prefix*.

        Returns the Wmo2msc instance after doSpecificProcessing has been called.
        """
        w, _ = _make_wmo2msc(tmp_path)
        # bulletin[0] is the first line (bytes), must be long enough for [:4]
        ahl_line = (ahl_prefix + 'XX99 CCCC 010000\n').encode('ascii')
        w.bulletin = [ahl_line, b'']
        w.bintxt = bytearray(ahl_line + body)
        w.doSpecificProcessing()
        return w

    def test_doSpecificProcessing_SD_removes_x1e(self, tmp_path):
        w = self._setup(tmp_path, 'SD', b'data\x1emore\x1eend\n')
        assert b'\x1e' not in w.bintxt

    def test_doSpecificProcessing_SR_replaces_tilde(self, tmp_path):
        w = self._setup(tmp_path, 'SR', b'line1~line2\n')
        assert b'~' not in w.bintxt
        assert b'line1\nline2\n' in w.bintxt

    def test_doSpecificProcessing_UK_removes_x01(self, tmp_path):
        w = self._setup(tmp_path, 'UK', b'data\x01more\n')
        assert b'\x01' not in w.bintxt

    def test_doSpecificProcessing_always_removes_cr(self, tmp_path):
        # Use a TT code that has no special handling (e.g. 'WC')
        w = self._setup(tmp_path, 'WC', b'line\r\nline2\r\n')
        assert b'\r' not in w.bintxt

    def test_doSpecificProcessing_trims_trailing_blanks(self, tmp_path):
        w = self._setup(tmp_path, 'WC', b'line   \nline2  \n')
        # trailing blanks before \n should be gone
        assert b'   \n' not in w.bintxt
        assert b'  \n' not in w.bintxt
        assert b'line\n' in w.bintxt


# ---------------------------------------------------------------------------
# after_work tests
# ---------------------------------------------------------------------------

class Test_after_work:

    def test_after_work_rejects_nonexistent_file(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        wl = _make_worklist()
        msg = _make_msg(str(tmp_path / 'no_such_file.txt'))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)
        assert len(wl.ok) == 0
        assert len(wl.rejected) == 1

    def test_after_work_rejects_short_ahl(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        wl = _make_worklist()
        indir = tmp_path / 'input'
        fpath = indir / 'short.txt'
        _write_bulletin(str(fpath), b'SHORT\n', b'body\n')
        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)
        assert len(wl.ok) == 0
        assert len(wl.rejected) == 1

    def test_after_work_alphanumeric_bulletin(self, tmp_path):
        w, opts = _make_wmo2msc(tmp_path)
        wl = _make_worklist()

        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'some observation data\r\n'
        fpath = indir / 'bulletin.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        assert len(wl.rejected) == 0

        out_relPath = wl.ok[0]['relPath']
        # The output relPath should contain the AHL-derived filename
        assert 'SACN37_CWAO_300104' in out_relPath
        # Tree structure: TT/CCCC/GG
        assert 'SA/CWAO/01' in out_relPath
        # output file should exist
        outfile = os.path.join(str(tmp_path), out_relPath)
        assert os.path.isfile(outfile)
        # CRs should be removed in converted output
        with open(outfile, 'rb') as f:
            data = f.read()
        assert b'\r' not in data

    def test_after_work_binary_bulletin_bufr(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        wl = _make_worklist()

        indir = tmp_path / 'input'
        ahl = b'IUAA01 CWAO 300104\n'
        body = b'BUFR' + b'\x00' * 20 + b'\r\ntrailing\r\n'
        fpath = indir / 'bufr_bulletin.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        out_relPath = wl.ok[0]['relPath']
        outfile = os.path.join(str(tmp_path), out_relPath)
        with open(outfile, 'rb') as f:
            data = f.read()
        assert b'\r' not in data

    def test_after_work_hash_uniquify(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path, uniquify='hash')
        wl = _make_worklist()

        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'unique payload data\n'
        fpath = indir / 'hash_test.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        out_relPath = wl.ok[0]['relPath']
        # filename should contain md5 hex digest
        basename = os.path.basename(out_relPath)
        parts = basename.split('_')
        # last part is the md5
        md5part = parts[-1]
        assert len(md5part) == 32
        # verify it's actually hex
        int(md5part, 16)

    def test_after_work_no_tree(self, tmp_path):
        w, opts = _make_wmo2msc(tmp_path, treeify=False)
        wl = _make_worklist()

        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'data\n'
        fpath = indir / 'notree.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        out_relPath = wl.ok[0]['relPath']
        outfile = os.path.join(str(tmp_path), out_relPath)
        assert os.path.isfile(outfile)
        # no tree: output should be in currentDir (the output dir), not in TT/CCCC/GG
        assert '/SA/CWAO/' not in outfile

    def test_after_work_savedUrl_restored(self, tmp_path):
        w, _ = _make_wmo2msc(tmp_path)
        wl = _make_worklist()

        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'data\n'
        fpath = indir / 'savedurl.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        msg['savedUrl'] = 'http://example.com/'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        assert wl.ok[0]['baseUrl'] == 'http://example.com/'

    def test_after_work_relPath_double_slash_removed(self, tmp_path):
        w, opts = _make_wmo2msc(tmp_path)
        # Arrange post_baseDir so relPath will have a double slash
        opts.post_baseDir = str(tmp_path)

        wl = _make_worklist()
        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'data\n'
        fpath = indir / 'dblslash.txt'
        _write_bulletin(str(fpath), ahl, body)

        msg = _make_msg(str(fpath))
        msg['baseUrl'] = 'file:'
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        assert '//' not in wl.ok[0]['relPath']

    def test_after_work_baseDir_path(self, tmp_path):
        """When baseUrl is not 'file:', baseDir is used to find the local file."""
        w, opts = _make_wmo2msc(tmp_path)

        indir = tmp_path / 'input'
        ahl = b'SACN37 CWAO 300104\n'
        body = b'data\n'
        fpath = indir / 'basedir_test.txt'
        _write_bulletin(str(fpath), ahl, body)

        # relPath relative to baseDir
        rel = os.path.relpath(str(fpath), str(tmp_path))
        msg = _make_msg(rel, baseUrl='http://example.com/')
        wl = _make_worklist()
        wl.ok = [msg]
        w.after_work(wl)

        assert len(wl.ok) == 1
        assert 'SACN37_CWAO_300104' in wl.ok[0]['relPath']