"""Tests for pyverilog_auto.integ.design (both backends)."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from pyverilog_auto.integ import is_slang_available
from pyverilog_auto.integ.design import Design, DesignError


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

TOP_SV = """\
module top (input logic clk, input logic rst_n, output logic [7:0] q);
   /*AUTOWIRE*/
   bus_if bus (.clk(clk));
   logic [7:0] q_arr [0:1];
   genvar i;
   for (i = 0; i < 2; i++) begin : gen_m
      mid u_mid (.clk(clk), .rst_n(rst_n), .m(bus), .q(q_arr[i]));
   end
   mid u_single (/*AUTOINST*/);
   phy u_phy (.a(clk), .b(q[0]));
   /*AUTOINOUTMODULE("other")*/
   assign q = q_arr[0];
endmodule
"""

MID_SV = """\
module mid (input logic clk, input logic rst_n, bus_if.master m, output logic [7:0] q);
   leaf #(.W(8)) u_leaf (.clk(clk), .d(8'd1), .q(q));
   leaf u_arr [1:0] (.clk(clk), .d(8'd2), .q());
   inv_cell u_cell (.a(clk), .y());
endmodule
"""

LEAF_SV = """\
module leaf #(parameter W = 8) (input logic clk, input logic [W-1:0] d, output logic [W-1:0] q);
   always_ff @(posedge clk) q <= d;
endmodule

module other (input logic x, output logic y);
   assign y = x;
endmodule
"""

BUS_IF_SV = """\
interface bus_if (input logic clk);
   logic req;
   logic ack;
   modport master (output req, input ack);
   modport slave (input req, output ack);
endinterface
"""

CELL_V = """\
module inv_cell (input a, output y);
   assign y = ~a;
endmodule
"""


def _write_design(root: Path) -> Path:
    (root / "rtl").mkdir()
    (root / "ylib").mkdir()
    (root / "rtl" / "top.sv").write_text(TOP_SV)
    (root / "rtl" / "mid.sv").write_text(MID_SV)
    (root / "rtl" / "leaf.sv").write_text(LEAF_SV)
    (root / "rtl" / "bus_if.sv").write_text(BUS_IF_SV)
    (root / "ylib" / "inv_cell.v").write_text(CELL_V)
    f = root / "design.f"
    f.write_text("// test design\n+libext+.v+.sv\n-y ylib\nrtl/top.sv\nrtl/mid.sv\nrtl/leaf.sv\nrtl/bus_if.sv\n")
    return f


def _name(design: Design, key: str) -> str:
    return Path(design.files[key].path).name


@pytest.mark.parametrize("backend", BACKENDS)
def test_modules_files_and_order(tmp_path, backend):
    f = _write_design(tmp_path)
    d = Design.from_filelist(str(f), relative_to="filelist", backend=backend)
    assert d.backend == backend
    assert set(d.modules) == {"top", "mid", "leaf", "other", "bus_if", "inv_cell"}
    # -y discovered file is a library
    cell_sf = d.files[d.modules["inv_cell"].file]
    assert cell_sf.role == "library"
    assert d.modules["inv_cell"].is_library
    assert Path(cell_sf.path).name == "inv_cell.v"
    # multi-module file
    assert d.files[d.modules["leaf"].file].modules == ["leaf", "other"]
    # unresolved black box
    assert "phy" in d.unresolved
    order = d.order()
    names = [_name(d, k) for k in order.files]
    assert names.index("leaf.sv") < names.index("mid.sv") < names.index("top.sv")
    assert names.index("bus_if.sv") < names.index("mid.sv")
    assert names.index("inv_cell.v") < names.index("mid.sv")
    assert order.level_of[d.modules["top"].file] == 2
    assert order.cycles == []
    # AUTOINOUTMODULE("other") creates an edge top -> leaf.sv (where other lives)
    deps = {_name(d, k) for k in d.dependencies(d.modules["top"].file)}
    assert "leaf.sv" in deps and "mid.sv" in deps and "bus_if.sv" in deps


@pytest.mark.parametrize("backend", BACKENDS)
def test_module_facts(tmp_path, backend):
    f = _write_design(tmp_path)
    d = Design.from_filelist(str(f), relative_to="filelist", backend=backend)
    mid = d.modules["mid"]
    assert mid.ansi
    ports = {p.name: p for p in mid.ports}
    assert ports["clk"].direction == "input"
    assert ports["q"].direction == "output" and ports["q"].packed_dims == "[7:0]"
    assert ports["m"].is_interface and ports["m"].iface_type == "bus_if" and ports["m"].modport == "master"
    insts = {r.inst_name: r for r in mid.refs if r.kind == "inst"}
    assert set(insts) == {"u_leaf", "u_arr", "u_cell"}
    assert insts["u_arr"].is_array and insts["u_arr"].array_dims == "[1:0]"
    assert insts["u_leaf"].param_overrides == {"W": "8"}
    assert insts["u_leaf"].connections_style == "named"
    assert [p.port for p in insts["u_leaf"].explicit_pins] == ["clk", "d", "q"]
    assert not insts["u_leaf"].uses_autoinst
    top = d.modules["top"]
    tinsts = {r.inst_name: r for r in top.refs if r.kind == "inst"}
    assert tinsts["u_single"].uses_autoinst
    assert tinsts["u_single"].connections_style == "empty"
    assert tinsts["u_mid"].in_generate
    assert "AUTOWIRE" in top.markers and top.markers["AUTOWIRE"][0].fence_range is None
    assert any(r.kind == "iface_port" and r.module == "bus_if" for r in mid.refs)
    assert any(r.kind == "auto" and r.module == "other" for r in top.refs)
    leaf = d.modules["leaf"]
    assert "W" in leaf.params
    lp = {p.name: p for p in leaf.ports}
    assert lp["d"].packed_dims == "[W-1:0]" and lp["d"].type_text == "logic"


@pytest.mark.parametrize("backend", BACKENDS)
def test_hierarchy_queries(tmp_path, backend):
    f = _write_design(tmp_path)
    d = Design.from_filelist(str(f), relative_to="filelist", backend=backend)
    roots = d.hierarchy()
    root_names = sorted(r.name for r in roots)
    assert "top" in root_names
    paths = {i.path for i in d.all_instances()}
    assert "top.u_single" in paths and "top.u_single.u_leaf" in paths
    assert "top.u_phy" in paths
    assert d.instance("top.u_phy").is_blackbox
    assert d.instance("top.bus").is_interface
    if backend == "slang":
        assert "top.gen_m[0].u_mid" in paths and "top.gen_m[1].u_mid.u_leaf" in paths
        assert "top.u_single.u_arr[0]" in paths and "top.u_single.u_arr[1]" in paths
    else:
        assert "top.u_mid" in paths
        assert "top.u_single.u_arr[1:0]" in paths
    found = d.find_instances(r"\.u_leaf$")
    assert all(i.module_name == "leaf" for i in found) and len(found) >= 2
    a = d.instance("top.u_single.u_leaf")
    b = d.instance("top.u_single.u_cell")
    assert d.lca(a, b).path == "top.u_single"
    assert [i.path for i in d.chain("top", a)] == ["top", "top.u_single", "top.u_single.u_leaf"]
    assert d.lca(a, d.instance("top.u_phy")).path == "top"
    assert len(d.instances_of("leaf")) >= 3
    with pytest.raises(DesignError):
        d.chain(a, b)
    j = d.to_json()
    json.dumps(j)
    assert j["backend"] == backend
    assert "phy" in j["unresolved"]
    assert len(j["levels"]) == 3


@pytest.mark.parametrize("backend", BACKENDS)
def test_top_filter_and_config(tmp_path, backend):
    f = _write_design(tmp_path)
    d = Design.from_filelist(str(f), relative_to="filelist", backend=backend, top="mid")
    roots = d.hierarchy()
    assert [r.name for r in roots] == ["mid"]
    cfg = d.make_config(d.modules["top"].file)
    assert any(os.path.normcase(x) == os.path.normcase(str(tmp_path / "rtl")) for x in cfg.library_directories)
    assert cfg.library_extensions == [".v", ".sv"]
    assert d.make_config() is not d.make_config()   # fresh copies


def test_duplicate_definition_warns(tmp_path):
    (tmp_path / "a.v").write_text("module m; endmodule\n")
    (tmp_path / "b.v").write_text("module m; endmodule\n")
    d = Design.from_files([str(tmp_path / "a.v"), str(tmp_path / "b.v")], backend="text")
    assert "m" in d.duplicates
    assert any("duplicate" in str(x) for x in d.diagnostics)


def test_refresh_after_edit(tmp_path):
    p = tmp_path / "a.v"
    p.write_text("module a; b u_b (); endmodule\nmodule b; endmodule\n")
    d = Design.from_files([str(p)], backend="text")
    assert set(d.modules) == {"a", "b"}
    d.file(str(p)).update("module a; c u_c (); endmodule\nmodule b; endmodule\nmodule c; endmodule\n")
    d.refresh([str(p)])
    assert set(d.modules) == {"a", "b", "c"}
    assert {i.path for i in d.all_instances()} == {"a", "a.u_c", "b"}


def test_missing_backend_raises(tmp_path, monkeypatch):
    p = tmp_path / "a.v"
    p.write_text("module a; endmodule\n")
    monkeypatch.setenv("PYVERILOG_AUTO_NO_SLANG", "1")
    assert not is_slang_available()
    with pytest.raises(DesignError):
        Design.from_files([str(p)], backend="slang")
    d = Design.from_files([str(p)], backend="auto")
    assert d.backend == "text"
