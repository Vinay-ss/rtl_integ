"""End-to-end tests of the AUTO strip mode.

Covers the ``strip`` subcommand, ``--strip-autos`` on ``expand``, ``diff``,
``integrate`` and ``route``, and the ``Design.expand_all(strip_autos=True)`` /
``Design.strip_autos()`` API.

Goldens: ``tests/integ/expected_stripped`` (same layout as
``tests/integ/expected``) is ``integrate --strip-autos`` on ``tests/integ``;
it must equal ``strip_autos`` of the expansion golden.  The CRLF fixtures are
generated at runtime (the repository is LF).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from pyverilog_auto.auto.strip import find_active_markers, strip_autos
from pyverilog_auto.integ import is_slang_available
from pyverilog_auto.integ.design import Design

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
INTEG = TESTS_DIR / "integ"
EXPECTED = INTEG / "expected"
STRIPPED = INTEG / "expected_stripped"
ROUTING = INTEG / "routing"
ROUTING_EXPECTED = ROUTING / "expected" / "rtl"

SOURCE_FILES = [
    "src/top.v",
    "src/core_wrap.sv",
    "src/mem_wrap.v",
    "src/if/bus_if.sv",
    "src/leaf/alu_leaf.v",
    "src/leaf/regfile_leaf.v",
    "src/leaf/sram_leaf.v",
]
LIB_FILES = ["ylib/clk_gate.v", "lib/misc_lib.v"]
STRIPPED_FILES = {"top.v", "core_wrap.sv", "mem_wrap.v", "regfile_leaf.v"}
ROUTING_FILES = ["top.v", "cluster.sv", "core.sv", "cctl.v", "mem.sv", "ctrl.sv", "dma.sv", "acc.sv", "timer.sv",
                 "axi_if.sv", "req_pkg.sv", "sim_top.sv"]

# Corpus files for ``expand``/``diff`` (none in the baseline failure list):
# AUTOARG + AUTOINST + AUTO_TEMPLATE + AUTO_LISP (lopaz, its submodule is a
# library file in the same dir), a parameterized submodule (paramover),
# AUTORESET, AUTOSENSE, and Local Variables (clog2).
CORPUS_FILES = ["autoinst_lopaz.v", "autoinst_lopaz_srpad.v", "autoinst_paramover.v", "autoinst_paramover_sub.v",
                "autoreset_cond.v", "autosense_inandout.v", "autoinst_clog2_bug522.v"]
# the submodule is listed before its parent: strip must run once, last
EXPAND_TARGETS = ["autoinst_lopaz_srpad.v", "autoinst_lopaz.v", "autoinst_paramover.v", "autoreset_cond.v",
                  "autosense_inandout.v", "autoinst_clog2_bug522.v"]


def _slang_ok() -> bool:
    if not is_slang_available():
        return False
    try:
        importlib.import_module("pyverilog_auto.integ.frontend_slang")
    except Exception:
        return False
    return True


BACKENDS = [
    "text",
    pytest.param("slang", marks=pytest.mark.skipif(not _slang_ok(), reason="pyslang backend unavailable")),
]
needs_slang = pytest.mark.skipif(not _slang_ok(), reason="pyslang backend unavailable")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO), env.get("PYTHONPATH", "")) if p)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONWARNINGS"] = "ignore"
    return env


def _cli(*args: str, cwd: Path, rc: int = 0) -> subprocess.CompletedProcess:
    """Run ``python -m pyverilog_auto ARGS`` (text output, LF-normalized)."""
    cmd = [sys.executable, "-m", "pyverilog_auto", *args]
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", env=_env())
    assert r.returncode == rc, f"{' '.join(cmd)}\nrc={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    return r


def _cli_raw(*args: str, cwd: Path) -> bytes:
    """Run the CLI and return stdout as raw bytes (no newline translation)."""
    cmd = [sys.executable, "-m", "pyverilog_auto", *args]
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, env=_env())
    assert r.returncode == 0, f"{' '.join(cmd)}\n{r.stderr[-2000:]!r}"
    return r.stdout


def _read(p: Path) -> str:
    """Exact file text (no newline translation)."""
    return p.read_bytes().decode("utf-8")


def _write_bytes(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def _age(p: Path, seconds: int = 3600) -> int:
    """Set *p*'s mtime into the past; return it (ns) so a rewrite is detectable."""
    t = int(time.time()) - seconds
    os.utime(p, (t, t))
    return p.stat().st_mtime_ns


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _copy_integ(dst: Path) -> Path:
    shutil.copytree(INTEG, dst, ignore=shutil.ignore_patterns(
        "expected", "expected_stripped", "routing", "__pycache__"))
    return dst


