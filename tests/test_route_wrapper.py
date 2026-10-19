"""Wrapper-level //auto_route end to end on tests/integ/routing_wrapper (pyslang only).

``wrap`` is instantiated twice (top.u_w0, top.u_w1).  Its annotations connect
its children: AUTOINST leaves instA/instB, the hand-written instance instC and
the AUTO sub-wrapper u_sub (u_sub.u_leaf).  One edit of wrap.sv must connect
the children of both wrapper instances.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.integ import is_slang_available  # noqa: E402
from pyverilog_auto.integ.design import Design  # noqa: E402
from pyverilog_auto.integ.route import RouteError  # noqa: E402
from pyverilog_auto.integ.routes_file import load_routes  # noqa: E402

import test_golden  # noqa: E402

pytestmark = pytest.mark.skipif(not is_slang_available(), reason="pyslang backend unavailable")

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE = TESTS_DIR / "integ" / "routing_wrapper"
EXPECTED = FIXTURE / "expected" / "rtl"
RTL_FILES = ["axi_if.sv", "leaf_a.sv", "leaf_b.sv", "leaf_c.sv", "leaf_x.sv", "sub.sv", "wrap.sv", "top.sv"]
LEAVES = ["axi_if.sv", "leaf_a.sv", "leaf_b.sv", "leaf_c.sv", "leaf_x.sv"]

# the annotations in rtl/wrap.sv, in file order: (LEFT, direction, targets)
ANNOTATIONS = [
    ("instA:a_data", "to", "instB:b_data"),                      # renamed ports
    ("instB:b_go", "from", "instC:c_done"),                      # from
    ("instA:a_sync", "to", "instB:b_sync, instC:c_sync"),        # fan-out
    ("instA:m_bus", "to", "instB:s_bus"),                        # interface
    ("u_sub.u_leaf:x", "to", "instC:c_x"),                       # deeper path
    (r"re:top\.u_w.\.u_sub\.u_leaf:y", "from", "instA:a_y"),     # regex LEFT, full match from the top
    ("instB:err", "to", "instC:err_in"),                         # cross-coupled pair
    ("instC:err", "to", "instB:err_in"),
]
N_SPECS = 18          # 9 connections per wrapper instance (the fan-out gives two)


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "routing_wrapper"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _design(root: Path) -> Design:
    return Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="slang")


def _norm(text: str) -> str:
    return test_golden._normalize_ws(test_golden._normalize(text))


def _snapshot(root: Path) -> dict[str, bytes]:
    return {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}


def _route(root: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pyverilog_auto", "route", "-f", str(root / "design.f"), "--relative-to", "filelist",
           *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def _inst_pins(text: str, inst: str) -> dict[str, str]:
    m = re.search(rf"\b{inst}\s*\((.*?)\);", text, re.S)
    assert m, inst
    return dict(re.findall(r"\.(\w+)\s*\(([\w\[\]:.]*)\)", m.group(1)))


def _drop_annotations(data: bytes) -> bytes:
    return b"".join(ln for ln in data.splitlines(keepends=True) if b"//auto_route" not in ln)


def _assert_wrap_connected(wrap: str) -> None:
    """Every annotation is connected in wrap's module text (shared by u_w0 and u_w1)."""
    a, b, c, s = (_inst_pins(wrap, i) for i in ("instA", "instB", "instC", "u_sub"))
    # annotation nets are named after the far end, the same in a wrapper with one
    # or many instances; a fan-out (one driver, several far ends) takes the driver's name
    assert a["a_data"] == b["b_data"] == "b_data"                       # renamed ports (to)
    assert c["c_done"] == b["b_go"] == "c_done"                         # from
    assert a["a_sync"] == b["b_sync"] == c["c_sync"] == "a_sync"        # fan-out: one net
    assert a["m_bus"] == b["s_bus"] == "s_bus"                          # interface
    assert "axi_if s_bus (.clk (clk), .rst_n (rst_n));" in wrap
    assert s["x"] == c["c_x"] == "c_x"                                  # deeper path
    assert a["a_y"] == s["y"] == "a_y"                                  # regex LEFT
    assert (b["err"], c["err_in"]) == ("instB_err", "instB_err")        # cross-coupled: two nets
    assert (c["err"], b["err_in"]) == ("instC_err", "instC_err")
    for decl in ("logic [7:0] b_data;", "logic c_done;", "logic a_sync;", "logic [3:0] c_x;", "logic a_y;",
                 "logic instB_err;", "logic instC_err;"):
        assert wrap.count(decl) == 1, decl
    # one declaration per net, and nothing else (no duplicates, no AUTOWIRE leftovers)
    nets = re.findall(r"^\s*(?:logic|wire)\s*(?:\[[^\]]*\]\s*)?(\w+);", wrap, re.M)
    nets += re.findall(r"^\s*axi_if\s+(\w+)\s*\(", wrap, re.M)
    assert sorted(nets) == sorted(["b_data", "c_done", "a_sync", "c_x", "a_y", "instB_err", "instC_err", "s_bus"])


