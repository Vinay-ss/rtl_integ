"""Hoist operation: out of a wrapper, for every copy; dissolve; round trips."""

from __future__ import annotations

import pytest

from pyverilog_auto.gui.ops import OpsManager
from pyverilog_auto.gui.ops.textops import add_pins, fold_constants, remove_pin, rewrite_pin, subst_identifiers
from pyverilog_auto.gui.session import Session
from pyverilog_auto.gui.verify import verify_designs


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


def test_round_trip_wrap_hoist_dissolve(proj_py, slang):
    s, ops = _open(proj_py)
    original = s.result.design
    manifest0 = (proj_py / "rtl_integ_project.toml").read_text()
    _do(ops, ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab"))

    plan = ops.plan_hoist("top.u_ab.u_b")
    assert "ab_wrap loses ports: b2c, b_busy" in plan["summary"]
    assert "ab_wrap gains ports: a2b (output)" in plan["summary"]
    _do(ops, plan)
    wrapper = (proj_py / "tpl" / "ab_wrap.svp").read_text()
    assert "output logic [7:0] a2b);" in wrapper and "b_busy" not in wrapper
    top = (proj_py / "tpl" / "top.svp").read_text()
    assert top.index("logic [7:0] a2b;") < top.index("ab_wrap u_ab")    # declared before use

    plan = ops.plan_hoist("top.u_ab.u_a")
    assert plan["question"]["key"] == "dissolve"
    _do(ops, ops.plan_hoist("top.u_ab.u_a", choices={"dissolve": "remove"}))
    assert not (proj_py / "tpl" / "ab_wrap.svp").exists()
    assert (proj_py / "rtl_integ_project.toml").read_text() == manifest0
    assert "ab_wrap" not in (proj_py / "tpl" / "top.svp").read_text()
    # back to the original connectivity, same instance paths
    assert verify_designs(original, s.result.design).ok
    # three journal entries; undo them all
    assert len(ops.journal()) == 3
    for _ in range(3):
        ops.undo()
    assert s.result.design.instance("top.u_a") is not None and not (proj_py / "tpl" / "ab_wrap.svp").exists()


def test_hoist_keep_empty_wrapper(proj_py, slang):
    s, ops = _open(proj_py)
    _do(ops, ops.plan_wrap(["top.u_a"], "a_wrap", "u_aw"))
    plan = ops.plan_hoist("top.u_aw.u_a", choices={"dissolve": "keep"})
    _do(ops, plan)
    assert any(d["code"] == "N_EMPTY" for d in plan["diagnostics"])
    assert "  ();" in (proj_py / "tpl" / "a_wrap.svp").read_text()


def test_hoist_from_wrapper_used_twice(proj_two, slang):
    s, ops = _open(proj_two)
    plan = ops.plan_hoist("top.u_p0.s1")
    assert plan["title"] == "hoist s1 out of pair (2 copies)"
    _do(ops, plan)
    top = (proj_two / "tpl" / "top.svp").read_text()
    assert "logic [7:0] u_p0_mid;" in top and "logic [7:0] u_p1_mid;" in top
    assert "stage #(.W(8)) u_p0_s1 (.clk(clk), .rst_n(rst_n), .d(u_p0_mid), .q(ya), .busy());" in top
    assert "pair u_p1 (.clk(clk), .rst_n(rst_n), .din(b), .mid(u_p1_mid));" in top
    pair = (proj_two / "tpl" / "pair.svp").read_text()
    assert "output logic [W-1:0] mid);" in pair and "dout" not in pair
    assert [i.path for i in s.result.design.all_instances()] == [
        "top", "top.u_p0", "top.u_p0.s0", "top.u_p0_s1", "top.u_p1", "top.u_p1.s0", "top.u_p1_s1"]


def test_hoist_auto_style(proj_auto, slang):
    s, ops = _open(proj_auto)
    _do(ops, ops.plan_wrap(["top.u_p", "top.u_q"], "pq_wrap", "u_pq"))
    plan = ops.plan_hoist("top.u_pq.u_q")
    _do(ops, plan)
    top = (proj_auto / "tpl" / "top.svp").read_text()
    assert "pq_wrap u_pq (/*AUTOINST*/);" in top            # AUTOINST picks up the new port
    assert "cons u_q (/*AUTOINST*/);" in top
    assert "   logic ack;" in top
    wrapper = (proj_auto / "tpl" / "pq_wrap.svp").read_text()
    assert "input  logic       ack" in wrapper and "dout" not in wrapper


def test_hoist_route_demo_auto_ports_and_routes(proj_route, slang):
    s, ops = _open(proj_route)
    plan = ops.plan_hoist("top.coreA.instC")
    assert any(d["code"] == "N_AUTO_PORTS" for d in plan["diagnostics"])
    _do(ops, plan)
    assert "leaf_c instC (/*AUTOINST*/);" in (proj_route / "src" / "top.v").read_text()
    assert "instC" not in (proj_route / "src" / "core_a.sv").read_text().split("module core_a")[1]
    paths = [i.path for i in s.result.design.all_instances()]
    assert "top.instC" in paths and "top.coreA.instD" in paths


@pytest.mark.parametrize("path,code", [
    ("top.u_a", "E_HOIST_TOP"),
    ("top", "E_HOIST_TOP"),
])
def test_hoist_refusals(proj_py, slang, path, code):
    s, ops = _open(proj_py)
    plan = ops.plan_hoist(path)
    assert [d["code"] for d in plan["diagnostics"] if d["severity"] == "error"] == [code]


def test_textops():
    line = ["   stage u_a (.clk(clk), .d(din), .q(a2b), .busy());"]
    assert rewrite_pin(line, "d", "x[3:0]") == ["   stage u_a (.clk(clk), .d(x[3:0]), .q(a2b), .busy());"]
    assert remove_pin(line, "busy") == ["   stage u_a (.clk(clk), .d(din), .q(a2b));"]
    assert remove_pin(line, "clk") == ["   stage u_a (.d(din), .q(a2b), .busy());"]
    assert add_pins(["   p u (/*AUTOINST*/);"], [("a", "b")]) == ["   p u (.a(b), /*AUTOINST*/);"]
    multi = ["   w u", "     (.a (a),", "      .b (b));"]
    assert add_pins(multi, [("c", "x")]) == ["   w u", "     (.a (a),", "      .b (b),", "      .c(x));"]
    assert remove_pin(multi, "b") == ["   w u", "     (.a (a));"]
    assert subst_identifiers("{a, b.c, p[W-1:0]}", {"a": "X", "c": "Y", "W": "8"}) == "{X, b.c, p[8-1:0]}"
    assert fold_constants("[8-1:0]") == "[7:0]" and fold_constants("[W-1:0]") == "[W-1:0]"
    assert fold_constants("(4*2)") == "8"