def _copy_routing(dst: Path) -> Path:
    shutil.copytree(ROUTING, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _copy_corpus(dst: Path) -> Path:
    dst.mkdir(parents=True)
    for name in CORPUS_FILES:
        shutil.copyfile(TESTS_DIR / name, dst / name)
    return dst


def _integ_args(root: Path) -> list[str]:
    return ["-f", str(root / "design.f"), "--relative-to", "filelist"]


def _code_tokens(text: str) -> list[str]:
    """Code tokens with comments removed (enough for these fixtures: no ``//`` in strings)."""
    t = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    t = re.sub(r"//[^\n]*", " ", t)
    return re.findall(r"[\w$`']+|\S", t)


_LEFTOVER_RE = re.compile(
    r"^\s*//\s*(?:Outputs|Inputs|Inouts|Interfaces|Interfaced|Parameters)\s*$"
    r"|Beginning of auto|End of automatics|Local Variables|AUTO_TEMPLATE|// Templated|// Implicit \.\*"
    r"|// routed:|/\*\s*AUTO",
    re.M,
)


def _assert_fully_stripped(text: str, what: str) -> None:
    assert find_active_markers(text) == [], f"active AUTO attributes left in {what}"
    m = _LEFTOVER_RE.search(text)
    assert m is None, f"AUTO leftover {m.group(0)!r} in {what}"
    assert strip_autos(text) == text, f"strip is not idempotent on {what}"


def _apply_patch(orig: str, patch: str) -> str:
    """Apply one ``difflib.unified_diff`` patch (hunks only) to *orig*."""
    src = orig.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    lines = patch.splitlines(keepends=True)
    k = 0
    while k < len(lines):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", lines[k])
        assert m, f"bad hunk header {lines[k]!r}"
        start, count = int(m.group(1)), int(m.group(2) or 1)
        at = start - 1 if count else start
        out.extend(src[i:at])
        i = at
        k += 1
        while k < len(lines) and not lines[k].startswith("@@"):
            tag, body = lines[k][:1], lines[k][1:]
            if tag == " ":
                assert src[i] == body, f"context mismatch at line {i + 1}"
                out.append(src[i])
                i += 1
            elif tag == "-":
                assert src[i] == body, f"removed line mismatch at line {i + 1}"
                i += 1
            elif tag == "+":
                out.append(body)
            k += 1
    out.extend(src[i:])
    return "".join(out)


def _split_patches(diff_text: str) -> list[tuple[str, str]]:
    """Split concatenated unified diffs into ``(path after 'a/', hunks)``, in order."""
    patches: list[tuple[str, list[str]]] = []
    lines = diff_text.splitlines(keepends=True)
    k = 0
    while k < len(lines):
        line = lines[k]
        if line.startswith("--- ") and k + 1 < len(lines) and lines[k + 1].startswith("+++ "):
            path = line[4:].rstrip("\r\n")
            path = path[2:] if path.startswith("a/") else path
            patches.append((path, []))
            k += 2
            continue
        if patches:
            patches[-1][1].append(line)
        k += 1
    return [(p, "".join(h)) for p, h in patches]


def _apply_diffs(diff_text: str, originals: dict[str, str], key) -> dict[str, str]:
    """Apply every patch in *diff_text*, in order, to ``originals[key(path)]``."""
    result = dict(originals)
    for path, hunks in _split_patches(diff_text):
        k = key(path)
        assert k in result, f"diff for an unexpected file {path}"
        result[k] = _apply_patch(result[k], hunks)
    return result


def _diag_codes(text: str, name: str) -> Counter:
    from pyslang.syntax import SyntaxTree

    tree = SyntaxTree.fromText(text, name=name)
    return Counter(str(d.code) for d in tree.diagnostics)


@pytest.fixture(scope="module")
def corpus_expanded(tmp_path_factory) -> dict[str, str]:
    """``expand`` (no strip) of EXPAND_TARGETS on a corpus copy: name -> exact text."""
    root = _copy_corpus(tmp_path_factory.mktemp("plain") / "c")
    _cli("expand", *EXPAND_TARGETS, cwd=root)
    return {name: _read(root / name) for name in EXPAND_TARGETS}


# ----------------------------------------------------------------------
# golden sanity (static)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("relpath", SOURCE_FILES)
def test_stripped_golden_is_strip_of_expansion_golden(relpath):
    expanded = _read(EXPECTED / relpath)
    golden = _read(STRIPPED / relpath)
    assert golden == strip_autos(expanded)
    _assert_fully_stripped(golden, relpath)
    assert _code_tokens(golden) == _code_tokens(expanded), f"code changed in {relpath}"
    assert (Path(relpath).name in STRIPPED_FILES) == (golden != expanded)


@pytest.mark.skipif(importlib.util.find_spec("pyslang") is None, reason="pyslang not installed")
@pytest.mark.parametrize("relpath", SOURCE_FILES)
def test_stripped_golden_parses_without_new_diagnostics(relpath):
    before = _diag_codes(_read(EXPECTED / relpath), relpath)
    after = _diag_codes(_read(STRIPPED / relpath), relpath)
    assert after - before == Counter(), f"new pyslang diagnostics in stripped {relpath}: {after - before}"


# ----------------------------------------------------------------------
# strip FILE...
# ----------------------------------------------------------------------

def test_strip_cmd_in_place_and_unchanged_files_not_rewritten(tmp_path):
    names = ["top.v", "core_wrap.sv", "alu_leaf.v"]
    src = {"top.v": "src/top.v", "core_wrap.sv": "src/core_wrap.sv", "alu_leaf.v": "src/leaf/alu_leaf.v"}
    for n in names:
        shutil.copyfile(EXPECTED / src[n], tmp_path / n)
    leaf_mtime = _age(tmp_path / "alu_leaf.v")  # nothing to strip in the leaf

    _cli("strip", *names, cwd=tmp_path)
    for n in names:
        assert (tmp_path / n).read_bytes() == (STRIPPED / src[n]).read_bytes(), n
    assert (tmp_path / "alu_leaf.v").stat().st_mtime_ns == leaf_mtime

    # a second run changes nothing and rewrites nothing
    mtimes = {n: _age(tmp_path / n) for n in names}
    _cli("strip", *names, cwd=tmp_path)
    for n in names:
        assert (tmp_path / n).stat().st_mtime_ns == mtimes[n], f"{n} rewritten although unchanged"
        assert (tmp_path / n).read_bytes() == (STRIPPED / src[n]).read_bytes(), n


def test_strip_cmd_no_save_prints_and_writes_nothing(tmp_path):
    f = tmp_path / "top.v"
    shutil.copyfile(EXPECTED / "src/top.v", f)
    mtime = _age(f)
    out = _cli_raw("strip", "--no-save", "top.v", cwd=tmp_path)
    assert out == (STRIPPED / "src/top.v").read_bytes()
    assert f.read_bytes() == (EXPECTED / "src/top.v").read_bytes()
    assert f.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in tmp_path.iterdir()) == ["top.v"]