def test_cli_collect_then_expand_matches_goldens(tmp_path):
    root = _copy(tmp_path)
    before = _snapshot(root)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{N_SPECS} spec(s), {N_SPECS} route(s)" in r.stdout and "0 warning(s)" in r.stdout
    assert "residual edits: 0" in r.stdout and "0 error(s)" in r.stdout
    for name in RTL_FILES:
        actual = (root / "rtl" / name).read_text(encoding="utf-8")
        golden = (EXPECTED / name).read_text(encoding="utf-8")
        assert _norm(actual) == _norm(golden), f"golden mismatch for {name}"
    after = _snapshot(root)
    # leaves already had every port (wrapper routes use create_dst = false): untouched
    assert {n: after[n] for n in LEAVES} == {n: before[n] for n in LEAVES}
    wrap = after["wrap.sv"].decode()
    _assert_wrap_connected(wrap)
    assert "/*AUTOINPUT*/\n   input logic clk," in wrap          # nothing left for AUTOINPUT: all inputs routed
    sub = after["sub.sv"].decode()
    assert re.search(r"output logic \[3:0\]\s+x,", sub) and re.search(r"input logic\s+y,", sub)
    assert _inst_pins(sub, "u_leaf") == {"x": "x[3:0]", "clk": "clk", "rst_n": "rst_n", "y": "y"}
    top = after["top.sv"].decode()
    assert _inst_pins(top, "u_w0") == _inst_pins(top, "u_w1") == {"clk": "clk", "rst_n": "rst_n"}

    # second run: no edits, nothing written
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 edit(s)" in r.stdout and "0 changed, 0 written" in r.stdout
    assert _snapshot(root) == after


