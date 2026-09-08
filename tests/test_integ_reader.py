"""Tests for pyverilog_auto.integ.reader (pyslang CST -> ModDecls).

Part (a): synthetic modules covering every conversion rule.
Part (b): equivalence with DeclParser on real fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.config import VerilogConfig  # noqa: E402
from pyverilog_auto.integ.database import SlangReaderDatabase  # noqa: E402
from pyverilog_auto.integ.reader import SlangModuleReader  # noqa: E402
from pyverilog_auto.integ.textscan import find_module_regions  # noqa: E402
from pyverilog_auto.library.module_db import ModuleDatabase  # noqa: E402
from pyverilog_auto.signal import ModDecls, Signal  # noqa: E402

import test_golden  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent


def _read(tmp_path: Path, text: str, name: str, cfg: VerilogConfig | None = None, fname: str = "m.sv") -> ModDecls:
    p = tmp_path / fname
    p.write_text(text)
    db = ModuleDatabase(cfg or VerilogConfig(), str(p))
    modi = db.module_inside_filename(name, str(p))
    assert modi is not None
    decls = SlangModuleReader(None).read_module_from_file(modi, cfg or VerilogConfig())
    assert decls is not None
    return decls


def _sig(s: Signal) -> tuple:
    return (s.name, s.bits, s.signed, s.type, s.memory, tuple(s.multidim) if s.multidim else None, s.modport)


# ----------------------------------------------------------------------
# (a) synthetic
# ----------------------------------------------------------------------

def test_ansi_ports_variable_and_net_headers(tmp_path):
    text = """
module m #(parameter W = 8, localparam L = 2, parameter int I = 3, parameter signed [3:0] S = 1,
           parameter type T = logic)
  (input wire clk, input wire logic rst_n, input [7:0] a, b,
   output reg [W-1:0] y, output logic signed [1:0][2:0] z [3:0],
   inout tri pad, input unsigned [3:0] u, input logic c, d,
   input var int cnt, input real r);
endmodule
"""
    d = _read(tmp_path, text, "m")
    ins = {s.name: _sig(s) for s in d.inputs}
    assert ins["clk"] == ("clk", None, None, "wire", None, None, None)
    assert ins["rst_n"] == ("rst_n", None, None, "wire logic", None, None, None)
    assert ins["a"] == ("a", "[7:0]", None, None, None, None, None)
    assert ins["b"] == ("b", "[7:0]", None, None, None, None, None)      # inherits from a
    assert ins["u"] == ("u", "[3:0]", "unsigned", None, None, None, None)
    assert ins["c"] == ("c", None, None, "logic", None, None, None)
    assert ins["d"] == ("d", None, None, "logic", None, None, None)
    assert ins["cnt"] == ("cnt", None, None, "int", None, None, None)
    assert ins["r"] == ("r", None, None, "real", None, None, None)
    outs = {s.name: _sig(s) for s in d.outputs}
    assert outs["y"] == ("y", "[W-1:0]", None, "reg", None, None, None)
    assert outs["z"] == ("z", "[2:0]", "signed", "logic", "[3:0]", ("[1:0]",), None)
    assert [_sig(s) for s in d.inouts] == [("pad", None, None, "tri", None, None, None)]
    gp = {s.name: _sig(s) for s in d.gparams}
    assert set(gp) == {"W", "I", "S", "T"}
    assert gp["I"] == ("I", None, None, "int", None, None, None)
    assert gp["S"] == ("S", "[3:0]", "signed", None, None, None, None)
    assert [s.name for s in d.consts] == ["L"]


def test_macro_widths_are_kept_symbolic(tmp_path):
    text = """