def test_strip_cmd_keeps_crlf(tmp_path):
    lf = _read(EXPECTED / "src/top.v")
    want_lf = _read(STRIPPED / "src/top.v")
    assert "\r" not in lf
    crlf = lf.replace("\n", "\r\n")
    want = want_lf.replace("\n", "\r\n").encode("utf-8")
    f = tmp_path / "top_crlf.v"
    _write_bytes(f, crlf)

    out = _cli_raw("strip", "--no-save", f.name, cwd=tmp_path)
    assert out == want
    assert f.read_bytes() == crlf.encode("utf-8")

    _cli("strip", f.name, cwd=tmp_path)
    data = f.read_bytes()
    assert data == want
    assert b"\n" not in data.replace(b"\r\n", b""), "bare LF in a CRLF file"
    assert data.replace(b"\r\n", b"\n").decode("utf-8") == strip_autos(lf)

    mtime = _age(f)
    _cli("strip", f.name, cwd=tmp_path)
    assert f.stat().st_mtime_ns == mtime and f.read_bytes() == want


def test_strip_cmd_missing_file(tmp_path):
    r = _cli("strip", "nope.v", cwd=tmp_path, rc=1)
    assert "not found" in r.stderr


# ----------------------------------------------------------------------
# expand --strip-autos / diff --strip-autos
# ----------------------------------------------------------------------