def test_collect_only_routes_out_has_create_dst_false(tmp_path):
    root = _copy(tmp_path)
    before = _snapshot(root)
    out = tmp_path / "collected.toml"
    r = _route(root, "--collect-only", "--routes-out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"wrote {N_SPECS} route(s)" in r.stdout
    text = out.read_text()
    assert text.count("[[route]]") == N_SPECS and text.count("create_dst = false") == N_SPECS
    assert "[top.u_w1: u_sub.u_leaf:y <- top.u_w1.instA]" in text
    for w in ("u_w0", "u_w1"):              # the regex LEFT resolves once per wrapper instance
        assert f"dst  = 'top\\.{w}\\.u_sub\\.u_leaf:y'" in text
        assert f"src  = 'top\\.{w}\\.u_sub\\.u_leaf:x'" in text
    assert _snapshot(root) == before
    loaded = load_routes(str(out))
    assert loaded == _design(root).collect_auto_routes().specs
    assert not any(s.create_dst for s in loaded)
    # applying the reviewed file gives the goldens too
    r = _route(root, "--routes", str(out), "--then-expand", "--quiet")
    assert r.returncode == 0, r.stdout + r.stderr
    for name in RTL_FILES:
        assert _norm((root / "rtl" / name).read_text()) == _norm((EXPECTED / name).read_text()), name


def test_typo_target_port_is_an_error_and_nothing_written(tmp_path):
    root = _copy(tmp_path)
    p = root / "rtl" / "wrap.sv"
    data = p.read_bytes()
    assert data.count(b":: instB:b_data\n") == 1
    p.write_bytes(data.replace(b":: instB:b_data\n", b":: instB:b_dta\n"))
    before = _snapshot(root)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode != 0
    assert "E_DST_PORT_MISSING" in r.stdout + r.stderr
    assert _snapshot(root) == before
    with pytest.raises(RouteError) as ei:
        d = _design(root)
        d.apply_routes(d.collect_auto_routes().specs, then_expand=True)
    assert {dg.code for dg in ei.value.diagnostics} == {"E_DST_PORT_MISSING"}
    assert "b_data" in str(ei.value)                     # closest-name hint
    assert _snapshot(root) == before


def test_api_matches_annotations(tmp_path):
    root = _copy(tmp_path)
    collected = _design(root).collect_auto_routes()
    assert len(collected.specs) == N_SPECS and not collected.warnings
    # same specs from Python, with no annotation in the sources
    p = root / "rtl" / "wrap.sv"
    p.write_bytes(_drop_annotations(p.read_bytes()))
    d = _design(root)
    assert d.collect_auto_routes().specs == []
    api = []
    for left, direction, targets in ANNOTATIONS:
        res = d.auto_route("wrap", left, direction, targets)
        assert not res.errors and not res.warnings, left
        api += res.specs
    assert api == collected.specs
    # and they wire the design exactly like the annotations
    rep = d.apply_routes(api, then_expand=True)
    assert rep.residual == [] and not rep.warnings
    for name in RTL_FILES:
        golden = (EXPECTED / name).read_bytes()
        if name == "wrap.sv":
            golden = _drop_annotations(golden)
        assert _norm((root / "rtl" / name).read_bytes().decode()) == _norm(golden.decode()), name


_AUTO_ONLY_SUB = """\
// sub with no hand-written ports: every port comes from AUTOINPUT/AUTOOUTPUT
module sub
  (/*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   leaf_x u_leaf (/*AUTOINST*/);
endmodule
"""


# a child whose ports all come from AUTOINPUT/AUTOOUTPUT still gets pins from AUTOINST
# after the marker, so the last routed pin before /*AUTOINST*/ needs its ','
@pytest.mark.parametrize("pre_expanded", [False, True], ids=["fresh", "expanded"])
def test_routed_pins_before_autoinst_of_auto_only_child(tmp_path, pre_expanded):
    root = _copy(tmp_path)
    (root / "rtl" / "sub.sv").write_bytes(_AUTO_ONLY_SUB.encode())
    if pre_expanded:
        _design(root).expand_all()
    d = _design(root)
    d.apply_routes(d.collect_auto_routes().specs, then_expand=True)
    wrap = (root / "rtl" / "wrap.sv").read_text()
    m = re.search(r"\bu_sub\s*\((.*?)\);", wrap, re.S)
    assert m and "/*AUTOINST*/" in m.group(1) and ".clk" in m.group(1)
    before_marker = m.group(1).split("/*AUTOINST*/")[0]
    assert re.search(r"\.y\s+\(a_y\),", before_marker), before_marker


_PARAM_LEAF = """\
module leaf #(parameter W = 8)
  (input  logic         clk,
   input  logic         rst_n,
   input  logic [W-1:0] d,
   output logic [W-1:0] q);
   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) q <= '0;
     else        q <= d;
endmodule
"""

_PARAM_TOP = r"""module top #(parameter W = 8)
  (/*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   //auto_route u_leaf0:q :: to :: $top:dbg_q
   /*AUTOWIRE*/

   /* leaf AUTO_TEMPLATE "u_leaf\([0-9]+\)" (
      .d (d@[]),
      .q (q@[]),
    ); */
   leaf #(.W(W)) u_leaf0 (/*AUTOINST*/);
   leaf #(.W(W)) u_leaf1 (/*AUTOINST*/);
endmodule
"""


# a route from a parameter-width port writes the elaborated width ([7:0], not
# [W-1:0]); later runs must take that port as routed, not as a type mismatch
def test_param_width_route_to_top_is_idempotent(tmp_path):
    root = tmp_path / "param_width"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "leaf.sv").write_bytes(_PARAM_LEAF.encode())
    (root / "rtl" / "top.sv").write_bytes(_PARAM_TOP.encode())
    (root / "design.f").write_bytes(b"+libext+.sv+.v\nrtl/leaf.sv\nrtl/top.sv\n--top top\n")
    r = _route(root, "--collect", "--then-expand")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "W_PARAM_WIDTH" in out and "W_RESIDUAL" not in out and "residual edits: 0" in out
    top = (root / "rtl" / "top.sv").read_bytes()
    assert re.search(r"output logic \[7:0\] dbg_q,\s*// routed: u_leaf0\.q->top\.dbg_q", top.decode()), top.decode()
    assert _inst_pins(top.decode(), "u_leaf0")["q"] == "dbg_q"
    for _ in range(2):
        r = _route(root, "--collect", "--then-expand")
        out = r.stdout + r.stderr
        assert r.returncode == 0 and "E_TYPE_MISMATCH" not in out, out
        assert "0 edit(s)" in out and "0 changed, 0 written" in out
        assert (root / "rtl" / "top.sv").read_bytes() == top
    # a real width mismatch on the existing port is still refused, nothing written
    narrowed = top.replace(b"logic [7:0] dbg_q", b"logic [3:0] dbg_q")
    (root / "rtl" / "top.sv").write_bytes(narrowed)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 2 and "E_TYPE_MISMATCH" in r.stdout + r.stderr, r.stdout + r.stderr
    assert (root / "rtl" / "top.sv").read_bytes() == narrowed


