"""Instance pin lineup and port comments (``pyverilog_auto/auto/inst_lineup.py``).

Unit cases run :func:`lineup_instances` on inline text with a dict-based
module lookup; every formatting call also asserts ``f(f(x)) == f(x)``.
The end-to-end case runs ``AutoEngine`` twice with the options enabled
through Local Variables.
"""

from __future__ import annotations

import re

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.auto.inst_lineup import is_port_comment, lineup_instances
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.signal import ModDecls, Signal

ALL = "dir width type"

LEAF = ModDecls(
    outputs=[Signal("q", type="reg"), Signal("data_out", bits="[7:0]", type="logic")],
    inputs=[Signal("clk"), Signal("data_in", bits="[7:0]", type="logic")],
    gparams=[Signal("W", type="int"), Signal("DEPTH")],
)
WIDE = ModDecls(
    inputs=[
        Signal("pk", bits="[7:0]", multidim=["[1:0]"], type="logic"),
        Signal("mem", bits="[3:0]", memory="[0:1]", type="wire"),
        Signal("sg", bits="[W-1:0]", signed="signed"),
    ],
    interfaces=[Signal("bus", type="my_if", modport="mp"), Signal("bus2", type="my_if")],
)
LONG = ModDecls(
    outputs=[Signal("a_really_long_port_name_that_overflows", bits="[3:0]", type="logic")],
    inputs=[Signal("clk")],
)
HAZARD = ModDecls(
    inputs=[
        Signal("unbal", bits="[(W-1:0]", type="logic"),        # unbalanced paren
        Signal("dotcall", bits="[x.y(1):0]", type="logic"),     # ".ident (" sequence
        Signal("weird", bits="[3:0]", type="struct packed {logic a;}"),
    ],
)
DB = {"leaf": LEAF, "wide": WIDE, "longp": LONG, "haz": HAZARD}


def _cfg(lineup=False, pc=None, **kw) -> VerilogConfig:
    return VerilogConfig(auto_inst_lineup=lineup, auto_inst_port_comment=pc, **kw)


def fmt(text: str, lineup: bool = False, pc=None, db=DB, **kw) -> str:
    """Format *text* and check idempotency."""
    cfg = _cfg(lineup, pc, **kw)
    out = lineup_instances(text, db.get, cfg)
    assert lineup_instances(out, db.get, cfg) == out, "not idempotent:\n" + out
    return out


_PIN_START_RE = re.compile(r"^\s*\.|\(\s*\.(?=[\w*])")


def pin_lines(out: str) -> list[str]:
    """Lines holding a pin (including a first pin on the paren line)."""
    return [ln for ln in out.splitlines() if _PIN_START_RE.search(ln.split("//")[0])]


def dot_col(line: str) -> int:
    return re.search(r"\.[\w*]", line).start()


def paren_col(line: str) -> int:
    """Column of the pin's ``(`` (not the instance's)."""
    return re.search(r"\.[\w$]+\s*\(", line).end() - 1


def code(line: str) -> str:
    """The pin text of a line (from its ``.`` to before the comment)."""
    return line[dot_col(line):].split("//")[0].rstrip()


def comment_cols(lines: list[str]) -> set[int]:
    return {ln.index("//") for ln in lines if "//" in ln}


def port_comment(line: str) -> list[str]:
    """Fields of the first ``//`` segment of a pin line."""
    seg = re.split(r"\s+(?=//)", line[line.index("//"):])[0]
    return seg[2:].split()


AUTOINST = """module top;
   leaf u_leaf (/*AUTOINST*/
                // Outputs
                .q                      (q),
                .data_out               (data_out[7:0]),
                // Inputs
                .clk                    (clk),
                .data_in                (data_in[7:0]));
endmodule
"""

AUTOINST_COMMENTED = """module top;
   leaf u_leaf (/*AUTOINST*/
                // Outputs
                .q                      (q),             // output       reg
                .data_out               (data_out[7:0]), // output [7:0] logic
                // Inputs
                .clk                    (clk),           // input        wire
                .data_in                (data_in[7:0])); // input  [7:0] logic
endmodule
"""