def test_expand_strip_autos_equals_strip_of_expand(tmp_path, corpus_expanded):
    root = _copy_corpus(tmp_path / "c")
    _cli("expand", "--strip-autos", *EXPAND_TARGETS, cwd=root)
    for name in EXPAND_TARGETS:
        expanded = corpus_expanded[name]
        got = _read(root / name)
        assert find_active_markers(expanded) != [], f"{name}: nothing to strip (bad test input)"
        assert got == strip_autos(expanded), f"expand --strip-autos != strip(expand) for {name}"
        _assert_fully_stripped(got, name)
        assert _code_tokens(got) == _code_tokens(expanded), f"code changed in {name}"
    # files that were only read as submodules are left alone
    sub = "autoinst_paramover_sub.v"
    assert (root / sub).read_bytes() == (TESTS_DIR / sub).read_bytes()


def test_expand_no_save_strip_autos(tmp_path, corpus_expanded):
    root = _copy_corpus(tmp_path / "c")
    f = root / "autoinst_lopaz.v"
    mtime = _age(f)
    out = _cli("expand", "--no-save", "--strip-autos", f.name, cwd=root).stdout
    assert out == strip_autos(corpus_expanded[f.name])
    assert f.read_bytes() == (TESTS_DIR / f.name).read_bytes()
    assert f.stat().st_mtime_ns == mtime


def test_diff_strip_autos_shows_stripped_result_and_writes_nothing(tmp_path, corpus_expanded):
    root = _copy_corpus(tmp_path / "c")
    names = ["autoinst_lopaz.v", "autoreset_cond.v", "autosense_inandout.v"]
    for n in names:
        _age(root / n)
    before = _snapshot(root)
    out = _cli("diff", "--strip-autos", *names, cwd=root).stdout
    assert _snapshot(root) == before, "diff --strip-autos wrote files"

    originals = {n: _read(root / n) for n in names}
    after = _apply_diffs(out, originals, key=lambda p: Path(p).name)
    for n in names:
        assert after[n] == strip_autos(corpus_expanded[n]), f"diff + side is not the stripped result for {n}"
        _assert_fully_stripped(after[n], n)

    # without the flag the + side is the plain expansion
    plain = _apply_diffs(_cli("diff", names[0], cwd=root).stdout, originals, key=lambda p: Path(p).name)
    assert plain[names[0]] == corpus_expanded[names[0]]


# ----------------------------------------------------------------------
# integrate --strip-autos
# ----------------------------------------------------------------------

def _assert_integ_stripped(root: Path) -> None:
    for relpath in SOURCE_FILES:
        assert (root / relpath).read_bytes() == (STRIPPED / relpath).read_bytes(), f"golden mismatch for {relpath}"
        _assert_fully_stripped(_read(root / relpath), relpath)
    for relpath in LIB_FILES:  # library files are never touched
        assert (root / relpath).read_bytes() == (INTEG / relpath).read_bytes(), relpath


@pytest.mark.parametrize("backend", BACKENDS)
def test_integrate_strip_autos_matches_golden(tmp_path, backend):
    root = _copy_integ(tmp_path / "integ")
    extra = ["--no-slang"] if backend == "text" else []
    out = _cli("integrate", *_integ_args(root), "--strip-autos", *extra, cwd=tmp_path).stdout
    assert f"backend={backend}" in out
    _assert_integ_stripped(root)


