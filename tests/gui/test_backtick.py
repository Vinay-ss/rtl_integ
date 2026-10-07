"""Operations on templates written in the backtick syntax (` code lines,
[* *] code blocks, `var` variables): same results as the prepro syntax, and
every line the GUI writes uses the template's own markers."""

from __future__ import annotations

from pyverilog_auto.gui.ops import OpsManager
from pyverilog_auto.gui.session import Session


def _open(root):
    s = Session()
    summary = s.open(project=str(root))
    assert summary["ok"], s.diagnostics()
    return s, OpsManager(s)


def _do(ops, plan):
    assert plan["ok"], (plan["diagnostics"], plan.get("question"))
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    if plan.get("then"):
        t = plan["then"]
        return _do(ops, getattr(ops, t["method"])(**t["params"]))
    return res


def _paths(s):
    return [i.path for i in s.result.design.all_instances()]


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "build" not in p.parts and ".rtl_integ_gui" not in p.parts}


def test_same_output_and_classes_as_prepro_syntax(proj_bt, proj_py, slang):
    s_bt, _ = _open(proj_bt)
    s_py, _ = _open(proj_py)
    gen_bt = (proj_bt / "build" / "gen" / "top.sv").read_text()
    assert gen_bt == (proj_py / "build" / "gen" / "top.sv").read_text()
    kids = {c["name"]: c["class"] for c in s_bt.tree()["roots"][0]["children"]}
    assert kids == {c["name"]: c["class"] for c in s_py.tree()["roots"][0]["children"]}
    assert (kids["u_a"], kids["u_c"], kids["u_d"], kids["u_lane0"]) == ("LITERAL", "SUBST", "GUARDED", "LOOP")


def test_wrap_subst_and_guard_then_hoist(proj_bt, slang):
    s, ops = _open(proj_bt)
    before = _files(proj_bt)
    _do(ops, ops.plan_wrap(["top.u_c", "top.u_d"], "cd_wrap", "u_cd"))
    wrapper = (proj_bt / "tpl" / "cd_wrap.svp").read_text()
    assert "stage #(.W(`W`)) u_c" in wrapper                      # variable token kept
    assert "` if USE_D:\n   stage u_d" in wrapper and "\n`\nendmodule" in wrapper
    assert "// py" not in wrapper
    manifest = (proj_bt / "rtl_integ_project.toml").read_text()
    assert 'vars = ["W", "USE_D"]' in manifest
    assert manifest.count("syntax") == 1                           # the project default covers the wrapper
    assert "stage #(.W(8)) u_c" in (proj_bt / "build" / "gen" / "cd_wrap.sv").read_text()
    _do(ops, ops.plan_hoist("top.u_cd.u_d"))
    assert "\n` if USE_D:\n   stage u_d (" in (proj_bt / "tpl" / "top.svp").read_text()
    assert "top.u_d" in _paths(s)
    ops.undo()
    ops.undo()
    assert _files(proj_bt) == before


def test_loop_group_and_unroll(proj_bt, slang):
    s, ops = _open(proj_bt)
    _do(ops, ops.plan_wrap(["top.u_lane0"], "lanes", "u_lanes", choices={"loop": "group"}))
    wrapper = (proj_bt / "tpl" / "lanes.svp").read_text()
    assert "` for i in range(LANES):" in wrapper and "stage u_lane`i`" in wrapper
    assert "top.u_lanes.u_lane1" in _paths(s)
    ops.undo()
    _do(ops, ops.plan_unroll("top.u_lane1"))
    text = (proj_bt / "tpl" / "top.svp").read_text()
    assert "range(LANES)" not in text and "stage u_lane1 (" in text


def test_template_syntax_overrides_project_default(proj_bt, slang):
    manifest = proj_bt / "rtl_integ_project.toml"
    text = manifest.read_text().replace('syntax = "backtick"\n', "")
    manifest.write_text(text.replace('lang = "python"\n', 'lang = "python"\nsyntax = "backtick"\n', 1))
    s, ops = _open(proj_bt)
    _do(ops, ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab"))
    # the wrapper's entry repeats the syntax, which differs from the project default
    entry = manifest.read_text().split('src = "tpl/ab_wrap.svp"')[1]
    assert 'syntax = "backtick"' in entry
    assert "top.u_ab.u_a" in _paths(s)
