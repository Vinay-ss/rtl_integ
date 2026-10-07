"""Template structure in operations: guards, loops, code blocks, unroll."""

from __future__ import annotations

import pytest

from pyverilog_auto.gui.ops import OpsManager
from pyverilog_auto.gui.session import Session
from pyverilog_auto.prepro import find_perl

needs_perl = pytest.mark.skipif(find_perl() is None, reason="no perl interpreter")


def _open(root):
    s = Session()
    summary = s.open(project=str(root))
    assert summary["ok"], s.diagnostics()
    return s, OpsManager(s)


def _do(ops, plan):
    """Apply *plan*, and the operation it hands over to (``then``)."""
    assert plan["ok"], (plan["diagnostics"], plan.get("question"))
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    if plan.get("then"):
        t = plan["then"]
        return _do(ops, getattr(ops, t["method"])(**t["params"]))
    return res


def _paths(s):
    return [i.path for i in s.result.design.all_instances()]


def _set_use_d(root, value: str, s):
    tpl = root / "tpl" / "top.svp"
    tpl.write_text(tpl.read_text().replace("USE_D = True", f"USE_D = {value}"))
    s.build()


def test_unroll_python_loop(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_unroll("top.u_lane1")
    assert plan["title"] == "unroll the for at tpl/top.svp:27"
    _do(ops, plan)
    text = (proj_py / "tpl" / "top.svp").read_text()
    assert "range(LANES)" not in text and "stage u_lane1 (" in text
    tree = {c["name"]: c["class"] for c in s.tree()["roots"][0]["children"]}
    assert tree["u_lane0"] == tree["u_lane1"] == "LITERAL"
    assert ops.plan_unroll("top.u_a")["diagnostics"][0]["code"] == "E_UNROLL_NONE"


@needs_perl
def test_unroll_perl_loop_takes_closing_brace(proj_pl, slang):
    s, ops = _open(proj_pl)
    _do(ops, ops.plan_unroll("top.u_lane0"))
    text = (proj_pl / "tpl" / "top.plv").read_text()
    assert "// pl for" not in text and "// pl }" not in text
    assert "stage u_lane0 (" in text and "stage u_lane1 (" in text


def test_wrap_loop_group(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_lane0"], "lanes", "u_lanes", choices={"loop": "group"})
    assert any(d["code"] == "N_LOOP_MEMBER" for d in plan["diagnostics"])
    _do(ops, plan)
    wrapper = (proj_py / "tpl" / "lanes.svp").read_text()
    assert "// py for i in range(LANES):" in wrapper and "stage u_lane#$i" in wrapper
    assert 'vars = ["LANES"]' in (proj_py / "rtl_integ_project.toml").read_text()
    assert "top.u_lanes.u_lane1" in _paths(s)
    # the loop stays live in the wrapper: more lanes in the parent mean more lanes inside
    tpl = proj_py / "tpl" / "top.svp"
    tpl.write_text(tpl.read_text().replace("LANES = 2", "LANES = 3"))
    s.build()
    assert "top.u_lanes.u_lane2" in _paths(s)


def test_wrap_loop_unroll_then_wrap(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_lane1", "top.u_d"], "ld", "u_ld", choices={"loop": "unroll"})
    assert plan["op"] == "unroll" and plan["then"]["method"] == "plan_wrap"
    _do(ops, plan)
    assert {"top.u_ld.u_lane1", "top.u_ld.u_d", "top.u_lane0"} <= set(_paths(s))


def test_wrap_same_guard_keeps_guard_in_parent(proj_py, slang):
    s, ops = _open(proj_py)
    _do(ops, ops.plan_wrap(["top.u_d"], "d_wrap", "u_dw"))
    text = (proj_py / "tpl" / "top.svp").read_text()
    assert text.index("// py if USE_D:") < text.index("d_wrap u_dw")
    assert "// py if" not in (proj_py / "tpl" / "d_wrap.svp").read_text()
    _set_use_d(proj_py, "False", s)
    assert "top.u_dw" not in _paths(s)                 # the wrapper instance is guarded


def test_wrap_mixed_guard_carries_it(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_c", "top.u_d"], "cd_wrap", "u_cd")
    assert any(d["code"] == "N_GUARD_REMOVED" for d in plan["diagnostics"])
    _do(ops, plan)
    wrapper = (proj_py / "tpl" / "cd_wrap.svp").read_text()
    assert "// py if USE_D:\n   stage u_d" in wrapper and "\n// py\nendmodule" in wrapper
    assert "// py if USE_D:" not in (proj_py / "tpl" / "top.svp").read_text()
    assert 'vars = ["W", "USE_D"]' in (proj_py / "rtl_integ_project.toml").read_text()
    _set_use_d(proj_py, "False", s)
    assert "top.u_cd.u_c" in _paths(s) and "top.u_cd.u_d" not in _paths(s)


def test_hoist_guarded_keeps_markers_at_column_zero(proj_py, slang):
    s, ops = _open(proj_py)
    _do(ops, ops.plan_wrap(["top.u_c", "top.u_d"], "cd_wrap", "u_cd"))
    _do(ops, ops.plan_hoist("top.u_cd.u_d"))
    text = (proj_py / "tpl" / "top.svp").read_text()
    assert "\n// py if USE_D:\n   stage u_d (" in text
    assert "// py if" not in (proj_py / "tpl" / "cd_wrap.svp").read_text()
    _set_use_d(proj_py, "False", s)
    assert "top.u_d" not in _paths(s)


def test_hoist_loop_member_unrolls_first(proj_py, slang):
    s, ops = _open(proj_py)
    _do(ops, ops.plan_wrap(["top.u_lane0"], "lanes", "u_lanes", choices={"loop": "group"}))
    plan = ops.plan_hoist("top.u_lanes.u_lane1")
    assert plan["question"]["key"] == "loop" and plan["question"]["options"] == ["unroll", "cancel"]
    _do(ops, ops.plan_hoist("top.u_lanes.u_lane1", choices={"loop": "unroll"}))
    assert "top.u_lane1" in _paths(s) and "top.u_lanes.u_lane0" in _paths(s)


def test_code_printed_instance_is_frozen_first(proj_py, slang):
    tpl = proj_py / "tpl" / "top.svp"
    tpl.write_text(tpl.read_text().replace(
        "   assign busy = b_busy;",
        "/* py-begin\nprint('   stage u_k (.clk(clk), .rst_n(rst_n), .d(din), .q(), .busy());')\npy-end */\n\n"
        "   assign busy = b_busy;"))
    s, ops = _open(proj_py)
    kids = {c["name"]: c["class"] for c in s.tree()["roots"][0]["children"]}
    assert kids["u_k"] == "CODE"
    plan = ops.plan_wrap(["top.u_k"], "k_wrap", "u_kw")
    assert plan["question"]["key"] == "code"
    _do(ops, ops.plan_wrap(["top.u_k"], "k_wrap", "u_kw", choices={"code": "freeze"}))
    assert "py-begin" not in tpl.read_text()
    assert "top.u_kw.u_k" in _paths(s)


def test_unroll_refuses_when_later_code_needs_the_loop(proj_py, slang):
    tpl = proj_py / "tpl" / "top.svp"
    tpl.write_text(tpl.read_text().replace("// py # end of lanes", "// py last = i\n// py # end of lanes"))
    s, ops = _open(proj_py)
    plan = ops.plan_unroll("top.u_lane0")
    # "last = i" runs after the loop (indent 0) and needs the loop variable: the
    # trial run of the unrolled template fails, nothing is planned
    assert not plan["ok"]
    assert [d["code"] for d in plan["diagnostics"]] == ["E_UNROLL_TRIAL"]
