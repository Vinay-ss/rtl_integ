"""Operations on modules that import packages: package types, parameters and
enum values are not nets; wrappers and parents get the imports they need."""

from __future__ import annotations

import shutil

import pytest

from pyverilog_auto.gui.connect import module_index, package_names
from pyverilog_auto.gui.journal import Journal, JournalError
from pyverilog_auto.gui.ops import OpsManager
from pyverilog_auto.gui.ops.textops import port_list_span, remove_pin, rewrite_pin
from pyverilog_auto.gui.session import Session, SessionError


def _open(root):
    s = Session()
    summary = s.open(project=str(root))
    assert summary["ok"], s.diagnostics()
    return s, OpsManager(s)


def _do(ops, plan):
    assert plan["ok"], (plan["diagnostics"], plan.get("question"))
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    return res


def test_index_knows_imported_names(proj_pkg, slang):
    s, _ops = _open(proj_pkg)
    d = s.result.design
    assert package_names(d, "p") == {"W", "op_e", "OP_A", "OP_B", "pkt_t"}
    ix = module_index(d, "blk")
    assert ix.imports == ["p::*"]
    assert ix.is_imported("OP_B") and ix.is_imported("pkt_t")
    assert not ix.is_imported("mid") and not ix.is_imported("clk")


def test_wrap_keeps_package_names_and_declaration_order(proj_pkg, slang):
    s, ops = _open(proj_pkg)
    plan = ops.plan_wrap(["top.u_blk.u_op", "top.u_blk.u_sink"], "os_wrap", "u_os")
    _do(ops, plan)
    wrapper = (proj_pkg / "tpl" / "os_wrap.svp").read_text()
    assert wrapper.startswith("// os_wrap: wrapper created by rtl-integ-gui from blk (tpl/blk.svp)\n"
                              "module os_wrap\n  import p::*;\n")
    ports = wrapper.split("(", 1)[1].split(");", 1)[0]
    for name in ("OP_B", "pkt_t ", "op_e "):
        assert f" {name.strip()}," not in ports and f" {name.strip()})" not in ports, name
    assert "#(.DEF(OP_B), .T(pkt_t)) u_op" in wrapper
    blk = (proj_pkg / "tpl" / "blk.svp").read_text()
    # "late" is declared after u_op: the wrapper instance takes u_sink's place
    assert blk.index("pkt_t late;") < blk.index("os_wrap u_os")
    assert "OP_B" not in blk and "leaf_op" not in blk and "leaf_sink" not in blk
    assert ".late (late)" in blk and ".mid  (mid)" in blk


def test_hoist_adds_the_import_and_rewrites_implicit_pins(proj_pkg, slang):
    s, ops = _open(proj_pkg)
    plan = ops.plan_hoist("top.u_blk.u_op")
    assert any(d["code"] == "N_IMPORT" for d in plan["diagnostics"]), plan["diagnostics"]
    _do(ops, plan)
    top = (proj_pkg / "tpl" / "top.svp").read_text()
    assert top.startswith("// top does not import the package itself\nmodule top\n  import p::*; (")
    assert ".clk(t_clk)" in top and ".din(t_din)" in top
    assert "#(.DEF(OP_B), .T(pkt_t)) u_op" in top


def test_header_and_pin_text_helpers():
    hdr = "module automatic m // c\n  import p::*, q::x;\n  #(parameter W = 1)\n  (input a,\n   output b);"
    op, cl = port_list_span(hdr)
    assert hdr[op:cl + 1] == "(input a,\n   output b)"
    inst = ["  leaf u (", "    .clk,", "    .din,   // data", "    .y (y)", "  );"]
    assert rewrite_pin(inst, "din", "t_din")[2] == "    .din(t_din),   // data"
    assert rewrite_pin(inst, "clk", "c")[1] == "    .clk(c),"
    # the entry goes, a comment written after it stays
    assert remove_pin(inst, "din") == ["  leaf u (", "    .clk,", "    // data", "    .y (y)", "  );"]
    assert remove_pin(inst, "clk") == ["  leaf u (", "    .din,   // data", "    .y (y)", "  );"]
    assert rewrite_pin(["  leaf u (.*);"], "clk", "c") is None      # nothing written for ".*"


def test_journal_is_relative_to_the_project(proj_pkg, tmp_path, slang):
    s, ops = _open(proj_pkg)
    _do(ops, ops.plan_wrap(["top.u_blk.u_op", "top.u_blk.u_sink"], "os_wrap", "u_os"))
    entry = Journal(str(proj_pkg / ".rtl_integ_gui")).entries()[-1]
    assert {f["path"] for f in entry.files} == {"tpl/blk.svp", "tpl/os_wrap.svp", "rtl_integ_project.toml"}
    # a copy of the project undoes in the copy, not in the original
    copy = tmp_path / "copy"
    shutil.copytree(proj_pkg, copy, ignore=shutil.ignore_patterns("build", "rtl_gen"))
    s2, ops2 = _open(copy)
    ops2.undo()
    assert not (copy / "tpl" / "os_wrap.svp").exists()
    assert (proj_pkg / "tpl" / "os_wrap.svp").exists()
    # an old entry naming a file outside the project is refused
    j = Journal(str(proj_pkg / ".rtl_integ_gui"))
    bad = j.entries()[-1]
    with pytest.raises(JournalError):
        j._resolve(str(tmp_path / "elsewhere.sv"), bad.title)
    with pytest.raises(SessionError):
        ops2.undo()                                     # nothing left in the copy