_PARAM_2D_LEAF = """\
module leaf #(parameter W = 8, parameter N = 4)
  (input  logic                clk,
   output logic [N-1:0][W-1:0] q,
   output logic [W-1:0]        u [N]);
endmodule
"""

_PARAM_2D_SINK = """\
module sink
  (input logic                clk,
   input logic [3:0][7:0]     q_in,
   input logic [7:0]          u_in [4]);
endmodule
"""

_PARAM_2D_TOP = """\
module top
  (/*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   //auto_route u_leaf0:q :: to :: $top:dbg_q
   //auto_route u_leaf0:u :: to :: $top:dbg_u
   //auto_route u_leaf1:q :: to :: u_sink0:q_in
   //auto_route u_leaf1:u :: to :: u_sink0:u_in
   /*AUTOWIRE*/

   leaf u_leaf0 (/*AUTOINST*/);
   leaf u_leaf1 (/*AUTOINST*/);
   sink u_sink0 (/*AUTOINST*/);
endmodule
"""


# a parameter-dependent port with more than one dimension is written with all
# of its elaborated dims ([3:0][7:0], not only the outer [3:0]; unpacked [N] as
# [0:3]) on ports and nets; existing ports of that shape are taken as routed
def test_param_2d_route_writes_every_dim_and_is_idempotent(tmp_path):
    root = tmp_path / "param_2d"
    (root / "rtl").mkdir(parents=True)
    for name, text in (("leaf.sv", _PARAM_2D_LEAF), ("sink.sv", _PARAM_2D_SINK), ("top.sv", _PARAM_2D_TOP)):
        (root / "rtl" / name).write_bytes(text.encode())
    (root / "design.f").write_bytes(b"+libext+.sv+.v\nrtl/leaf.sv\nrtl/sink.sv\nrtl/top.sv\n--top top\n")
    r = _route(root, "--collect", "--then-expand")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "W_RESIDUAL" not in out and "E_TYPE_MISMATCH" not in out and "residual edits: 0" in out
    assert "elaborated width [3:0][7:0]" in out and "elaborated width [7:0] [0:3]" in out
    top = (root / "rtl" / "top.sv").read_bytes()
    text = top.decode()
    assert re.search(r"output logic \[3:0\]\[7:0\] dbg_q,\s*// routed: u_leaf0\.q->top\.dbg_q", text), text
    assert re.search(r"output logic \[7:0\] dbg_u \[0:3\],\s*// routed: u_leaf0\.u->top\.dbg_u", text), text
    assert re.search(r"^\s*logic \[3:0\]\[7:0\] q_in;", text, re.M), text
    assert re.search(r"^\s*logic \[7:0\] u_in \[0:3\];", text, re.M), text
    assert _inst_pins(text, "u_leaf0")["q"] == "dbg_q" and _inst_pins(text, "u_leaf0")["u"] == "dbg_u"
    assert _inst_pins(text, "u_leaf1")["q"] == "q_in" and _inst_pins(text, "u_sink0")["u_in"] == "u_in"
    for _ in range(2):
        r = _route(root, "--collect", "--then-expand")
        out = r.stdout + r.stderr
        assert r.returncode == 0 and "E_TYPE_MISMATCH" not in out, out
        assert "0 edit(s)" in out and "0 changed, 0 written" in out
        assert (root / "rtl" / "top.sv").read_bytes() == top
    # the same 32 bits in another shape is still refused, nothing written
    reshaped = top.replace(b"logic [3:0][7:0] dbg_q", b"logic [7:0][3:0] dbg_q")
    (root / "rtl" / "top.sv").write_bytes(reshaped)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 2 and "E_TYPE_MISMATCH" in r.stdout + r.stderr, r.stdout + r.stderr
    assert (root / "rtl" / "top.sv").read_bytes() == reshaped


