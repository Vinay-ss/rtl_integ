"""Tests for pyverilog_auto.regex_compat."""

import re

import pytest

from pyverilog_auto.regex_compat import (
    emacs_re_compile,
    string_match_fold,
    translate_emacs_regex,
)


class TestTranslateEmacsRegex:
    """Each Emacs→Python conversion gets its own test."""

    def test_whitespace_class(self):
        assert translate_emacs_regex(r"\s-") == r"[ \t\n\f\r]"

    def test_underscore_class(self):
        assert translate_emacs_regex(r"\s_") == "_"

    def test_word_constituent_class(self):
        assert translate_emacs_regex(r"\sw") == "[A-Za-z0-9_]"

    def test_punctuation_class(self):
        assert translate_emacs_regex(r"\s.") == r"[!-/:-@[-`{-~]"

    def test_word_boundary_start(self):
        result = translate_emacs_regex(r"\<")
        assert result == r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_])"

    def test_word_boundary_end(self):
        result = translate_emacs_regex(r"\>")
        assert result == r"(?<=[A-Za-z0-9_])(?![A-Za-z0-9_])"

    def test_w_shortcut(self):
        assert translate_emacs_regex(r"\w+") == "[A-Za-z0-9_]+"

    def test_W_shortcut(self):
        assert translate_emacs_regex(r"\W") == "[^A-Za-z0-9_]"

    def test_grouping(self):
        assert translate_emacs_regex(r"\(foo\)") == "(foo)"

    def test_alternation(self):
        assert translate_emacs_regex(r"a\|b") == "a|b"

    def test_buffer_start(self):
        assert translate_emacs_regex(r"\`") == r"\A"

    def test_buffer_end(self):
        assert translate_emacs_regex(r"\'") == r"\Z"

    def test_literal_parens(self):
        """Bare ``(`` and ``)`` in Emacs are literal; they should
        become ``\\(`` and ``\\)`` in Python."""
        result = translate_emacs_regex("foo(bar)")
        assert result == r"foo\(bar\)"

    def test_combined(self):
        """A pattern using multiple Emacs features at once."""
        pat = r"\(output\|input\)\s-+\sw+"
        result = translate_emacs_regex(pat)
        compiled = re.compile(result)
        assert compiled.search("output  clk")
        assert compiled.search("input  data_in")
        assert not compiled.search("parameter FOO")

    def test_unknown_escape_passthrough(self):
        """Unknown backslash sequences (like \\n) pass through."""
        assert translate_emacs_regex(r"\n") == r"\n"


class TestEmacsReCompile:
    def test_compiles_and_matches(self):
        pat = emacs_re_compile(r"\(module\|interface\)")
        assert pat.search("module foo")
        assert pat.search("interface bar")
        assert not pat.search("class baz")


class TestStringMatchFold:
    def test_case_insensitive(self):
        assert string_match_fold(r"module", "MODULE foo", case_fold=True)

    def test_case_sensitive_no_match(self):
        assert string_match_fold(r"module", "MODULE foo", case_fold=False) is None

    def test_returns_match_object(self):
        m = string_match_fold(r"\(wire\)", "  wire  clk", case_fold=True)
        assert m is not None
        assert m.group(1) == "wire"
