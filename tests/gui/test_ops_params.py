"""Wrapping instances of a module with its own parameters, type parameters,
typedefs and functions: the wrapper gets copies of everything the moved text
and its own port / net declarations need."""

from __future__ import annotations

from pyverilog_auto.gui.connect import module_index
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
    return res


def test_index_records_types_functions_and_enum_values(proj_param, slang):
    s, _ops = _open(proj_param)
    ix = module_index(s.result.design, "blk")
    d = ix.decls
    assert (d["W"].kind, d["W"].type_text, d["W"].is_type) == ("param", "int", False)
    assert (d["T"].kind, d["T"].is_type, d["T"].value, d["T"].refs) == ("param", True, "logic [W-1:0]", ["W"])
    assert (d["word_t"].kind, d["word_t"].is_type) == ("localparam", True)
    assert d["pair_t"].kind == "typedef" and d["pair_t"].refs == ["T", "word_t"]
    assert d["pair_t"].value.startswith("struct packed {")
    assert d["lvl_e"].kind == "typedef"
    assert (d["LV_HI"].kind, d["LV_HI"].value) == ("enumval", "lvl_e")
    assert d["depth_bits"].kind == "function"
    assert d["DEPTH"].refs == ["N"]
    assert d["sdin"].full_type == "logic signed [W-1:0]"


def test_wrap_copies_parameters_types_and_functions(proj_param, slang):
    s, ops = _open(proj_param)
    plan = ops.plan_wrap(["top.u_blk.u_mk", "top.u_blk.u_use"], "wrp", "u_wrp")
    assert "  types:    T, word_t, pair_t\n" in plan["summary"]
    assert any(d["code"] == "N_COPIED" for d in plan["diagnostics"]), plan["diagnostics"]
    _do(ops, plan)
    wrapper = (proj_param / "tpl" / "wrp.svp").read_text()
    assert wrapper.startswith(
        "// wrp: wrapper created by rtl-integ-gui from blk (tpl/blk.svp)\n"
        "module wrp\n"
        "  #(parameter int W = 8,\n"
        "    parameter type T = logic [W-1:0],\n"
        "    parameter type word_t = logic [W-1:0],\n"
        "    parameter type pair_t = struct packed {\n"
        "      T      a;\n"
        "      word_t b;\n"
        "    },\n"
        "    parameter int N = 2,\n"
        "    parameter int DEPTH = N * 2)\n"
        "  (input  logic                clk,\n"
        "   input  T                    tin,\n"
        "   input  logic signed [W-1:0] sdin,\n"
        "   output logic signed [W:0]   sum,\n"
        "   output logic [W-1:0]        y);\n"
        "\n"
        "  function automatic int depth_bits(int v);\n"
        "    return $clog2(v);\n"
        "  endfunction\n"
        "\n"
        "  pair_t mid;\n"), wrapper
    blk = (proj_param / "tpl" / "blk.svp").read_text()
    assert "wrp #(.W(W), .T(T), .word_t(word_t), .pair_t(pair_t), .N(N), .DEPTH(DEPTH)) u_wrp" in blk
    assert "pair_t mid;" not in blk
    # the parent keeps its own declarations: other statements may still use them
    assert "typedef struct packed" in blk and "function automatic int depth_bits" in blk


def test_wrap_port_of_a_local_struct_type(proj_param, slang):
    s, ops = _open(proj_param)
    _do(ops, ops.plan_wrap(["top.u_blk.u_use"], "use_wrap", "u_uw"))
    wrapper = (proj_param / "tpl" / "use_wrap.svp").read_text()
    assert "   input  pair_t        mid,\n" in wrapper
    assert "parameter type pair_t = struct packed {" in wrapper
    assert "depth_bits" not in wrapper and "DEPTH" not in wrapper
    blk = (proj_param / "tpl" / "blk.svp").read_text()
    assert "use_wrap #(.W(W), .T(T), .word_t(word_t), .pair_t(pair_t)) u_uw" in blk


def test_wrap_refuses_a_local_enum_value(proj_param, slang):
    s, ops = _open(proj_param)
    plan = ops.plan_wrap(["top.u_blk.u_lvl"], "lvl_wrap", "u_lw")
    assert not plan["ok"]
    assert [d["code"] for d in plan["diagnostics"]] == ["E_WRAP_ENUM"], plan["diagnostics"]
    assert not (proj_param / "tpl" / "lvl_wrap.svp").exists()


def test_hoist_refuses_a_type_parameter(proj_param, slang):
    s, ops = _open(proj_param)
    _do(ops, ops.plan_wrap(["top.u_blk.u_use"], "use_wrap", "u_uw"))
    plan = ops.plan_hoist("top.u_blk.u_uw.u_use")
    assert not plan["ok"]
    assert any(d["code"] == "E_HOIST_TYPE_PARAM" for d in plan["diagnostics"]), plan["diagnostics"]
