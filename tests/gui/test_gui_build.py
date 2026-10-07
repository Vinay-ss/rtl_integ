"""Project manifest, build pipeline, source map, session and RPC server."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from pyverilog_auto.gui.build import Builder, binding_defines
from pyverilog_auto.gui.project import (
    ProjectError,
    TemplateEntry,
    append_template_entry_text,
    load_project,
)
from pyverilog_auto.gui.session import Session
from pyverilog_auto.gui.srcmap import FILE, GUARDED, LITERAL, LOOP, SUBST, SourceMap


def _build(root):
    proj = load_project(str(root))
    res = Builder(proj).build()
    assert res.ok, [str(d) for d in res.diagnostics]
    return proj, res


def test_load_project_defaults(proj_py):
    proj = load_project(str(proj_py))
    assert proj.top == ["top"]
    assert [t.out for t in proj.templates] == ["top.sv"]
    assert proj.templates[0].lang == "python"
    assert [os.path.basename(s.path) for s in proj.sources] == ["stage.sv"]
    assert proj.wrap.port_naming == "net"
    assert proj.gen_dir.endswith(os.path.join("build", "gen"))


def test_load_project_errors(tmp_path):
    (tmp_path / "rtl_integ_project.toml").write_text('[[template]]\nsrc = "a.svp"\nlang = "tcl"\n')
    with pytest.raises(ProjectError):
        load_project(str(tmp_path))


def test_append_template_entry_keeps_tables(proj_py):
    proj = load_project(str(proj_py))
    text = (proj_py / "rtl_integ_project.toml").read_text()
    entry = TemplateEntry(src=str(proj_py / "tpl" / "w.svp"), out="w.sv", lang="python", created_by_gui=True)
    new = append_template_entry_text(text, proj, entry)
    assert new.index('src = "tpl/w.svp"') < new.index("[gui.wrap]")
    (proj_py / "rtl_integ_project.toml").write_text(new)
    again = load_project(str(proj_py))
    assert [t.out for t in again.templates] == ["top.sv", "w.sv"]
    assert again.templates[1].created_by_gui
    assert again.wrap.port_naming == "net"


def test_build_outputs_and_classes(proj_py, slang):
    proj, res = _build(proj_py)
    outs = {o.out: o for o in res.outputs}
    assert set(outs) == {"top.sv", "stage.sv"}
    assert outs["top.sv"].kind == "template" and outs["top.sv"].linemap is not None
    assert outs["stage.sv"].kind == "source" and outs["stage.sv"].linemap is None
    assert os.path.exists(os.path.join(proj.integ_dir, "design.f"))
    sm = SourceMap(res)
    d = res.design
    classes = {i.name: sm.statement_of(i).cls for i in d.all_instances() if i.parent is not None}
    assert classes == {"u_a": LITERAL, "u_b": LITERAL, "u_c": SUBST, "u_d": GUARDED,
                       "u_lane0": LOOP, "u_lane1": LOOP}
    st = sm.statement_of(d.instance("top.u_c"))
    assert (st.tpl_start, st.tpl_end) == (21, 22)
    assert [t.raw for t in st.tokens] == ["#$W"]
    lane = sm.statement_of(d.instance("top.u_lane1"))
    assert lane.loop is not None and lane.loop.start == 27 and lane.emit_count == 2
    guard = sm.statement_of(d.instance("top.u_d"))
    assert [g.start for g in guard.guards] == [23]


def test_module_locations(proj_py, slang):
    proj, res = _build(proj_py)
    sm = SourceMap(res)
    top = sm.module_locs("top")
    assert top["template"].path == str(proj_py / "tpl" / "top.svp")
    assert top["template"].line == 5          # "module top" in the template
    assert top["gen"].line == 2               # comment + module line in the output
    stage = sm.module_locs("stage")
    assert stage["template"].path == str(proj_py / "rtl" / "stage.sv")
    assert stage["template"].line == 2


def test_incremental_build_skips_unchanged(proj_py, slang):
    proj = load_project(str(proj_py))
    log: list[str] = []
    Builder(proj, log=log.append).build()
    log.clear()
    Builder(proj, log=log.append).build()
    assert any("up to date" in line for line in log)
    (proj_py / "tpl" / "top.svp").write_text((proj_py / "tpl" / "top.svp").read_text().replace("LANES = 2", "LANES = 3"))
    log.clear()
    res = Builder(proj, log=log.append).build()
    assert not any("up to date" in line for line in log)
    assert res.design.instance("top.u_lane2") is not None


def test_template_error_is_a_diagnostic(proj_py):
    p = proj_py / "tpl" / "top.svp"
    p.write_text(p.read_text().replace("#$W", "#$NOT_DEFINED"))
    res = Builder(load_project(str(proj_py))).build()
    assert not res.ok
    errs = [d for d in res.diagnostics if d.severity == "error"]
    assert errs and errs[0].source == "prepro" and errs[0].line == 21


def test_binding_defines():
    assert binding_defines("python", {"W": "8", "name": "'x'"}) == ["W = 8", "name = 'x'"]
    assert binding_defines("perl", {"$w": '"[7:0]"', "@l": "[1,2]"}) == ['$w = "[7:0]";', "@l = @{[1,2]};"]


def test_bound_template_gets_values(proj_py, slang):
    (proj_py / "tpl" / "child.svp").write_text("module child_#$W;\nendmodule\n")
    man = proj_py / "rtl_integ_project.toml"
    man.write_text(man.read_text().replace("[[source]]", '[[template]]\nsrc = "tpl/child.svp"\nout = "child.sv"\n'
                                           'bind = { from = "tpl/top.svp", line = 21, vars = ["W"] }\n\n[[source]]'))
    proj, res = _build(proj_py)
    gen = open(os.path.join(proj.gen_dir, "child.sv")).read()
    assert gen == "module child_8;\nendmodule\n"


# ----------------------------------------------------------------------
# session / server
# ----------------------------------------------------------------------

def test_session_tree_and_locate(proj_py, slang):
    events: list[tuple[str, dict]] = []
    s = Session(notify=lambda m, p: events.append((m, p)))
    summary = s.open(project=str(proj_py))
    assert summary["ok"] and summary["top"] == ["top"]
    assert any(m == "built" for m, _ in events)
    tree = s.tree()
    root = tree["roots"][0]
    assert root["path"] == "top" and root["class"] == "TOP"
    tags = {c["name"]: c["tag"] for c in root["children"]}
    assert tags == {"u_a": "", "u_b": "", "u_c": "S", "u_d": "G", "u_lane0": "Lx2", "u_lane1": "Lx2"}
    loc = s.locate("top.u_c", what="inst")
    assert loc["view"] == "template" and loc["line"] == 21 and loc["end_line"] == 22 and not loc["readonly"]
    loc = s.locate("top.u_c", what="module")
    assert loc["path"].endswith("stage.sv") and loc["line"] == 2
    loc = s.locate("top.u_c", what="inst", view="integ")
    assert loc["view"] == "integ" and loc["readonly"]
    info = s.node_info("top.u_b")
    assert info["statement"]["class"] == LITERAL
    assert [p["name"] for p in info["ports"]] == ["clk", "rst_n", "d", "q", "busy"]
    out = s.console_cmd("find top.u_lane.*")
    assert out["paths"] == ["top.u_lane0", "top.u_lane1"]
    assert "unknown command" in s.console_cmd("frobnicate")["output"]


def test_server_subprocess_roundtrip(proj_py, slang):
    reqs = [
        {"id": 1, "method": "hello"},
        {"id": 2, "method": "open", "params": {"project": str(proj_py)}},
        {"id": 3, "method": "locate", "params": {"path": "top.u_d", "what": "inst"}},
        {"id": 4, "method": "nope"},
        {"id": 5, "method": "shutdown"},
    ]
    inp = "".join(json.dumps(r) + "\n" for r in reqs).encode()
    env = dict(os.environ, PYTHONWARNINGS="ignore")
    proc = subprocess.run([sys.executable, "-m", "pyverilog_auto.gui.server"], input=inp,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, env=env)
    msgs = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    replies = {m["id"]: m for m in msgs if "id" in m}
    notes = [m for m in msgs if "id" not in m]
    assert replies[1]["result"]["protocol"] == 1
    assert replies[2]["result"]["ok"]
    assert replies[3]["result"]["line"] == 25
    assert replies[4]["error"]["code"] == "E_METHOD"
    assert any(n["method"] == "log" for n in notes)
    assert all(m.get("method") != "log" or "text" in m["params"] for m in msgs)


def test_view_only_session(tmp_path, slang):
    src = os.path.join(os.path.dirname(__file__), "fixtures", "proj_py", "rtl", "stage.sv")
    top = tmp_path / "t.sv"
    top.write_text("module t; stage u_s (.clk(), .rst_n(), .d(), .q(), .busy()); endmodule\n")
    fl = tmp_path / "d.f"
    fl.write_text(f"{src}\nt.sv\n")
    s = Session()
    summary = s.open(filelist=str(fl), top="t")
    assert summary["view_only"] and summary["top"] == ["t"]
    loc = s.locate("t.u_s", what="inst")
    assert loc["view"] == "integ" and loc["fallback"]
    child = s.tree()["roots"][0]["children"][0]
    assert child["class"] == FILE and child["tag"] == ""