_TYPED_LEAF = """\
package pkg;
  typedef enum logic [1:0] {A, B, C} e_t;
  typedef enum logic [1:0] {X, Y, Z} f_t;
  typedef logic [3:0][7:0] v_t;
endpackage

module leaf import pkg::*; #(parameter N = 4, parameter type T = logic)
  (input  logic            clk,
   output int              i,
   output pkg::e_t         e,
   output pkg::v_t         v,
   output pkg::e_t [N-1:0] ea,
   output e_t              ie,
   output T                tp);
endmodule
"""

_TYPED_SRC = """\
module src
  (input  logic    clk,
   output pkg::e_t e,
   output pkg::v_t v,
   output int      i);
endmodule
"""

_TYPED_SINK = """\
module sink import pkg::*;
  (input logic    clk,
   input e_t      e_in,
   input pkg::v_t v,
   input int      i);
endmodule
"""

_TYPED_TOP = """\
module top
  (/*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   //auto_route u_leaf0:i :: to :: $top:dbg_i
   //auto_route u_leaf0:e :: to :: $top:dbg_e
   //auto_route u_leaf0:v :: to :: $top:dbg_v
   //auto_route u_leaf0:ea :: to :: $top:dbg_ea
   //auto_route u_leaf0:ie :: to :: $top:dbg_ie
   //auto_route u_leaf0:tp :: to :: $top:dbg_tp
   //auto_route u_src0:e :: to :: u_sink0:e_in
   //auto_route u_src0:v :: to :: u_sink0:v
   //auto_route u_src0:i :: to :: u_sink0:i
   /*AUTOWIRE*/

   leaf #(.N(3), .T(logic [9:0])) u_leaf0 (/*AUTOINST*/);
   src u_src0 (/*AUTOINST*/);
   sink u_sink0 (/*AUTOINST*/);
endmodule
"""


def _write_design(root: Path, files: dict[str, str]) -> None:
    (root / "rtl").mkdir(parents=True)
    for name, text in files.items():
        (root / "rtl" / name).write_bytes(text.encode())
    (root / "design.f").write_bytes(("+libext+.sv+.v\n" + "".join(f"rtl/{n}\n" for n in files) + "--top top\n").encode())


def _slang_errors(root: Path, names) -> str:
    import pyslang
    from pyslang.ast import Compilation
    from pyslang.syntax import SyntaxTree

    comp = Compilation()
    for n in names:
        comp.addSyntaxTree(SyntaxTree.fromFile(str(root / "rtl" / n)))
    errs = [d for d in comp.getAllDiagnostics() if d.isError()]
    return pyslang.DiagnosticEngine.reportAll(comp.sourceManager, errs) if errs else ""


# a port of a named type (int, an enum, a vector typedef) is routed with that
# type, not as a 1-bit logic: package typedefs by their qualified name (an
# imported e_t too), a parameter-dependent pkg::e_t [N-1:0] and a type
# parameter with their elaborated dims; existing ports of that type are taken
# as routed, a 1-bit port of the same name is refused
def test_typed_route_writes_the_type_and_is_idempotent(tmp_path):
    root = tmp_path / "typed"
    files = {"leaf.sv": _TYPED_LEAF, "src.sv": _TYPED_SRC, "sink.sv": _TYPED_SINK, "top.sv": _TYPED_TOP}
    _write_design(root, files)
    r = _route(root, "--collect", "--then-expand")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "W_RESIDUAL" not in out and "E_TYPE_MISMATCH" not in out and "residual edits: 0" in out
    assert "elaborated width [2:0]" in out and "elaborated type logic [9:0]" in out
    top = (root / "rtl" / "top.sv").read_bytes()
    text = top.decode()
    for decl, src in (("int dbg_i", "i"), ("pkg::e_t dbg_e", "e"), ("pkg::v_t dbg_v", "v"),
                      (r"pkg::e_t \[2:0\] dbg_ea", "ea"), ("pkg::e_t dbg_ie", "ie"), (r"logic \[9:0\] dbg_tp", "tp")):
        assert re.search(rf"output {decl},\s*// routed: u_leaf0\.{src}->top\.", text), (decl, text)
    # an enum net into an enum input, and a typedef the AUTOs (no typedef regexp) would not declare
    assert re.search(r"^\s*pkg::e_t e_in;", text, re.M), text
    assert re.search(r"^\s*pkg::v_t v;", text, re.M), text
    assert re.search(r"^\s*int\s+i;", text, re.M), text
    assert _inst_pins(text, "u_src0")["e"] == "e_in" and _inst_pins(text, "u_sink0")["v"] == "v"
    assert _slang_errors(root, files) == ""
    for _ in range(2):
        r = _route(root, "--collect", "--then-expand")
        out = r.stdout + r.stderr
        assert r.returncode == 0 and "E_TYPE_MISMATCH" not in out, out
        assert "0 edit(s)" in out and "0 changed, 0 written" in out
        assert (root / "rtl" / "top.sv").read_bytes() == top
    # the 1-bit port an earlier version wrote is refused, nothing written
    one_bit = top.replace(b"output int dbg_i", b"output logic dbg_i")
    (root / "rtl" / "top.sv").write_bytes(one_bit)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 2 and "E_TYPE_MISMATCH" in r.stdout + r.stderr, r.stdout + r.stderr
    assert (root / "rtl" / "top.sv").read_bytes() == one_bit
    # ... and so is another enum of the same width (it would need a cast)
    (root / "rtl" / "top.sv").write_bytes(top)
    sink = (root / "rtl" / "sink.sv").read_bytes()
    (root / "rtl" / "sink.sv").write_bytes(sink.replace(b"input e_t      e_in", b"input f_t      e_in"))
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 2 and "E_TYPE_MISMATCH" in r.stdout + r.stderr, r.stdout + r.stderr
    assert (root / "rtl" / "top.sv").read_bytes() == top


