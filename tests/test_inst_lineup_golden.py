"""Goldens and end-to-end tests for the instance lineup / port comments.

Goldens: ``tests/inst_lineup/<case>.sv`` -> ``tests/inst_lineup/expected/<case>.sv``
with the options set through Local Variables (``lu_*.sv`` are the
submodules / interface they instantiate, not goldens).  The expected files
were generated with this tool and reviewed by eye: ``.port``, ``(net`` and
``// comment`` each in one column across the whole connection list.

End-to-end flows (CLI in a subprocess, on copies of the fixtures):

* ``expand`` with ``--inst-*`` flags == the Local Variables route, and
  Local Variables override the flags;
* ``integrate`` on ``tests/integ`` (text and slang backends): aligned and
  idempotent; with ``--strip-autos`` the port comments stay;
* ``route --then-expand`` on ``tests/integ/routing``: routed pins aligned,
  ``// routed:`` kept after the port comment, idempotent; ``--strip-autos``;
* defaults off: the formatter is never called.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.auto.inst_lineup import is_port_comment
from pyverilog_auto.auto.strip import find_active_markers
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.integ.textscan import find_instantiations, mask_comments_and_strings, matching_close

import test_integ_golden  # noqa: E402  (tests/ is on sys.path under pytest)

REPO = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO / "tests"
FIXTURE = TESTS_DIR / "inst_lineup"
EXPECTED = FIXTURE / "expected"
CASES = sorted(p.name for p in EXPECTED.glob("*.sv"))

INTEG = TESTS_DIR / "integ"
INTEG_SOURCES = test_integ_golden.SOURCE_FILES
ROUTING = INTEG / "routing"
ROUTING_RTL = ["top.v", "cluster.sv", "core.sv", "cctl.v", "mem.sv", "ctrl.sv", "dma.sv", "acc.sv", "timer.sv",
               "axi_if.sv", "req_pkg.sv", "sim_top.sv"]

SLANG_OK = test_integ_golden._slang_ok()
BACKENDS = test_integ_golden.BACKENDS
FULL = ["--inst-lineup", "--inst-port-comment", "dir,width,type"]
DIRS = {"input", "output", "inout", "interface", "parameter"}
LV_OPT_RE = re.compile(r"^// verilog-auto-inst-(lineup|port-comment|comment-column):[ \t]*(.*)\n", re.M)

# Corpus files (Emacs goldens that pass) for the defaults-off checks.
CORPUS = ["autoinst_signed.v", "autoinst_interface.v", "autoinst_template_lint.v"]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")  # universal newlines: LF in memory


def _copy_fixture(tmp_path: Path) -> Path:
    dst = tmp_path / "inst_lineup"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _copy_tree(src: Path, dst: Path, *ignore: str) -> Path:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("expected", "__pycache__", *ignore))
    return dst


def _expand(path: Path, text: str, **cfg_kw) -> str:
    cfg = VerilogConfig(library_directories=[str(path.parent)], **cfg_kw)
    buf = VerilogBuffer.from_string(text, str(path))
    AutoEngine(cfg).run(buf, cfg)
    return buf.buffer_string()


def _cli(*args, cwd: Path) -> subprocess.CompletedProcess:
    """Run ``python -m pyverilog_auto ARGS`` with this checkout first on PYTHONPATH."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    r = subprocess.run([sys.executable, "-W", "ignore", "-m", "pyverilog_auto", *map(str, args)],
                       cwd=str(cwd), env=env, capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    return r


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _first_diff(actual: str, expected: str) -> str:
    a, e = actual.splitlines(), expected.splitlines()
    for i, (x, y) in enumerate(zip(a, e)):
        if x != y:
            return f"line {i + 1}:\n  actual:   {x!r}\n  expected: {y!r}"
    return f"line count {len(a)} != {len(e)}"


def _code_tokens(text: str) -> str:
    """The text with comments and all whitespace removed (layout-insensitive code)."""
    return re.sub(r"\s+", "", mask_comments_and_strings(text))


