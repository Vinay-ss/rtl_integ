"""Wrap operation: plan, apply (journal), verify, undo."""

from __future__ import annotations

import json

import pytest

from pyverilog_auto.gui.ops import OpsManager
from pyverilog_auto.gui.session import Session, SessionError
from pyverilog_auto.prepro import find_perl

needs_perl = pytest.mark.skipif(find_perl() is None, reason="no perl interpreter")


def _open(root):
    s = Session()
    summary = s.open(project=str(root))
    assert summary["ok"], s.diagnostics()
    return s, OpsManager(s)


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "build" not in p.parts and ".rtl_integ_gui" not in p.parts}


def test_wrap_literal_net_naming(proj_py, slang):
    s, ops = _open(proj_py)
    before = _files(proj_py)
    plan = ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab")
    assert plan["ok"], plan["diagnostics"]
    assert "+   ab_wrap u_ab" in plan["diff"]
    assert "-   logic [7:0] a2b;" in plan["diff"]          # a2b became internal
    assert "internal: a2b" in plan["summary"]
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    wrapper = (proj_py / "tpl" / "ab_wrap.svp").read_text()
    assert wrapper == (
        "// ab_wrap: wrapper created by rtl-integ-gui from top (tpl/top.svp)\n"
        "module ab_wrap\n"
        "  (input  logic       clk,\n"
        "   input  logic       rst_n,\n"
        "   input  logic [7:0] din,\n"
        "   output logic [7:0] b2c,\n"
        "   output logic       b_busy);\n"
        "\n"
        "   logic [7:0] a2b;\n"
        "\n"
        "   stage u_a (.clk(clk), .rst_n(rst_n), .d(din), .q(a2b), .busy());\n"
        "\n"
        "   stage u_b (.clk(clk), .rst_n(rst_n), .d(a2b), .q(b2c), .busy(b_busy));\n"
        "endmodule\n")
    tree = s.tree()["roots"][0]["children"]
    assert [(c["name"], c["wrapper"]) for c in tree][:2] == [("u_ab", True), ("u_c", False)]
    assert [c["name"] for c in tree[0]["children"]] == ["u_a", "u_b"]
    assert res["focus"] == "top.u_ab"
    # undo restores every file byte for byte and removes the wrapper
    assert [e["title"] for e in ops.journal()] == ["wrap u_a, u_b into ab_wrap u_ab"]
    ops.undo()
    assert _files(proj_py) == before
    assert s.result.design.instance("top.u_a") is not None
    assert ops.journal() == []


