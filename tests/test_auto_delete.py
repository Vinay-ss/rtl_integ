"""Tests for AutoDeleter — AUTO section deletion."""

import pytest
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.auto.delete import AutoDeleter


class TestDeleteAutosLined:
    """Test deletion of multi-line AUTO sections (// Beginning ... // End)."""

    def test_delete_autowire_section(self):
        text = """\
module top;
   /*AUTOWIRE*/
   // Beginning of automatic wires (for undeclared instantiated-module outputs)
   wire [7:0]   bus_a;
   wire          enable;
   // End of automatics
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "// Beginning" not in result
        assert "// End of automatics" not in result
        assert "wire [7:0]" not in result
        assert "/*AUTOWIRE*/" in result
        assert "endmodule" in result

    def test_delete_autoinput_section(self):
        text = """\
module top;
   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input [7:0]   data;
   input          clk;
   // End of automatics
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "// Beginning" not in result
        assert "input" not in result
        assert "/*AUTOINPUT*/" in result

    def test_preserve_marker_when_no_beginning(self):
        text = """\
module top;
   /*AUTOWIRE*/
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "/*AUTOWIRE*/" in result
        assert "endmodule" in result

    def test_delete_autooutput_section(self):
        text = """\
module top;
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output [31:0]  data_out;
   // End of automatics
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "output" not in result
        assert "/*AUTOOUTPUT*/" in result


class TestDeleteToParen:
    """Test deletion of paren-delimited AUTO sections (AUTOINST, AUTOARG)."""

    def test_delete_autoinst_pins(self):
        text = """\
module top;
   sub inst (/*AUTOINST*/
      // Outputs
      .out1   (out1),
      // Inputs
      .clk    (clk));
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        # The AUTOINST marker should remain
        assert "/*AUTOINST*/" in result
        # The pins should be deleted (they're between AUTOINST and closing paren)
        assert ".out1" not in result

    def test_delete_autoarg(self):
        text = """\
module top (/*AUTOARG*/
   // Outputs
   data_out,
   // Inputs
   clk, reset
   );
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "/*AUTOARG*/" in result
        # The auto-generated arg list should be deleted
        assert "data_out" not in result


class TestDeleteTemplateComments:
    """Test deletion of trailing // Templated comments."""

    def test_remove_templated_comment(self):
        text = """\
      .port1   (sig1), // Templated
      .port2   (sig2));
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "// Templated" not in result
        assert ".port1" in result

    def test_remove_templated_with_line_numbers(self):
        text = """\
      .port1   (sig1), // Templated T1 L5
      .port2   (sig2));
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "// Templated" not in result

    def test_remove_implicit_star(self):
        text = """\
      .port1   (sig1), // Implicit .*
      .port2   (sig2));
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "// Implicit .*" not in result

    def test_remove_autonohookup(self):
        text = """\
      .port1   (sig1), // Templated AUTONOHOOKUP
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "AUTONOHOOKUP" not in result


class TestMultipleAutos:
    """Test deletion when multiple AUTO markers are present."""

    def test_multiple_sections(self):
        text = """\
module top;
   /*AUTOWIRE*/
   // Beginning of automatic wires
   wire foo;
   // End of automatics

   /*AUTOREG*/
   // Beginning of automatic regs
   reg bar;
   // End of automatics
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "wire foo" not in result
        assert "reg bar" not in result
        assert "/*AUTOWIRE*/" in result
        assert "/*AUTOREG*/" in result

    def test_combined_inst_and_wire(self):
        text = """\
module top;
   /*AUTOWIRE*/
   // Beginning of automatic wires
   wire [7:0] data;
   // End of automatics

   sub inst (/*AUTOINST*/
      // Outputs
      .data   (data[7:0]),
      // Inputs
      .clk    (clk));
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        result = buf.buffer_string()
        assert "wire [7:0] data" not in result
        assert "/*AUTOWIRE*/" in result
        assert "/*AUTOINST*/" in result


class TestDeleteAutoStar:
    """Test AUTOSTAR (.* expansion) deletion."""

    def test_delete_star_safe(self):
        """When .* is followed only by ) or section comments, it's safe to delete."""
        text = """\
module top;
   sub inst (.*);
endmodule
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        deleter = AutoDeleter(buf, config)
        deleter.delete()
        # The .* itself isn't an AUTO marker — it needs expanded pins to be "safe"
        result = buf.buffer_string()
        # .* with no AUTOINST context — the delete should not crash
        assert "endmodule" in result