# Hand-written pin, then the marker on its own line.
MIX = """module top;
   leaf u_leaf (
      .clk(sys_clk),  // input from ctrl
      /*AUTOINST*/
      // Outputs
      .q                      (q),
      .data_out               (data_out[7:0]),
      // Inputs
      .data_in                (data_in[7:0]));
endmodule
"""

MIX_FORMATTED = """module top;
   leaf u_leaf (
      .clk                              (sys_clk),       // input        wire   // input from ctrl
      /*AUTOINST*/
      // Outputs
      .q                                (q),             // output       reg
      .data_out                         (data_out[7:0]), // output [7:0] logic
      // Inputs
      .data_in                          (data_in[7:0])); // input  [7:0] logic
endmodule
"""

# Hand-written pin and the marker both on the paren line.
MIX_PAREN = """module top;
   leaf u_leaf (.clk(sys_clk), /*AUTOINST*/
                // Outputs
                .q                      (q),
                .data_out               (data_out[7:0]),
                // Inputs
                .data_in                (data_in[7:0]));
endmodule
"""

MIX_PAREN_FORMATTED = """module top;
   leaf u_leaf (.clk                    (sys_clk), /*AUTOINST*/ // input        wire
                // Outputs
                .q                      (q),             // output       reg
                .data_out               (data_out[7:0]), // output [7:0] logic
                // Inputs
                .data_in                (data_in[7:0])); // input  [7:0] logic
endmodule
"""

SEVERAL = """module top;
   leaf u_leaf (.clk(clk), .q(q),
                .data_in(d), .data_out(o));
endmodule
"""

PARAM = """module top;
   leaf #(/*AUTOINSTPARAM*/
          // Parameters
          .W                      (W),
          .DEPTH                  (DEPTH))
   u_leaf (.clk(clk), .q(q), .data_in(d), .data_out(o)
   );
endmodule
"""

PARAM_FORMATTED = """module top;
   leaf #(/*AUTOINSTPARAM*/
          // Parameters
          .W                            (W),     // parameter int
          .DEPTH                        (DEPTH)) // parameter
   u_leaf (.clk                         (clk), // input        wire
           .q                           (q),   // output       reg
           .data_in                     (d),   // input  [7:0] logic
           .data_out                    (o)    // output [7:0] logic
   );
endmodule
"""


# ----------------------------------------------------------------------
# Switches
# ----------------------------------------------------------------------

def test_options_off_returns_text_unchanged():
    def boom(name):
        raise AssertionError("lookup must not be called")

    for src in (AUTOINST, MIX, MIX_PAREN, SEVERAL, PARAM):
        assert lineup_instances(src, boom, _cfg()) == src
        assert lineup_instances(src, boom, _cfg(pc="")) == src


def test_lineup_alone_needs_no_lookup_and_adds_no_comments():
    def boom(name):
        raise AssertionError("lookup must not be called")

    out = lineup_instances(SEVERAL, boom, _cfg(lineup=True))
    pins = pin_lines(out)
    assert len(pins) == 4                       # one pin per line
    assert "//" not in out
    assert {dot_col(ln) for ln in pins} == {16}
    assert {paren_col(ln) for ln in pins} == {40}  # inst.py rule: max(40, 16 + 8*((16+7)//8))


def test_port_comment_alone_keeps_paren_spacing_and_splits_lines():
    out = fmt(SEVERAL, pc=ALL)
    pins = pin_lines(out)
    assert [code(ln) for ln in pins] == [
        ".clk(clk),", ".q(q),", ".data_in(d),", ".data_out(o));"]
    assert {dot_col(ln) for ln in pins} == {16}
    assert len(comment_cols(pins)) == 1
    assert [port_comment(ln) for ln in pins] == [
        ["input", "wire"], ["output", "reg"], ["input", "[7:0]", "logic"], ["output", "[7:0]", "logic"]]


def test_both_switches_full_format():
    out = fmt(SEVERAL, lineup=True, pc=ALL)
    pins = pin_lines(out)
    assert {paren_col(ln) for ln in pins} == {40}
    assert len(comment_cols(pins)) == 1
    # sub-columns: "output" sets the dir width, "[7:0]" the width column
    assert pins[0].endswith("// input        wire")
    assert pins[2].endswith("// input  [7:0] logic")
    assert pins[3].endswith("(o));  // output [7:0] logic")