def test_wrap_subst_carries_variables_inst_port(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_c", "top.u_b"], "bc_wrap", "u_bc", port_naming="inst_port")
    assert plan["ok"], plan["diagnostics"]
    assert any(d["code"] == "N_CARRY" for d in plan["diagnostics"])
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    manifest = (proj_py / "rtl_integ_project.toml").read_text()
    assert 'bind = { from = "tpl/top.svp", line = 19, vars = ["W"] }' in manifest
    wrapper = (proj_py / "tpl" / "bc_wrap.svp").read_text()
    assert "stage #(.W(#$W)) u_c" in wrapper              # template token kept
    assert "assign a2b = u_b_d;" in wrapper and "assign u_c_q = c2d;" in wrapper
    gen = (proj_py / "build" / "gen" / "bc_wrap.sv").read_text()
    assert "stage #(.W(8)) u_c" in gen                    # bound at build time
    top = (proj_py / "tpl" / "top.svp").read_text()
    assert ".u_b_d     (a2b)" in top


def test_wrap_auto_style_with_interface(proj_auto, slang):
    s, ops = _open(proj_auto)
    plan = ops.plan_wrap(["top.u_p", "top.u_q"], "pq_wrap", "u_pq")
    assert plan["ok"], plan["diagnostics"]
    assert "+   pq_wrap u_pq (/*AUTOINST*/);" in plan["diff"]
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    wrapper = (proj_auto / "tpl" / "pq_wrap.svp").read_text()
    assert "   ifc                bus);" in wrapper
    assert "   logic ack;" in wrapper
    integ = (proj_auto / "build" / "integ" / "top.sv").read_text()
    assert ".bus                   (bus)" in integ
    assert "From u_pq of pq_wrap" in integ                # AUTOWIRE now sees the wrapper


@needs_perl
def test_wrap_perl_carry(proj_pl, slang):
    s, ops = _open(proj_pl)
    plan = ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab")
    assert plan["ok"], plan["diagnostics"]
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    manifest = (proj_pl / "rtl_integ_project.toml").read_text()
    assert 'vars = ["$W"]' in manifest and 'lang = "perl"' in manifest
    assert "stage #(.W(8)) u_b" in (proj_pl / "build" / "gen" / "ab_wrap.sv").read_text()


@needs_perl
def test_wrap_perl_lexical_asks_to_freeze_and_partial_output(proj_pl, slang):
    s, ops = _open(proj_pl)
    plan = ops.plan_wrap(["top.u_e"], "e_wrap", "u_ew")
    assert not plan["ok"] and plan["question"]["key"] == "subst"
    assert "freeze" in plan["question"]["options"]
    plan = ops.plan_wrap(["top.u_e"], "e_wrap", "u_ew", choices={"subst": "freeze"})
    assert plan["ok"], plan["diagnostics"]
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    wrapper = (proj_pl / "tpl" / "e_wrap.plv").read_text()
    assert "output logic [3:0] u_e_q" in wrapper           # partial output gets its own port
    assert "stage #(.W(4)) u_e (" in wrapper and ".q(u_e_q)" in wrapper
    assert ".u_e_q (dout[3:0])" in (proj_pl / "tpl" / "top.plv").read_text()


def test_wrap_loop_asks(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_lane0"], "w", "u_w")
    assert plan["question"]["key"] == "loop" and plan["question"]["options"] == ["group", "unroll", "cancel"]
    plan = ops.plan_wrap(["top.u_lane0"], "w", "u_w", choices={"loop": "cancel"})
    assert [d["code"] for d in plan["diagnostics"] if d["severity"] == "error"] == ["E_TPL_LOOP"]


@pytest.mark.parametrize("paths,module,inst,code", [
    (["top.u_a"], "stage", "u_w", "E_WRAP_MODULE_EXISTS"),
    (["top.u_a"], "w", "u_c", "E_WRAP_INST_EXISTS"),
    (["top.u_a"], "1bad", "u_w", "E_WRAP_NAME"),
    (["top"], "w", "u_w", "E_WRAP_TOP"),
    (["top.u_a", "top.u_a"], "w", "u_w", "E_WRAP_DUP"),
])
def test_wrap_refusals(proj_py, slang, paths, module, inst, code):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(paths, module, inst)
    assert not plan["ok"]
    assert [d["code"] for d in plan["diagnostics"] if d["severity"] == "error"] == [code]


def test_wrap_refuses_stale_template(proj_py, slang):
    s, ops = _open(proj_py)
    plan = ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab")
    tpl = proj_py / "tpl" / "top.svp"
    tpl.write_text(tpl.read_text() + "// edited meanwhile\n")
    with pytest.raises(SessionError) as ei:
        ops.apply(plan["id"])
    assert ei.value.code == "E_STALE"
    assert not (proj_py / "tpl" / "ab_wrap.svp").exists()


def test_wrap_after_unbuilt_template_edit(proj_py, slang):
    # the template is edited (lines shift) but not rebuilt: planning must
    # rebuild first instead of using the old line map
    s, ops = _open(proj_py)
    tpl = proj_py / "tpl" / "top.svp"
    text = tpl.read_text()
    text = text.replace("   output logic       busy);",
                        "   output logic       busy,\n   output logic       test_busy);")
    text = text.replace(".q(a2b), .busy());", ".q(a2b), .busy(test_busy));")
    tpl.write_text(text)
    assert [s.project.rel(f) for f in s.changed_inputs()] == ["tpl/top.svp"]
    plan = ops.plan_wrap(["top.u_a", "top.u_b"], "ab_wrap", "u_ab")
    assert plan["rebuilt"] is True
    assert plan["ok"], plan["diagnostics"]
    res = ops.apply(plan["id"])
    assert res["verified"] is True, res
    top = tpl.read_text()
    assert "stage u_a" not in top and "stage u_b" not in top and "ab_wrap u_ab" in top
    assert ".test_busy (test_busy)" in top
    wrapper = (proj_py / "tpl" / "ab_wrap.svp").read_text()
    assert "output logic       test_busy" in wrapper
    assert "stage u_a (.clk(clk), .rst_n(rst_n), .d(din), .q(a2b), .busy(test_busy));" in wrapper
    # nothing changed since: the next plan does not rebuild
    assert s.changed_inputs() == []
    assert ops.plan_hoist("top.u_ab.u_a")["rebuilt"] is False


def test_wrap_over_rpc(proj_py, slang):
    from pyverilog_auto.gui.ops import install
    from pyverilog_auto.gui.server import Server

    class Out:
        def __init__(self):
            self.msgs = []

        def write(self, b):
            self.msgs.extend(json.loads(x) for x in b.decode().splitlines())

        def flush(self):
            pass

    out = Out()
    srv = Server(out)
    install(srv)
    for i, (m, p) in enumerate([("open", {"project": str(proj_py)}),
                                ("plan_wrap", {"paths": ["top.u_a", "top.u_b"], "module": "ab_wrap",
                                               "instance": "u_ab"})], 1):
        srv.handle_line(json.dumps({"id": i, "method": m, "params": p}).encode())
    plan = next(m for m in out.msgs if m.get("id") == 2)["result"]
    srv.handle_line(json.dumps({"id": 3, "method": "apply", "params": {"plan_id": plan["id"]}}).encode())
    res = next(m for m in out.msgs if m.get("id") == 3)["result"]
    assert res["verified"] is True
    srv.handle_line(json.dumps({"id": 4, "method": "apply", "params": {"plan_id": plan["id"]}}).encode())
    err = next(m for m in out.msgs if m.get("id") == 4)["error"]
    assert err["code"] == "E_PLAN"
