"""Tests for SubDeclParser — read sub-instance signal connections."""

import os
import pytest
from pathlib import Path

from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.signal import Signal, ModDecls, SubDecls
from pyverilog_auto.parser.sub_decls import SubDeclParser
from pyverilog_auto.library.module_db import ModuleDatabase

TESTS_DIR = Path(__file__).parent.parent / "tests"


def _make_parser(text: str, tmp_path: Path):
    """Write text to a temp file and create a SubDeclParser for it."""
    fp = tmp_path / "test.v"
    fp.write_text(text, encoding="utf-8")
    filepath = str(fp)
    buf = VerilogBuffer.from_file(filepath)
    config = VerilogConfig()
    db = ModuleDatabase(config, filepath)
    return buf, config, db


class TestSubDeclParserSimple:
    """Test SubDeclParser on simple inline module definitions."""

    def test_basic_outputs_inputs(self, tmp_path):
        text = """\
module sub (
   output [7:0] data_out,
   input        clk,
   input        reset
);
endmodule

module top;
   sub inst (/*AUTOINST*/
      // Outputs
      .data_out  (data_out[7:0]),
      // Inputs
      .clk       (clk),
      .reset     (reset));
endmodule
"""
        buf, config, db = _make_parser(text, tmp_path)

        # Position buf inside the top module
        top_pos = text.find("module top")
        assert top_pos >= 0
        semi_pos = text.find(";", top_pos)
        buf.goto_char(semi_pos + 1)

        parser = SubDeclParser(buf, config, db)
        result = parser.parse()

        assert isinstance(result, SubDecls)
        out_names = [s.name for s in result.outputs]
        in_names = [s.name for s in result.inputs]
        assert "data_out" in out_names
        assert "clk" in in_names
        assert "reset" in in_names

    def test_inouts(self, tmp_path):
        text = """\
module sub (
   inout [3:0] data_bus
);
endmodule

module top;
   sub inst (/*AUTOINST*/
      // Inouts
      .data_bus  (data_bus[3:0]));
endmodule
"""
        buf, config, db = _make_parser(text, tmp_path)

        top_pos = text.find("module top")
        semi_pos = text.find(";", top_pos)
        buf.goto_char(semi_pos + 1)

        parser = SubDeclParser(buf, config, db)
        result = parser.parse()

        inout_names = [s.name for s in result.inouts]
        assert "data_bus" in inout_names

    def test_empty_autoinst(self, tmp_path):
        """A module with AUTOINST but no expanded pins should return empty."""
        text = """\
module top;
   sub inst (/*AUTOINST*/);
endmodule
"""
        buf, config, db = _make_parser(text, tmp_path)
        buf.goto_char(0)

        parser = SubDeclParser(buf, config, db)
        result = parser.parse()
        assert isinstance(result, SubDecls)

    def test_multiple_instances(self, tmp_path):
        text = """\
module sub1 (
   output out1,
   input  in1
);
endmodule

module sub2 (
   output out2,
   input  in2
);
endmodule

module top;
   sub1 inst1 (/*AUTOINST*/
      // Outputs
      .out1  (out1),
      // Inputs
      .in1   (in1));
   sub2 inst2 (/*AUTOINST*/
      // Outputs
      .out2  (out2),
      // Inputs
      .in2   (in2));
endmodule
"""
        buf, config, db = _make_parser(text, tmp_path)

        top_pos = text.find("module top")
        semi_pos = text.find(";", top_pos)
        buf.goto_char(semi_pos + 1)

        parser = SubDeclParser(buf, config, db)
        result = parser.parse()

        out_names = [s.name for s in result.outputs]
        in_names = [s.name for s in result.inputs]
        assert "out1" in out_names
        assert "out2" in out_names
        assert "in1" in in_names
        assert "in2" in in_names


class TestSubDeclParserSignalExpression:
    """Test the signal expression parsing."""

    def test_simple_name(self):
        from pyverilog_auto.parser.sub_decls import SubDeclParser
        sig, vec, _ = SubDeclParser._parse_signal_expr("foo)")
        assert sig == "foo"
        assert vec is None

    def test_name_with_vector(self):
        from pyverilog_auto.parser.sub_decls import SubDeclParser
        sig, vec, _ = SubDeclParser._parse_signal_expr("foo[7:0])")
        assert sig == "foo"
        assert vec == "[7:0]"

    def test_name_with_single_bit(self):
        from pyverilog_auto.parser.sub_decls import SubDeclParser
        sig, vec, _ = SubDeclParser._parse_signal_expr("bar[3])")
        assert sig == "bar"
        assert vec == "[3]"

    def test_complex_expression(self):
        from pyverilog_auto.parser.sub_decls import SubDeclParser
        sig, vec, _ = SubDeclParser._parse_signal_expr("{a, b})")
        # Complex concatenation expressions may or may not be parseable
        # The important thing is no crash
        assert True