def test_integrate_strip_autos_rerun_on_expanded_files_still_strips(tmp_path):
    root = _copy_integ(tmp_path / "integ")
    _cli("integrate", *_integ_args(root), "--quiet", cwd=tmp_path)
    for relpath in SOURCE_FILES:  # precondition: plain expansion golden
        assert (root / relpath).read_bytes() == (EXPECTED / relpath).read_bytes(), relpath

    out = _cli("integrate", *_integ_args(root), "--strip-autos", cwd=tmp_path).stdout
    exp_lines = [ln for ln in out.splitlines() if ln.startswith("[L")]
    assert exp_lines and all(ln.endswith(": unchanged") for ln in exp_lines), "expansion was not a no-op"
    strip_lines = [ln for ln in out.splitlines() if ln.startswith("[strip] ")]
    stripped = {Path(ln[len("[strip] "):].rsplit(": ", 1)[0]).name for ln in strip_lines
                if ln.endswith(": stripped, written")}
    assert stripped == STRIPPED_FILES
    _assert_integ_stripped(root)

    # a stripped design cannot be expanded again: a further run is a no-op
    before = _snapshot(root)
    _cli("integrate", *_integ_args(root), "--strip-autos", "--quiet", cwd=tmp_path)
    assert _snapshot(root) == before


def test_integrate_strip_autos_dry_run_and_diff_write_nothing(tmp_path):
    root = _copy_integ(tmp_path / "integ")
    before = _snapshot(root)

    out = _cli("integrate", *_integ_args(root), "--strip-autos", "--dry-run", cwd=tmp_path).stdout
    assert _snapshot(root) == before, "--dry-run wrote files"
    would = {Path(ln.split("would change:", 1)[1].strip()).name for ln in out.splitlines() if "would change:" in ln}
    assert would == STRIPPED_FILES

    out = _cli("integrate", *_integ_args(root), "--strip-autos", "--diff", "--quiet", cwd=tmp_path).stdout
    assert _snapshot(root) == before, "--diff wrote files"
    # expansion diffs, then the strip diffs: applied in order they give the stripped golden
    originals = {rp: _read(root / rp) for rp in SOURCE_FILES}
    by_name = {Path(rp).name: rp for rp in SOURCE_FILES}
    after = _apply_diffs(out, originals, key=lambda p: by_name[Path(p.replace("\\", "/")).name])
    for relpath in SOURCE_FILES:
        assert after[relpath] == _read(STRIPPED / relpath), f"--diff result mismatch for {relpath}"


# ----------------------------------------------------------------------
# Python API
# ----------------------------------------------------------------------

def _design(root: Path, backend: str) -> Design:
    return Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend=backend)


@pytest.mark.parametrize("backend", BACKENDS)
def test_api_expand_all_strip_autos(tmp_path, backend):
    root = _copy_integ(tmp_path / "integ")
    d = _design(root, backend)
    report = d.expand_all(strip_autos=True)
    assert not report.errors(), [r.error for r in report.errors()]
    strip_pass = [r for r in report.results if r.pass_no == report.passes + 1]
    assert {Path(r.path).name for r in strip_pass if r.changed and r.written} == STRIPPED_FILES
    _assert_integ_stripped(root)


@pytest.mark.parametrize("backend", BACKENDS)
def test_api_strip_autos_after_expand(tmp_path, backend):
    root = _copy_integ(tmp_path / "integ")
    d = _design(root, backend)
    assert not d.expand_all().errors()
    for relpath in SOURCE_FILES:
        assert (root / relpath).read_bytes() == (EXPECTED / relpath).read_bytes(), relpath
    changed = d.strip_autos()
    assert {p.name for p in changed} == STRIPPED_FILES
    _assert_integ_stripped(root)
    for relpath in SOURCE_FILES:  # the in-memory overlay follows the files
        assert d.file(str(root / relpath)).text == _read(STRIPPED / relpath)
    assert d.strip_autos() == []