_TYPED_NONANSI_LEAF = """\
package pkg;
  typedef enum logic [1:0] {A, B, C} e_t;
endpackage

module leaf (clk, e, ea);
   parameter N = 4;
   input  logic            clk;
   output pkg::e_t         e;
   output pkg::e_t [N-1:0] ea;
endmodule
"""

_TYPED_NONANSI_MID = """\
module mid (/*AUTOARG*/
   // Inputs
   clk
   );
   input logic clk;
   leaf #(.N(3)) u_leaf (.clk(clk));
endmodule
"""

_TYPED_NONANSI_TOP = """\
module top (input logic clk);
   //auto_route u_mid.u_leaf:e :: to :: $top:dbg_e
   //auto_route u_mid.u_leaf:ea :: to :: $top:dbg_ea
   mid u_mid (.clk(clk));
endmodule
"""

_TYPEDEF_REGEXP_LV = """
// Local Variables:
// verilog-typedef-regexp: "_t$"
// End:
"""


# the dims of a non-ANSI pkg::e_t [N-1:0] port are kept (they are part of its
# type text); a module with AUTOARG needs a typedef regexp that matches the
# routed typedef, as for structs, or AUTOARG would list pkg::e_t as a port
def test_typed_nonansi_route_keeps_dims_and_needs_typedef_regexp(tmp_path):
    root = tmp_path / "typed_nonansi"
    files = {"leaf.sv": _TYPED_NONANSI_LEAF, "mid.sv": _TYPED_NONANSI_MID, "top.sv": _TYPED_NONANSI_TOP}
    _write_design(root, files)
    r = _route(root, "--collect", "--then-expand")
    assert r.returncode == 2 and "E_TYPEDEF_REGEXP" in r.stdout + r.stderr, r.stdout + r.stderr
    assert (root / "rtl" / "mid.sv").read_text() == _TYPED_NONANSI_MID
    (root / "rtl" / "mid.sv").write_bytes((_TYPED_NONANSI_MID + _TYPEDEF_REGEXP_LV).encode())
    r = _route(root, "--collect", "--then-expand")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "W_RESIDUAL" not in out and "residual edits: 0" in out and "elaborated width [2:0]" in out
    mid = (root / "rtl" / "mid.sv").read_bytes()
    text = mid.decode()
    assert re.search(r"^\s*output pkg::e_t dbg_e;", text, re.M), text
    assert re.search(r"^\s*output pkg::e_t \[2:0\] dbg_ea;", text, re.M), text
    assert re.search(r"/\*AUTOARG\*/\s*// Outputs\s*dbg_e, dbg_ea,\s*// Inputs\s*clk", text), text
    assert re.search(r"output pkg::e_t \[2:0\] dbg_ea\b", (root / "rtl" / "top.sv").read_text())
    assert _slang_errors(root, files) == ""
    r = _route(root, "--collect", "--then-expand")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "0 edit(s)" in out and "0 changed, 0 written" in out, out
    assert (root / "rtl" / "mid.sv").read_bytes() == mid