def _ws(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _segments(line: str) -> list[str]:
    """The ``//`` segments of a line's trailing comment."""
    i = line.find("//")
    return [] if i < 0 else re.split(r"\s+(?=//)", line[i:].rstrip())


@dataclass
class Pin:
    name: str
    line: str
    line_no: int
    dot: int
    paren: int
    comment: Optional[int]


_PIN_RE = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")


def connection_lists(text: str) -> list[tuple[str, list[Pin]]]:
    """``(instance, pins)`` per named-connection list; ``#(`` and port lists are separate."""
    masked = mask_comments_and_strings(text)
    out: list[tuple[str, list[Pin]]] = []
    for m in find_instantiations(masked, 0, len(masked)):
        spans = []
        if m.params_span:
            po = masked.index("(", m.params_span[0])
            spans.append((po, matching_close(masked, po)))
        spans.append((m.open_paren, m.close_paren))
        for o, c in spans:
            pins: list[Pin] = []
            for pm in _PIN_RE.finditer(masked, o + 1, c):
                seg = masked[o:pm.start()]
                if seg.count("(") - seg.count(")") != 1:
                    continue
                ls = masked.rfind("\n", 0, pm.start()) + 1
                le = text.find("\n", pm.start())
                line = text[ls:le if le >= 0 else len(text)]
                dot = pm.start() - ls
                cpos = line.find("//", dot)
                pins.append(Pin(pm.group(1), line, text.count("\n", 0, ls) + 1, dot,
                                pm.end() - 1 - ls, cpos if cpos >= 0 else None))
            out.append((m.inst_name, pins))
    return out


def assert_aligned(text: str, *, lineup: bool = True, where: str = "") -> int:
    """Every connection list: one pin per line, one ``.`` column, one ``(``
    column (with lineup) and one ``//`` column.  Returns the number of pins."""
    n = 0
    for inst, pins in connection_lists(text):
        if not pins:
            continue
        n += len(pins)
        tag = f"{where}:{inst} line {pins[0].line_no}"
        lines = [p.line_no for p in pins]
        assert len(set(lines)) == len(lines), f"{tag}: several pins on one line"
        assert len({p.dot for p in pins}) == 1, f"{tag}: '.' columns {sorted({p.dot for p in pins})}"
        if lineup:
            assert len({p.paren for p in pins}) == 1, f"{tag}: '(' columns {sorted({p.paren for p in pins})}"
        cols = {p.comment for p in pins if p.comment is not None}
        assert len(cols) <= 1, f"{tag}: comment columns {sorted(cols)}"
    return n


def _lv_options(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in LV_OPT_RE.finditer(text)}


def _cli_flags(opts: dict[str, str]) -> list[str]:
    flags: list[str] = []
    if opts.get("lineup") == "t":
        flags.append("--inst-lineup")
    pc = opts.get("port-comment", "nil")
    if pc != "nil":
        flags += ["--inst-port-comment", ",".join(pc.strip('"').split())]
    if "comment-column" in opts:
        flags += ["--inst-comment-column", opts["comment-column"]]
    return flags


def _port_commented(text: str) -> list[str]:
    """Pin lines that carry a port comment (in any segment)."""
    return [p.line for _, pins in connection_lists(text) for p in pins
            if any(is_port_comment(s) for s in _segments(p.line))]


def _no_trailing_ws(text: str) -> list[int]:
    return [i + 1 for i, ln in enumerate(text.splitlines()) if ln != ln.rstrip()]


# ----------------------------------------------------------------------
# 1. Goldens (Local Variables)
# ----------------------------------------------------------------------

def test_cases_collected():
    assert len(CASES) >= 7
    for case in CASES:
        assert (FIXTURE / case).is_file(), case


@pytest.mark.parametrize("case", CASES)
def test_golden(tmp_path, case):
    root = _copy_fixture(tmp_path)
    path = root / case
    expected = _read(EXPECTED / case)
    out = _expand(path, _read(path))
    assert out == expected, _first_diff(out, expected)
    # Idempotent: re-expanding the expected output (AUTOINST regenerates its pins) changes nothing.
    assert _expand(path, expected) == expected
    assert _no_trailing_ws(out) == []
    opts = _lv_options(expected)
    assert assert_aligned(out, lineup=opts.get("lineup") == "t", where=case) > 0
    # Layout and comments only: the code is the same as with the options off.
    off = _expand(path, LV_OPT_RE.sub("", _read(path)))
    assert _code_tokens(out) == _code_tokens(off)


def test_golden_sample_look():
    """The look requested for the feature (one line of the reviewed golden)."""
    lines = {_ws(ln): ln for ln in _read(EXPECTED / "autoinst_mix.sv").splitlines()}
    line = lines[".c_in (c_in[7:0]), // input [7:0] logic"]
    assert re.fullmatch(r" +\.c_in +\(c_in\[7:0\]\), +// input  \[7:0\] +logic", line)
    hand = lines[".rst_n (rst_n), // input logic // async reset"]
    assert hand.index("//") == line.index("//")
    tmpl = {_ws(ln) for ln in _read(EXPECTED / "autoinst_template.sv").splitlines()}
    assert ".c_in (cfg_byte[7:0]), // input logic // Templated" in tmpl
    iface = {_ws(ln) for ln in _read(EXPECTED / "autoinst_iface.sv").splitlines()}
    assert "lu_ifc u_ifc (.m_bus (bus.mst), // interface lu_bus_if.mst" in iface
    assert ".acc (acc[11:0]), // output [11:0] logic signed" in iface
    assert ".lanes (lanes/*[3:0].[0:3]*/)); // input [3:0][0:3] logic" in iface


# ----------------------------------------------------------------------
# 2. expand CLI flags vs Local Variables
# ----------------------------------------------------------------------

def test_expand_cli_flags_match_local_variables(tmp_path):
    root = _copy_fixture(tmp_path)
    groups: dict[tuple[str, ...], list[str]] = {}
    for case in CASES:
        text = _read(root / case)
        groups.setdefault(tuple(_cli_flags(_lv_options(text))), []).append(case)
        (root / case).write_text(LV_OPT_RE.sub("", text), encoding="utf-8", newline="\n")
    assert len(groups) >= 3
    for flags, cases in groups.items():
        assert flags, cases
        _cli("expand", "-y", root, *flags, *cases, cwd=root)
    for case in CASES:
        expected = LV_OPT_RE.sub("", _read(EXPECTED / case))
        actual = _read(root / case)
        assert actual == expected, f"{case}: " + _first_diff(actual, expected)


def test_local_variables_override_cli(tmp_path):
    root = _copy_fixture(tmp_path)
    off = root / "autoinst_mix.sv"
    off_text = re.sub(r"(inst-lineup|inst-port-comment): .*", r"\1: nil", _read(off))
    off.write_text(off_text, encoding="utf-8", newline="\n")
    width = root / "autoinst_iface.sv"
    width_text = _read(width).replace('port-comment: "dir width type"', 'port-comment: "width"')
    width.write_text(width_text, encoding="utf-8", newline="\n")
    _cli("expand", "-y", root, *FULL, "--inst-comment-column", "90", off.name, width.name, cwd=root)

    # nil in the file wins: same as expanding with every option off.
    out = _read(off)
    assert out == _expand(off, off_text)
    assert not _port_commented(out)

    # "width" in the file wins over dir,width,type; the CLI comment column still applies.
    out = _read(width)
    assert out == _expand(width, width_text, auto_inst_comment_column=90)
    pins = [p for _, ps in connection_lists(out) for p in ps]
    assert {p.comment for p in pins if p.comment is not None} == {90}
    comments = [_segments(p.line)[0] for p in pins if p.comment is not None]
    assert "// [11:0]" in comments and "// [3:0][0:3]" in comments
    assert not any(set(c.split()) & DIRS for c in comments)


# ----------------------------------------------------------------------
# 3. integrate on tests/integ
# ----------------------------------------------------------------------

def _integ_copy(tmp_path: Path, name: str = "integ") -> Path:
    return _copy_tree(INTEG, tmp_path / name, "routing")


def _integrate(root: Path, backend: str, *extra: str) -> str:
    args = ["integrate", "-f", "design.f", "--relative-to", "filelist", *FULL, *extra]
    if backend == "text":
        args.append("--no-slang")
    out = _cli(*args, cwd=root).stdout
    assert f"backend={backend}" in out
    return out


@pytest.mark.parametrize("backend", BACKENDS)
def test_integrate_aligns_every_instance_and_is_idempotent(tmp_path, backend):
    root = _integ_copy(tmp_path)
    _integrate(root, backend)
    for rel in INTEG_SOURCES:
        text = _read(root / rel)
        assert_aligned(text, where=rel)
        assert _no_trailing_ws(text) == [], rel
        # Same code as the options-off goldens: only layout and comments differ.
        assert _code_tokens(text) == _code_tokens(_read(INTEG / "expected" / rel)), rel

    top = _read(root / "src" / "top.v")
    lists = dict(connection_lists(top))
    core = lists["u_core"]
    assert len(core) == 15 and all(p.comment is not None for p in core)
    assert {_segments(p.line)[0].split()[1] for p in core} <= DIRS
    by_name = {p.name: _ws(p.line) for p in core}
    assert by_name["rdata"] == ".rdata (rdata[7:0]), // output [7:0] wire"
    assert by_name["m_bus"] == ".m_bus (m_bus.master), // interface bus_if.master"
    assert by_name["we"] == ".we (we)); // input wire"
    mem = {p.name: _ws(p.line) for p in lists["u_mem"]}
    assert mem["addr"] == ".addr (addr[AW-1:0]), // input [AW-1:0] wire"
    wrap = dict(connection_lists(_read(root / "src" / "core_wrap.sv")))
    assert any(p.comment is not None for _, pins in wrap.items() for p in pins)

    before = _snapshot(root)
    out = _integrate(root, backend)
    assert " 0 changed" in out
    assert _snapshot(root) == before


@pytest.mark.skipif(not SLANG_OK, reason="pyslang backend unavailable")
def test_integrate_backends_agree(tmp_path):
    a, b = _integ_copy(tmp_path, "a"), _integ_copy(tmp_path, "b")
    _integrate(a, "text")
    _integrate(b, "slang")
    for rel in INTEG_SOURCES:
        assert _read(a / rel) == _read(b / rel), rel


# ----------------------------------------------------------------------
# 4. route --then-expand on tests/integ/routing
# ----------------------------------------------------------------------

def _route(root: Path, *extra: str) -> str:
    return _cli("route", "-f", "design.f", "--relative-to", "filelist", "--routes", "routes.toml",
                "--then-expand", *FULL, *extra, cwd=root).stdout


@pytest.mark.skipif(not SLANG_OK, reason="pyslang backend unavailable")
def test_route_then_expand_aligns_routed_pins(tmp_path):
    root = _copy_tree(ROUTING, tmp_path / "routing")
    _route(root, "--quiet")
    routed = 0
    for name in ROUTING_RTL:
        text = _read(root / "rtl" / name)
        assert_aligned(text, where=name)
        assert _no_trailing_ws(text) == [], name
        if name != "top.v":  # top.v: see test_route_port_comments_do_not_change_code
            assert _code_tokens(text) == _code_tokens(_read(ROUTING / "expected" / "rtl" / name)), name
        for _, pins in connection_lists(text):
            for p in pins:
                segs = _segments(p.line)
                if any(s.startswith("// routed:") for s in segs):
                    routed += 1
                    # The port comment comes first, the routed segment is kept after it.
                    assert segs[0].split()[1] in DIRS, p.line
                    assert segs[-1].startswith("// routed:"), p.line
    assert routed >= 10

    mem = {_ws(p.line) for _, pins in connection_lists(_read(root / "rtl" / "mem.sv")) for p in pins}
    assert ".tick_in (tick), // input logic // routed: tick" in mem
    assert ".cfg_o (cfg) // output [3:0] logic // routed: cfg" in mem

    before = _snapshot(root)
    out = _route(root)
    assert " 0 edit(s)" in out and " 0 changed" in out
    assert _snapshot(root) == before


@pytest.mark.skipif(not SLANG_OK, reason="pyslang backend unavailable")
# regression: Design._decls_cache must be keyed by the caller's verilog-typedef-regexp too;
# the port-comment lookup in sim_top.sv used to parse mem first without top.v's regexp,
# so top.v saw 'input req_pkg::req_t req' as an interface and AUTOOUTPUT exported it
def test_route_port_comments_do_not_change_code(tmp_path):
    root = _copy_tree(ROUTING, tmp_path / "routing")
    _route(root, "--quiet")
    for name in ROUTING_RTL:
        assert _code_tokens(_read(root / "rtl" / name)) == \
            _code_tokens(_read(ROUTING / "expected" / "rtl" / name)), name


# ----------------------------------------------------------------------
# 5. --strip-autos together with the port comments
# ----------------------------------------------------------------------

_AUTO_LEFTOVERS = ("// Templated", "// routed:", "// Implicit .*", "/*AUTO", "// Beginning of automatic",
                   "// End of automatics", "Local Variables", "AUTO_TEMPLATE", "//auto_route")


def _assert_stripped(text: str, where: str) -> None:
    assert find_active_markers(text) == [], where
    for s in _AUTO_LEFTOVERS:
        assert s not in text, f"{where}: {s!r} left"
    assert _no_trailing_ws(text) == [], where
    assert_aligned(text, where=where)


@pytest.mark.parametrize("backend", BACKENDS)
def test_integrate_strip_keeps_port_comments(tmp_path, backend):
    root = _integ_copy(tmp_path)
    _integrate(root, backend, "--strip-autos")
    for rel in INTEG_SOURCES:
        text = _read(root / rel)
        _assert_stripped(text, rel)
        assert _code_tokens(text) == _code_tokens(_read(INTEG / "expected" / rel)), rel
    lists = dict(connection_lists(_read(root / "src" / "top.v")))
    core = {p.name: _ws(p.line) for p in lists["u_core"]}
    assert len(core) == 15
    assert core["y"] == ".y (y[WIDTH-1:0]), // output [WIDTH-1:0] wire signed"
    assert all(_segments(ln)[0].split()[1] in DIRS for ln in core.values())


@pytest.mark.skipif(not SLANG_OK, reason="pyslang backend unavailable")
def test_route_strip_keeps_port_comments(tmp_path):
    root = _copy_tree(ROUTING, tmp_path / "routing")
    _route(root, "--quiet", "--strip-autos")
    for name in ROUTING_RTL:
        _assert_stripped(_read(root / "rtl" / name), name)
    mem = {_ws(p.line) for _, pins in connection_lists(_read(root / "rtl" / "mem.sv")) for p in pins}
    assert ".tick_in (tick), // input logic" in mem
    assert ".cfg_o (cfg) // output [3:0] logic" in mem


# ----------------------------------------------------------------------
# 6. Defaults off
# ----------------------------------------------------------------------

def _no_formatter(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("lineup formatter called with the options off")

    monkeypatch.setattr(AutoEngine, "_lineup_instances", staticmethod(boom))


@pytest.mark.parametrize("name", CORPUS)
def test_defaults_off_corpus_never_formats(monkeypatch, name):
    path = TESTS_DIR / name
    text = _read(path)
    with_column = _expand(path, text, auto_inst_comment_column=60)  # column alone enables nothing
    _no_formatter(monkeypatch)
    assert _expand(path, text) == with_column


def test_defaults_off_goldens_never_format(monkeypatch, tmp_path):
    root = _copy_fixture(tmp_path)
    _no_formatter(monkeypatch)
    for case in CASES:
        out = _expand(root / case, LV_OPT_RE.sub("", _read(root / case)))
        assert not _port_commented(out), case
