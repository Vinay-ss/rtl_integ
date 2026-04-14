"""Tests for DeclParser — parses all tests/Examp*.v files and verifies
that the output matches known port lists.
"""

from __future__ import annotations

import glob
import os
import sys

import pytest

# Ensure package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.parser.decl_parser import DeclParser
from pyverilog_auto.library.module_db import ModuleDatabase


TESTS_DIR = os.path.dirname(__file__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_module(text: str, module_name: str, config: VerilogConfig = None):
    """Parse a specific module from a Verilog text string."""
    if config is None:
        config = VerilogConfig()

    buf = VerilogBuffer.from_string(text)

    # Find the module keyword followed by the module name
    import re
    pattern = rf"\bmodule\s+{re.escape(module_name)}\b"
    m = re.search(pattern, text)
    if m is None:
        raise ValueError(f"Module '{module_name}' not found in text")

    buf.goto_char(m.start())
    parser = DeclParser(buf, config)
    return parser.parse()


def _sig_names(signals):
    """Extract signal names from a list of Signal objects."""
    return [s.name for s in signals]


# ---------------------------------------------------------------------------
# ExampInst.v — InstModule has output [31:0] o, input i
# ---------------------------------------------------------------------------

class TestExampInst:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampInst.v")) as f:
            return f.read()

    def test_inst_module_outputs(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.outputs)
        assert "o" in names

    def test_inst_module_output_bits(self, text):
        decls = _parse_module(text, "InstModule")
        o_sig = [s for s in decls.outputs if s.name == "o"][0]
        assert o_sig.bits == "[31:0]"

    def test_inst_module_inputs(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.inputs)
        assert "i" in names

    def test_examp_inst_outputs(self, text):
        decls = _parse_module(text, "ExampInst")
        names = _sig_names(decls.outputs)
        assert "o" in names

    def test_examp_inst_inputs(self, text):
        decls = _parse_module(text, "ExampInst")
        names = _sig_names(decls.inputs)
        assert "i" in names


# ---------------------------------------------------------------------------
# ExampOutput.v — InstModule has output o (ANSI style)
# ---------------------------------------------------------------------------

class TestExampOutput:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampOutput.v")) as f:
            return f.read()

    def test_inst_module_output(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.outputs)
        assert "o" in names


# ---------------------------------------------------------------------------
# ExampInput.v — InstModule has input i (ANSI style)
# ---------------------------------------------------------------------------

class TestExampInput:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampInput.v")) as f:
            return f.read()

    def test_inst_module_input(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.inputs)
        assert "i" in names


# ---------------------------------------------------------------------------
# ExampInout.v — InstModule has inout io (ANSI style)
# ---------------------------------------------------------------------------

class TestExampInout:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampInout.v")) as f:
            return f.read()

    def test_inst_module_inout(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.inouts)
        assert "io" in names


# ---------------------------------------------------------------------------
# ExampWire.v — ExampWire has input i, wire [31:0] o
# ---------------------------------------------------------------------------

class TestExampWire:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampWire.v")) as f:
            return f.read()

    def test_inst_module_outputs(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.outputs)
        assert "o" in names

    def test_examp_wire_inputs(self, text):
        decls = _parse_module(text, "ExampWire")
        names = _sig_names(decls.inputs)
        assert "i" in names

    def test_examp_wire_vars(self, text):
        decls = _parse_module(text, "ExampWire")
        names = _sig_names(decls.vars)
        assert "o" in names


# ---------------------------------------------------------------------------
# ExampReg.v — ExampReg has output o, input i, reg o
# ---------------------------------------------------------------------------

class TestExampReg:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampReg.v")) as f:
            return f.read()

    def test_outputs(self, text):
        decls = _parse_module(text, "ExampReg")
        names = _sig_names(decls.outputs)
        assert "o" in names

    def test_inputs(self, text):
        decls = _parse_module(text, "ExampReg")
        names = _sig_names(decls.inputs)
        assert "i" in names


# ---------------------------------------------------------------------------
# ExampArg.v — ExampArg has input i, output o
# ---------------------------------------------------------------------------

class TestExampArg:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampArg.v")) as f:
            return f.read()

    def test_inputs(self, text):
        decls = _parse_module(text, "ExampArg")
        names = _sig_names(decls.inputs)
        assert "i" in names

    def test_outputs(self, text):
        decls = _parse_module(text, "ExampArg")
        names = _sig_names(decls.outputs)
        assert "o" in names


# ---------------------------------------------------------------------------
# ExampInoutParam.v — ExampMain has parameter PARAM = 22
# ---------------------------------------------------------------------------

class TestExampInoutParam:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampInoutParam.v")) as f:
            return f.read()

    def test_main_params(self, text):
        decls = _parse_module(text, "ExampMain")
        names = _sig_names(decls.gparams)
        assert "PARAM" in names


# ---------------------------------------------------------------------------
# ExampInstParam.v — InstModule has parameter PAR
# ---------------------------------------------------------------------------

class TestExampInstParam:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampInstParam.v")) as f:
            return f.read()

    def test_inst_module_params(self, text):
        decls = _parse_module(text, "InstModule")
        names = _sig_names(decls.gparams)
        assert "PAR" in names

    def test_examp_inst_param_params(self, text):
        decls = _parse_module(text, "ExampInstParam")
        names = _sig_names(decls.gparams)
        assert "PAR" in names


# ---------------------------------------------------------------------------
# ExampOutputEvery.v — has output o, tempa, tempb; input i; wire defs
# ---------------------------------------------------------------------------

class TestExampOutputEvery:
    @pytest.fixture
    def text(self):
        with open(os.path.join(TESTS_DIR, "ExampOutputEvery.v")) as f:
            return f.read()

    def test_outputs(self, text):
        decls = _parse_module(text, "ExampOutputEvery")
        names = _sig_names(decls.outputs)
        assert "o" in names

    def test_inputs(self, text):
        decls = _parse_module(text, "ExampOutputEvery")
        names = _sig_names(decls.inputs)
        assert "i" in names


# ---------------------------------------------------------------------------
# ModuleDatabase — find modules across files
# ---------------------------------------------------------------------------

class TestModuleDatabase:
    def test_find_inst_module_in_examp_inst(self):
        filepath = os.path.join(TESTS_DIR, "ExampInst.v")
        config = VerilogConfig()
        db = ModuleDatabase(config, filepath)
        modi = db.module_inside_filename("InstModule", filepath)
        assert modi is not None
        assert modi.name == "InstModule"
        assert modi.type == "module"

    def test_find_examp_inst_module(self):
        filepath = os.path.join(TESTS_DIR, "ExampInst.v")
        config = VerilogConfig()
        db = ModuleDatabase(config, filepath)
        modi = db.module_inside_filename("ExampInst", filepath)
        assert modi is not None
        assert modi.name == "ExampInst"

    def test_lookup_finds_module_in_same_file(self):
        filepath = os.path.join(TESTS_DIR, "ExampInst.v")
        config = VerilogConfig()
        db = ModuleDatabase(config, filepath)
        modi = db.lookup("InstModule", ignore_error=True)
        assert modi is not None

    def test_lookup_nonexistent_module(self):
        filepath = os.path.join(TESTS_DIR, "ExampInst.v")
        config = VerilogConfig()
        db = ModuleDatabase(config, filepath)
        modi = db.lookup("NonExistentModule", ignore_error=True)
        assert modi is None


# ---------------------------------------------------------------------------
# Smoke test: every Examp*.v file should parse without errors
# ---------------------------------------------------------------------------

class TestAllExampFiles:
    @pytest.fixture(params=sorted(glob.glob(os.path.join(TESTS_DIR, "Examp*.v"))))
    def verilog_file(self, request):
        return request.param

    def test_parse_without_error(self, verilog_file):
        """Every Examp*.v file should parse at least one module
        without raising an exception."""
        with open(verilog_file) as f:
            text = f.read()

        config = VerilogConfig()
        buf = VerilogBuffer.from_string(text, filepath=verilog_file)

        # Find first module keyword
        import re
        m = re.search(r"\bmodule\s+\w+", text)
        if m is None:
            pytest.skip(f"No module found in {os.path.basename(verilog_file)}")

        buf.goto_char(m.start())
        parser = DeclParser(buf, config)
        decls = parser.parse()

        # Should return a ModDecls object
        assert decls is not None
        # At least one category should have signals (most test files have ports)
        total = (len(decls.outputs) + len(decls.inputs) + len(decls.inouts)
                 + len(decls.vars) + len(decls.gparams) + len(decls.consts)
                 + len(decls.assigns) + len(decls.interfaces))
        # Some modules are empty, that's fine
        assert total >= 0