def test_autoinst_layout_is_kept_and_commented():
    assert fmt(AUTOINST, lineup=True) == AUTOINST
    assert fmt(AUTOINST, pc=ALL) == AUTOINST_COMMENTED
    assert fmt(AUTOINST, lineup=True, pc=ALL) == AUTOINST_COMMENTED
    assert fmt(AUTOINST_COMMENTED, lineup=True, pc=ALL) == AUTOINST_COMMENTED


def test_field_subsets():
    out = fmt(AUTOINST, pc="width,type")
    assert [port_comment(ln) for ln in pin_lines(out)] == [
        ["reg"], ["[7:0]", "logic"], ["wire"], ["[7:0]", "logic"]]
    out = fmt(AUTOINST, pc="dir")
    assert [port_comment(ln) for ln in pin_lines(out)] == [["output"], ["output"], ["input"], ["input"]]
    # switching fields re-renders the comment instead of stacking a second one
    assert fmt(AUTOINST_COMMENTED, pc="dir") == fmt(AUTOINST, pc="dir")


def test_comment_column_option():
    out = fmt(AUTOINST, lineup=True, pc=ALL, auto_inst_comment_column=72)
    assert comment_cols(pin_lines(out)) == {72}
    out = fmt(AUTOINST, lineup=True, pc=ALL, auto_inst_comment_column=20)
    longest = max(len(ln.split("//")[0].rstrip()) for ln in pin_lines(out))
    assert comment_cols(pin_lines(out)) == {longest + 1}


# ----------------------------------------------------------------------
# Layout cases
# ----------------------------------------------------------------------

def test_long_port_name_pushes_paren_column():
    src = """module top;
   longp u_l (/*AUTOINST*/
              // Outputs
              .a_really_long_port_name_that_overflows(x[3:0]),
              // Inputs
              .clk                    (clk));
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    pins = pin_lines(out)
    parens = {paren_col(ln) for ln in pins}
    assert len(parens) == 1
    long_line = pins[0]
    assert paren_col(long_line) == dot_col(long_line) + len(".a_really_long_port_name_that_overflows") + 1
    assert len(comment_cols(pins)) == 1


def test_hand_written_pin_and_marker_on_own_line():
    out = fmt(MIX, lineup=True, pc=ALL)
    assert out == MIX_FORMATTED
    # headers stay above the same pins (SubDeclParser reads directions from them)
    order = [".clk", "/*AUTOINST*/", "// Outputs", ".q ", ".data_out", "// Inputs", ".data_in"]
    pos = [out.index(tok) for tok in order]
    assert pos == sorted(pos)


def test_hand_written_pin_and_marker_on_paren_line():
    assert fmt(MIX_PAREN, lineup=True, pc=ALL) == MIX_PAREN_FORMATTED


def test_marker_first_aligns_to_column_after_paren():
    # AUTOINST regenerates its pins one column after "(", so that is the "." column.
    src = """module top;
   leaf u_leaf (/*AUTOINST*/
      // Outputs
      .q (q),
      .data_out (data_out[7:0]));
endmodule
"""
    out = fmt(src, lineup=True)
    assert {dot_col(ln) for ln in pin_lines(out)} == {16}
    assert "                // Outputs\n" in out


def test_templated_implicit_routed_and_dotstar():
    src = """module top;
   leaf u_leaf (.*,
                // Outputs
                .q                      (q_r),           // Templated T12 L3
                .data_out               (data_out[7:0]), // Implicit .*
                // Inputs
                .clk                    (clk),  // routed: u_top.clk to u_leaf.clk
                .data_in                (din)); // input [3:0] wire  // keep me
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    pins = pin_lines(out)
    assert code(pins[0]) == ".*,"
    commented = pins[1:]
    assert len(comment_cols(commented)) == 1
    assert commented[0].endswith("// output       reg    // Templated T12 L3")
    assert commented[1].endswith("// output [7:0] logic  // Implicit .*")
    assert commented[2].endswith("// input        wire   // routed: u_top.clk to u_leaf.clk")
    # the stale port comment is replaced; the user segment survives
    assert "(din));" in commented[3] and commented[3].endswith("// input  [7:0] logic  // keep me")


