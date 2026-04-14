"""Tests for TemplateParser — AUTO_TEMPLATE parsing."""

import pytest
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.signal import Signal
from pyverilog_auto.parser.template_parser import TemplateParser, TemplateResult, TemplateEntry


class TestTemplateParserBasic:
    """Test basic template parsing."""

    def test_simple_template(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .port_a  (sig_a),
    .port_b  (sig_b[7:0]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        # Position at the AUTOINST marker
        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert isinstance(result, TemplateResult)
        assert len(result.sig_list) == 2
        names = [e.port_pattern for e in result.sig_list]
        assert "port_a" in names
        assert "port_b" in names
        # Check connection expressions
        for entry in result.sig_list:
            if entry.port_pattern == "port_a":
                assert entry.signal_expr == "sig_a"
            elif entry.port_pattern == "port_b":
                assert entry.signal_expr == "sig_b[7:0]"

    def test_template_with_at_substitution(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .clk  (clk_@),
    ); */

SubMod inst0 (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert len(result.sig_list) == 1
        assert result.sig_list[0].signal_expr == "clk_@"

    def test_template_with_brackets(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .data  (data_bus[]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert len(result.sig_list) == 1
        assert result.sig_list[0].signal_expr == "data_bus[]"

    def test_no_template_returns_empty(self):
        text = """\
SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert result.regexp == ""
        assert len(result.sig_list) == 0
        assert len(result.wild_list) == 0

    def test_template_with_regexp(self):
        """Template with custom regexp for instance name matching."""
        text = """\
/* SubMod AUTO_TEMPLATE "\\(.*\\)" (
    .a  (@_a_value[]),
    ); */

SubMod foo1_1 (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        # The regexp should be captured
        assert result.regexp != ""
        assert len(result.sig_list) == 1
        assert result.sig_list[0].signal_expr == "@_a_value[]"


class TestTemplateParserWildcard:
    """Test wildcard (regex) port patterns in templates."""

    def test_wildcard_with_at(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .sd0_adrs@  (c@_sd_adrs[\\1]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert len(result.wild_list) >= 1

    def test_wildcard_with_backslash_groups(self):
        text = r"""/* SubMod AUTO_TEMPLATE (
    .sd0_dqm\(.*\)_l  (c@_sd_dqm_[\1]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert len(result.wild_list) >= 1


class TestTemplateApply:
    """Test applying template entries to ports."""

    def test_apply_simple(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .port_a  (mapped_a),
    .port_b  (mapped_b[7:0]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")

        port_a = Signal(name="port_a", bits=None)
        sig = parser.apply(port_a, result, tpl_num="0")
        assert sig == "mapped_a"

        port_b = Signal(name="port_b", bits="[7:0]")
        sig = parser.apply(port_b, result, tpl_num="0")
        assert sig == "mapped_b[7:0]"

    def test_apply_at_substitution(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .clk  (clk_@),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")

        port = Signal(name="clk")
        sig = parser.apply(port, result, tpl_num="5")
        assert sig == "clk_5"

    def test_apply_bracket_substitution(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .data  (data_bus[]),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")

        port = Signal(name="data", bits="[15:0]")
        sig = parser.apply(port, result, tpl_num="0")
        assert sig == "data_bus[15:0]"

    def test_apply_no_match(self):
        text = """\
/* SubMod AUTO_TEMPLATE (
    .port_a  (mapped_a),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")

        port = Signal(name="port_x")
        sig = parser.apply(port, result, tpl_num="0")
        assert sig is None


class TestMultipleTemplates:
    """Test parsing with multiple templates for the same or different modules."""

    def test_nearest_template_wins(self):
        """The last template before AUTOINST should be used."""
        text = """\
/* SubMod AUTO_TEMPLATE (
    .a  (first_a),
    ); */

/* SubMod AUTO_TEMPLATE (
    .a  (second_a),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        # The last (nearest) template before AUTOINST should win
        assert len(result.sig_list) >= 1
        assert result.sig_list[0].signal_expr == "second_a"

    def test_multi_module_templates(self):
        """Templates for different modules stacked together."""
        text = """\
/*
  SubA AUTO_TEMPLATE
  SubB AUTO_TEMPLATE
  SubC AUTO_TEMPLATE (
    .a  (Boo@),
    ); */

SubC inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubC")
        assert len(result.sig_list) == 1
        assert result.sig_list[0].signal_expr == "Boo@"

    def test_template_with_comments(self):
        """Template body can contain comments."""
        text = """\
/* SubMod AUTO_TEMPLATE (
    // This is a comment
    .port_a  (sig_a),
    /* block comment */
    .port_b  (sig_b),
    ); */

SubMod inst (/*AUTOINST*/);
"""
        buf = VerilogBuffer(text)
        config = VerilogConfig()
        parser = TemplateParser(buf, config)

        pos = text.find("/*AUTOINST*/")
        buf.goto_char(pos)

        result = parser.parse("SubMod")
        assert len(result.sig_list) == 2