`define W 8
`define RNG [3:0]
module m (input [`W-1:0] a, output `RNG b, output [`W-1:`W-4] c);
endmodule
"""
    d = _read(tmp_path, text, "m")
    assert _sig(d.inputs[0])[1] == "[`W-1:0]"
    assert _sig(d.outputs[0])[1] == "`RNG"
    assert _sig(d.outputs[1])[1] == "[`W-1:`W-4]"


def test_interface_ports_and_typedef_regexp(tmp_path):
    text = """
package p; typedef logic [3:0] nib_t; endpackage
interface my_if; logic a; modport mp (input a); endinterface
module m (my_if.mp port_a, my_if port_b, input p::nib_t n, input other_t o, output foo_t [1:0] f);
endmodule
"""
    d = _read(tmp_path, text, "m")
    ifs = {s.name: _sig(s) for s in d.interfaces}
    assert ifs["port_a"] == ("port_a", None, None, "my_if", None, None, "mp")
    assert ifs["port_b"] == ("port_b", None, None, "my_if", None, None, None)
    # user types without a typedef regexp are treated as interface ports (verilog-mode rule)
    assert "n" in ifs and ifs["n"][3] == "p::nib_t"
    assert "o" in ifs and "f" in ifs
    cfg = VerilogConfig(typedef_regexp="_t$")
    d2 = _read(tmp_path, text, "m", cfg, fname="m2.sv")
    ins = {s.name: _sig(s) for s in d2.inputs}
    assert ins["n"] == ("n", None, None, "p::nib_t", None, None, None)
    assert ins["o"] == ("o", None, None, "other_t", None, None, None)
    assert {s.name for s in d2.interfaces} == {"port_a", "port_b"}


def test_non_ansi_body_ports_and_vars(tmp_path):
    text = """
module m (/*AUTOARG*/);
   input clk;
   input [7:0] a, b;
   output reg [3:0] q [0:1];
   inout wire w;
   wire [1:0] n1;
   logic [7:0] v1, v2;
   reg r1;
   my_t tv;
   localparam int LP = 2;
   parameter P = 4;
   genvar g;
   assign n1 = a[1:0];
   assign {v1, v2[3]} = 0;
   assign r1 = w;
   /*AUTO_CONSTANT(K1, K2)*/
endmodule
"""
    d = _read(tmp_path, text, "m", VerilogConfig(typedef_regexp="_t$"))
    ins = {s.name: _sig(s) for s in d.inputs}
    assert ins["clk"] == ("clk", None, None, None, None, None, None)
    assert ins["a"] == ("a", "[7:0]", None, None, None, None, None)
    assert ins["b"] == ("b", "[7:0]", None, None, None, None, None)
    assert [_sig(s) for s in d.outputs] == [("q", "[3:0]", None, "reg", "[0:1]", None, None)]
    assert [_sig(s) for s in d.inouts] == [("w", None, None, "wire", None, None, None)]
    vs = {s.name: _sig(s) for s in d.vars}
    assert vs["n1"] == ("n1", "[1:0]", None, None, None, None, None)
    assert vs["v2"] == ("v2", "[7:0]", None, None, None, None, None)
    assert vs["r1"][3] is None
    assert vs["tv"][3] == "my_t"
    assert [s.name for s in d.gparams] == ["P"]
    assert {s.name for s in d.consts} == {"LP", "g", "K1", "K2"}
    assert [s.name for s in d.assigns] == ["n1", "v1", "v2", "r1"]


def test_modports_and_clocking(tmp_path):
    text = """
interface bus (input logic clk);
   logic req, ack;
   logic [3:0] d;
   modport master (output req, d, input ack);
   modport slave (input req, input d, output ack);
   clocking cb @(posedge clk); input ack; output req, d; endclocking
   modport mon (clocking cb);
endinterface
"""
    d = _read(tmp_path, text, "bus")
    mps = {m.name: [(s.name, s.type) for s in m.signals] for m in d.modports}
    assert mps["master"] == [("req", "output"), ("d", "output"), ("ack", "input")]
    assert mps["slave"] == [("req", "input"), ("d", "input"), ("ack", "output")]
    assert mps["cb"] == [("ack", "input"), ("req", "output"), ("d", "output")]
    assert mps["mon"] == []
    assert [s.name for s in d.inputs] == ["clk"]
    assert {s.name for s in d.vars} == {"req", "ack", "d"}


def test_generate_blocks_are_scanned(tmp_path):
    text = """
module m (input clk);
   generate
      if (1) begin : g1
         wire w1;
      end else begin : g2
         wire w2;
      end
      for (genvar i = 0; i < 2; i++) begin : g3
         logic [7:0] w3;
      end
   endgenerate
   always @(posedge clk) begin : blk
      reg not_a_var;
   end
   function automatic int f(input int x); return x; endfunction
endmodule
"""
    d = _read(tmp_path, text, "m")
    # DeclParser also counts declarations inside procedural blocks (not functions)
    assert {s.name for s in d.vars} == {"w1", "w2", "w3", "not_a_var"}


def test_ifdef_forces_text_parser(tmp_path):
    text = """
module m (input a
`ifdef X
 , input b
`endif
);
endmodule
"""
    p = tmp_path / "m.sv"
    p.write_text(text)
    db = ModuleDatabase(VerilogConfig(), str(p))
    modi = db.module_inside_filename("m", str(p))
    reader = SlangModuleReader(None, directive_policy="text")
    assert reader.read_module_from_file(modi, VerilogConfig()) is None
    reader2 = SlangModuleReader(None, directive_policy="preprocess")
    d = reader2.read_module_from_file(modi, VerilogConfig())
    assert [s.name for s in d.inputs] == ["a"]
    # The database falls back to DeclParser, which sees both branches (Emacs behavior)
    sdb = SlangReaderDatabase(VerilogConfig(), str(p))
    d3 = sdb.get_decls(modi)
    assert [s.name for s in d3.inputs] == ["a", "b"]


def test_same_module_name_twice_picks_by_point(tmp_path):
    text = "module m (input a); endmodule\nmodule m (input b, input c); endmodule\n"
    p = tmp_path / "m.sv"
    p.write_text(text)
    db = ModuleDatabase(VerilogConfig(), str(p))
    first = db.module_inside_filename("m", str(p))
    reader = SlangModuleReader(None)
    assert [s.name for s in reader.read_module_from_file(first, VerilogConfig()).inputs] == ["a"]
    from pyverilog_auto.signal import Modi

    second = Modi(name="m", filepath=str(p), point=text.index("module m (input b") + len("module m"), type="module")
    assert [s.name for s in reader.read_module_from_file(second, VerilogConfig()).inputs] == ["b", "c"]


# ----------------------------------------------------------------------
# (b) equivalence with DeclParser on fixtures
# ----------------------------------------------------------------------

FIXTURES = [
    "ExampInst.v", "autoinst_signed.v", "autoinst_signed_fubar2.v", "autoinst_vec_t.v",
    "autoinst_multidim.v", "autoinst_array.v", "autoinst_interface_sub.v", "autoinst_iface270_sub.v",
    "autoinst_iface_noparam.v", "autoinstparam_first_sub.v", "autoinst_paramover_sub.v",
    "autoinst_param_type.v", "autoinst_unsigned_bug302.v", "autoinst_2k_fredriksen.v",
    "autoinst_sv_kulkarni_base.v", "autoinst_lopaz_srpad.v", "autoinst_tennant.v", "autoinst_rogoff.v",
    "autoinst_brucet_library.v", "autoinst_crawford_array_a.v", "autoinst_wildcard_sub.v",
    "autoinst_import2012.v", "autoinst_modport_param.v", "automodport_if.v", "ExampInoutModport.v",
    "autoinoutmodport_prefix.v", "v2k_typedef_yee_sub1.v", "v2k_typedef_yee_sub2.v",
    "../sample_env/rtl/uart_tx.v", "../sample_env/rtl/uart_rx.v", "../sample_env/rtl/spi_master.v",
    "../sample_env/rtl/gpio_port.v",
]

# fixture -> reason (documented divergences between the CST reader and DeclParser)
_KNOWN_DIVERGENCES: dict[str, str] = {}


def _decls_key(d: ModDecls) -> dict:
    return {
        "inputs": [_sig(s) for s in d.inputs],
        "outputs": [_sig(s) for s in d.outputs],
        "inouts": [_sig(s) for s in d.inouts],
        "interfaces": [_sig(s) for s in d.interfaces],
        "gparams": [_sig(s) for s in d.gparams],
        "vars": sorted((s.name, s.bits) for s in d.vars),
        "consts": sorted(s.name for s in d.consts),
        "modports": [m.name for m in d.modports],
    }


@pytest.mark.parametrize("fixture", FIXTURES)
def test_reader_matches_declparser(fixture, tmp_path):
    path = (TESTS_DIR / fixture).resolve()
    if not path.exists():
        pytest.skip(f"{fixture} missing")
    if fixture in _KNOWN_DIVERGENCES:
        pytest.xfail(_KNOWN_DIVERGENCES[fixture])
    cfg = test_golden.build_config_for_test(path, tmp_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    names = [r.name for r in find_module_regions(text)]
    assert names, "no modules found"
    db = ModuleDatabase(cfg, str(path))
    reader = SlangModuleReader(None)
    compared = 0
    for name in names:
        modi = db.module_inside_filename(name, str(path))
        if modi is None:
            continue
        got = reader.read_module_from_file(modi, cfg)
        if got is None:
            continue   # directive policy: text parser is used for this module
        want = db.get_decls(modi)
        assert _decls_key(got) == _decls_key(want), f"module {name}"
        compared += 1
    if compared == 0:
        pytest.skip("every module of this fixture is read by the text parser (directives/parse errors)")