def test_regenerated_last_pin_comment_is_not_duplicated():
    # AUTOINST regeneration leaves the previous "// input ..." after the new "// Templated".
    src = """module top;
   leaf u_leaf (/*AUTOINST*/
                // Inputs
                .data_in                (din)); // Templated  // input  [7:0] logic
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    assert pin_lines(out)[0].endswith("(din)); // input [7:0] logic  // Templated")


def test_autoinstparam_list():
    assert fmt(PARAM, lineup=True, pc=ALL) == PARAM_FORMATTED


def test_param_list_with_code_after_close_paren():
    src = """module top;
   leaf #(.W(8), .DEPTH(4)) u_leaf (.clk(clk), .q(q));
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    lines = out.splitlines()
    assert lines[1].endswith("(8), // parameter int")
    # ".DEPTH(4)) u_leaf (" carries code after the close paren: no comment
    depth_line = lines[2]
    assert ".DEPTH" in depth_line and "u_leaf (.clk" in depth_line
    assert depth_line.split("u_leaf (")[1].split("//")[1].split() == ["input", "wire"]
    # the port list pins line up under the first pin, which sits after the moved "("
    assert lines[3].index(".q") == depth_line.index(".clk")


def test_interface_modport_packed_unpacked_signed():
    src = """module top;
   wide u_w (/*AUTOINST*/
             // Interfaces
             .bus                    (bus),
             .bus2                   (bus2),
             // Inputs
             .pk                     (pk),
             .mem                    (mem),
             .sg                     (sg));
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    assert [port_comment(ln) for ln in pin_lines(out)] == [
        ["interface", "my_if.mp"],
        ["interface", "my_if"],
        ["input", "[1:0][7:0]", "logic"],
        ["input", "[3:0][0:1]", "wire"],
        ["input", "[W-1:0]", "wire", "signed"],
    ]
    assert len(comment_cols(pin_lines(out))) == 1


def test_hazardous_fields_are_omitted():
    src = """module top;
   haz u_h (.unbal(a), .dotcall(b), .weird(c));
endmodule
"""
    out = fmt(src, pc=ALL)
    assert [port_comment(ln) for ln in pin_lines(out)] == [
        ["input", "logic"], ["input", "logic"], ["input", "[3:0]"]]
    for ln in pin_lines(out):
        comment = ln[ln.index("//"):]
        assert comment.count("(") == comment.count(")")
        assert not re.search(r"\.\s*\w+\s*\(", comment)


def test_tabs_are_measured_as_columns():
    src = ("module top;\n"
           "   leaf u_leaf (/*AUTOINST*/\n"
           "\t\t// Outputs\n"
           "\t\t.q\t\t\t(q),\n"
           "\t\t.data_out\t\t(data_out[7:0]));\n"
           "endmodule\n")
    out = fmt(src, lineup=True, pc=ALL)
    pins = pin_lines(out)
    assert "\t" not in "".join(pins)
    assert {dot_col(ln) for ln in pins} == {16}
    assert {paren_col(ln) for ln in pins} == {40}


# ----------------------------------------------------------------------
# Port-comment recognition
# ----------------------------------------------------------------------

@pytest.mark.parametrize("segment,expected", [
    ("// input", True),
    ("// output [7:0] logic", True),
    ("// inout  [1:0][3:0]  wire signed", True),
    ("// interface my_if.mp", True),
    ("// parameter int", True),
    ("// input from ctrl", False),
    ("// input [7:0] my_t", False),
    ("// input logic extra", False),
    ("// Templated", False),
    ("// [7:0] logic", False),          # DIR is required while "dir" is a field
    ("/* input */", False),
])
def test_is_port_comment(segment, expected):
    assert is_port_comment(segment) is expected


def test_is_port_comment_rendered_type_and_subsets():
    assert is_port_comment("// input [7:0] my_t", rendered_type="my_t")
    assert is_port_comment("// [7:0] logic", fields=("width", "type"))
    assert not is_port_comment("// from ctrl", fields=("width", "type"))


def test_user_comment_resembling_port_comment_survives():
    out = fmt(MIX, pc=ALL)
    clk = pin_lines(out)[0]
    assert clk.endswith("// input        wire   // input from ctrl")


# ----------------------------------------------------------------------
# Lookup gaps and skipped lists
# ----------------------------------------------------------------------

def test_unknown_module():
    src = AUTOINST.replace("leaf u_leaf", "other u_leaf")
    assert fmt(src, pc=ALL) == src                      # nothing to add
    out = fmt(SEVERAL.replace("leaf", "other"), lineup=True, pc=ALL)
    assert "//" not in out                              # aligned only
    assert {paren_col(ln) for ln in pin_lines(out)} == {40}


def test_unknown_port_gets_no_comment():
    src = """module top;
   leaf u_leaf (.clk(clk), .extra(x), .q(q));