def test_api_expand_all_strip_autos_dry_run(tmp_path):
    root = _copy_integ(tmp_path / "integ")
    before = _snapshot(root)
    d = _design(root, "text")
    report = d.expand_all(strip_autos=True, dry_run=True)
    assert not report.errors()
    assert _snapshot(root) == before, "dry run wrote files"
    for relpath in SOURCE_FILES:
        assert d.file(str(root / relpath)).text == _read(STRIPPED / relpath), relpath
    strip_pass = [r for r in report.results if r.pass_no == report.passes + 1 and r.changed]
    assert {Path(r.path).name for r in strip_pass} == STRIPPED_FILES
    assert all(r.diff and not r.written for r in strip_pass)


def test_api_strip_autos_selected_crlf_file(tmp_path):
    root = _copy_integ(tmp_path / "integ")
    top = root / "src" / "top.v"
    _write_bytes(top, _read(EXPECTED / "src/top.v").replace("\n", "\r\n"))
    d = _design(root, "text")
    changed = d.strip_autos(files=[top])
    assert [p.name for p in changed] == ["top.v"]
    assert top.read_bytes() == _read(STRIPPED / "src/top.v").replace("\n", "\r\n").encode("utf-8")
    # files= restricts the strip: the other sources keep their markers
    for relpath in SOURCE_FILES[1:]:
        assert (root / relpath).read_bytes() == (INTEG / relpath).read_bytes(), relpath


# ----------------------------------------------------------------------
# route --then-expand --strip-autos
# ----------------------------------------------------------------------

def _ws(s: str) -> str:
    return " ".join(s.split())


@needs_slang
def test_route_then_expand_strip_autos(tmp_path):
    plain = _copy_routing(tmp_path / "plain")
    _cli("route", "-f", "design.f", "--relative-to", "filelist", "--routes", "routes.toml", "--then-expand",
         "--quiet", cwd=plain)
    root = _copy_routing(tmp_path / "strip")
    out = _cli("route", "-f", "design.f", "--relative-to", "filelist", "--routes", "routes.toml", "--then-expand",
               "--strip-autos", cwd=root).stdout
    assert re.search(r"^strip: \d+ file\(s\) changed$", out, re.M), out[-500:]

    for name in ROUTING_FILES:
        got = _read(root / "rtl" / name)
        expanded = _read(plain / "rtl" / name)
        assert got == strip_autos(expanded), f"route --strip-autos != strip(route) for {name}"
        _assert_fully_stripped(got, name)
        assert _code_tokens(got) == _code_tokens(expanded), f"code changed in {name}"
        new = _diag_codes(got, name) - _diag_codes(expanded, name)
        assert new == Counter(), f"new pyslang diagnostics in stripped {name}: {new}"
        # every routed line of the golden is still there, minus its '// routed:' comment
        lines = {_ws(ln) for ln in got.splitlines()}
        for gl in _read(ROUTING_EXPECTED / name).splitlines():
            if "// routed:" in gl:
                code = _ws(gl.split("// routed:", 1)[0])
                assert code in lines, f"{name}: routed line {code!r} lost"

    top = {_ws(ln) for ln in _read(root / "rtl" / "top.v").splitlines()}
    assert ".m_axi (m_axi_0)," in top and ".m_axi (m_axi_1)," in top and ".tick (tick)," in top
    mem = _read(root / "rtl" / "mem.sv")
    assert "axi_if.slave m_axi_0," in mem and ".tick_in               (tick)," in mem


@needs_slang
def test_route_strip_autos_dry_run_writes_nothing(tmp_path):
    root = _copy_routing(tmp_path / "routing")
    before = _snapshot(root)
    out = _cli("route", "-f", "design.f", "--relative-to", "filelist", "--routes", "routes.toml", "--then-expand",
               "--strip-autos", "--dry-run", cwd=root).stdout
    assert _snapshot(root) == before, "route --dry-run --strip-autos wrote files"
    assert "(dry run)" in out and "+++ b/" in out
