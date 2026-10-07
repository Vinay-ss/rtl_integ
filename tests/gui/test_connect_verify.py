"""Connectivity index and leaf-equivalence verifier."""

from __future__ import annotations

import os

import pytest

from pyverilog_auto.gui.build import Builder
from pyverilog_auto.gui.connect import module_index
from pyverilog_auto.gui.project import load_project
from pyverilog_auto.gui.verify import compare, leaf_partition, rename_path, verify_designs
from pyverilog_auto.integ.design import Design


def _design(proj_root):
    res = Builder(load_project(str(proj_root))).build()
    assert res.ok, [str(d) for d in res.diagnostics]
    return res.design


def _files(tmp_path, files: dict[str, str]) -> Design:
    paths = []
    for name, text in files.items():
        p = tmp_path / name
        p.write_text(text)
        paths.append(str(p))
    return Design.from_files(paths, backend="slang")


LEAF = """module leaf (input logic [7:0] d, output logic [7:0] q, output logic busy);
  assign q = d; assign busy = |d;
endmodule
"""


def test_index_pins_and_uses(proj_py, slang):
    d = _design(proj_py)
    ix = module_index(d, "top")
    assert list(ix.insts) == ["u_a", "u_b", "u_c", "u_d", "u_lane0", "u_lane1"]
    u_b = ix.insts["u_b"]
    assert [(p.port, p.expr, p.direction) for p in u_b.pins] == [
        ("clk", "clk", "input"), ("rst_n", "rst_n", "input"), ("d", "a2b", "input"),
        ("q", "b2c", "output"), ("busy", "b_busy", "output")]
    assert ix.insts["u_c"].params == {"W": "8"}
    owners = ix.inst_owners(["u_a", "u_b"])
    assert not ix.used_outside("a2b", owners)
    assert ix.used_outside("b2c", owners)       # read by u_c
    assert ix.used_outside("b_busy", owners)    # assign busy = b_busy
    assert ix.is_port("din") and not ix.is_port("a2b")
    a2b = ix.decls["a2b"]
    assert (a2b.kind, a2b.type_text, a2b.dims, a2b.full_type) == ("var", "logic", "[7:0]", "logic [7:0]")
    assert ix.text[a2b.decl_start:a2b.decl_end] == "a2b"


def test_index_autoinst_and_interfaces(tmp_path, slang):
    d = _files(tmp_path, {
        "ifc.sv": "interface ifc; logic v; modport m(output v); modport s(input v); endinterface\n",
        "src.sv": "module src (ifc.m bus, output logic [3:0] x); assign bus.v = 1'b1; assign x = '0; endmodule\n",
        "top.sv": "module top (output logic [3:0] o);\n  ifc bus_i ();\n  src u_s (.bus(bus_i), .x(o[3:0]));\n"
                  "  src u_t (.bus(bus_i), .x({o[1:0], o[3:2]}));\nendmodule\n",
    })
    ix = module_index(d, "top")
    assert ix.decls["bus_i"].kind == "iface_inst"
    assert ix.insts["u_s"].pin("bus").direction is None
    assert ix.insts["u_s"].pin("x").names == ["o"]
    assert ix.insts["u_t"].pin("x").names == ["o", "o"]


def test_index_dotstar_and_implicit(tmp_path, slang):
    d = _files(tmp_path, {
        "leaf.sv": LEAF,
        "top.sv": "module top (input logic [7:0] d, output logic [7:0] q, output logic busy);\n"
                  "  leaf u_l (.*);\n  leaf u_m (.d, .q(), .busy());\nendmodule\n",
    })
    ix = module_index(d, "top")
    assert sorted((p.port, p.implicit) for p in ix.insts["u_l"].pins) == [("busy", True), ("d", True), ("q", True)]
    assert ix.insts["u_m"].pin("d").names == ["d"] and ix.insts["u_m"].pin("d").implicit


def test_leaf_partition_and_rename(tmp_path, slang):
    flat = _files(tmp_path, {
        "leaf.sv": LEAF,
        "top.sv": "module top (input logic [7:0] i, output logic [7:0] o);\n  logic [7:0] m;\n"
                  "  leaf u_a (.d(i), .q(m), .busy());\n  leaf u_b (.d(m), .q(o), .busy());\nendmodule\n",
    })
    (tmp_path / "w").mkdir()
    wrapped = _files(tmp_path / "w", {
        "leaf.sv": LEAF,
        "wrap.sv": "module wrap (input logic [7:0] i, output logic [7:0] o);\n  logic [7:0] m;\n"
                   "  leaf u_a (.d(i), .q(m), .busy());\n  leaf u_b (.d(m), .q(o), .busy());\nendmodule\n",
        "top.sv": "module top (input logic [7:0] i, output logic [7:0] o);\n  wrap u_w (.i(i), .o(o));\nendmodule\n",
    })
    part = leaf_partition(flat)
    assert frozenset({("top.u_a", "q"), ("top.u_b", "d")}) in part
    assert frozenset({("top", "i"), ("top.u_a", "d")}) in part
    renames = {"top.u_a": "top.u_w.u_a", "top.u_b": "top.u_w.u_b"}
    assert verify_designs(flat, wrapped, renames).ok
    res = verify_designs(flat, wrapped, {})
    assert not res.ok and "before" in res.describe()


def test_verify_detects_rewire(tmp_path, slang):
    a = _files(tmp_path, {
        "leaf.sv": LEAF,
        "top.sv": "module top (input logic [7:0] i, output logic [7:0] o);\n"
                  "  leaf u_a (.d(i), .q(o), .busy());\nendmodule\n",
    })
    (tmp_path / "b").mkdir()
    b = _files(tmp_path / "b", {
        "leaf.sv": LEAF,
        "top.sv": "module top (input logic [7:0] i, output logic [7:0] o);\n"
                  "  leaf u_a (.d(i), .q(), .busy());\nendmodule\n",
    })
    res = verify_designs(a, b)
    assert not res.ok
    assert frozenset({("top", "o"), ("top.u_a", "q")}) in res.missing


def test_rename_path_longest_prefix():
    r = {"top.a": "top.w.a", "top.a.b": "top.x"}
    assert rename_path("top.a", r) == "top.w.a"
    assert rename_path("top.a.c", r) == "top.w.a.c"
    assert rename_path("top.a.b.c", r) == "top.x.c"
    assert rename_path("top.ab", r) == "top.ab"
    assert compare({frozenset({("top.a", "p"), ("top", "q")})}, {frozenset({("top.w.a", "p"), ("top", "q")})},
                   {"top.a": "top.w.a"}).ok


def test_fixture_partition_is_stable(proj_py, slang):
    d = _design(proj_py)
    p1 = leaf_partition(d)
    # "assign busy = b_busy" aliases the two nets
    assert frozenset({("top.u_b", "busy"), ("top", "busy")}) in p1
    assert frozenset({("top.u_c", "q"), ("top.u_d", "d")}) in p1
    assert any(("top.u_lane0", "d") in c and ("top.u_a", "d") in c and ("top", "din") in c for c in p1)
    assert os.path.isdir(str(proj_py / "build"))