endmodule
"""
    out = fmt(src, lineup=True, pc=ALL)
    pins = pin_lines(out)
    assert "//" not in pins[1]
    assert port_comment(pins[0]) == ["input", "wire"] and port_comment(pins[2]) == ["output", "reg"]


@pytest.mark.parametrize("body", [
    "(clk, q, d, o);",                                        # ordered
    "(.clk(clk),\n`ifdef FOO\n                .q(q),\n`endif\n                .data_in(d));",
    "( .clk(clk)\n              , .q(q));",                 # leading comma
    "(.clk(clk), /* a\n   b */ .q(q));",                     # multi-line block comment
    "(.clk(clk) /* x */, .q(q));",                            # comment before the comma
    "(.*);",                                                  # wildcard only
    "();",                                                    # empty
])
def test_skipped_lists_are_untouched(body):
    src = f"module top;\n   leaf u_leaf {body}\nendmodule\n"
    for lineup, pc in ((True, None), (False, ALL), (True, ALL)):
        assert fmt(src, lineup=lineup, pc=pc) == src


# ----------------------------------------------------------------------
# End to end: AutoEngine twice with Local Variables
# ----------------------------------------------------------------------

E2E_LEAF = """module e2e_leaf (
   input  logic       clk,
   input  logic [7:0] d,
   output logic [7:0] q,
   output logic       valid
);
endmodule
"""

E2E_TOP = """module e2e_top (/*AUTOARG*/);
   /*AUTOWIRE*/

   /* e2e_leaf AUTO_TEMPLATE (
      .valid (leaf_valid),
   ); */

   e2e_leaf u_leaf (.d(8'h00), /*AUTOINST*/);
endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
"""


def _expand(path, text):
    cfg = VerilogConfig(library_directories=[str(path.parent)])
    buf = VerilogBuffer.from_string(text, str(path))
    AutoEngine(cfg).run(buf, cfg)
    return buf.buffer_string()


def test_engine_end_to_end_idempotent(tmp_path):
    (tmp_path / "e2e_leaf.v").write_text(E2E_LEAF)
    top = tmp_path / "e2e_top.v"
    top.write_text(E2E_TOP)

    out1 = _expand(top, E2E_TOP)
    out2 = _expand(top, out1)
    assert out2 == out1

    pins = pin_lines(out1[out1.index("   e2e_leaf u_leaf"):])
    assert [code(p).split()[0] for p in pins] == [".d", ".q", ".valid", ".clk"]
    assert len({paren_col(ln) for ln in pins}) == 1
    assert [port_comment(ln) for ln in pins] == [
        ["input", "[7:0]", "logic"], ["output", "[7:0]", "logic"], ["output", "logic"], ["input", "logic"]]
    assert pins[2].endswith("// output       logic  // Templated")

    # The options-off expansion declares the same wires.
    base = _expand(top, E2E_TOP.split("// Local Variables:")[0])
    wires = lambda s: sorted(ln.split("//")[0].split() for ln in s.splitlines()  # noqa: E731
                             if ln.split()[:1] in (["wire"], ["logic"]))
    assert wires(out2) == wires(base) != []
