"""Indentation engine for Verilog buffers.

Ported from ``verilog-calculate-indent``, ``verilog-do-indent``, and
``verilog-indent-buffer`` in ``verilog-mode.el`` (lines ~5993-7337).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from .token import Tokenizer

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


# ---------------------------------------------------------------------------
# Regex patterns ported from verilog-mode.el
# ---------------------------------------------------------------------------

# Directive patterns
_DIRECTIVE_RE = re.compile(r"[ \t]*`[a-zA-Z_]")
_DIRECTIVE_RE_1 = re.compile(r"[ \t]*`[a-zA-Z_]\w*")
_DIRECTIVE_BEGIN = re.compile(r"`(?:for|if|ifdef|ifndef|switch|while)\b")
_DIRECTIVE_MIDDLE = re.compile(r"`(?:else|elsif|default|case)\b")
_DIRECTIVE_END = re.compile(r"`(?:endfor|endif|endswitch|endwhile)\b")

# End-block keywords
_END_BLOCK_RE = re.compile(
    r"\b(?:end|endcase|join|join_any|join_none|endclass|endtable"
    r"|endspecify|endfunction|endgenerate|endtask|endgroup"
    r"|endproperty|endinterface|endpackage|endprogram|endsequence"
    r"|endclocking)\b"
    r"|`(?:ovm_component_utils_end|ovm_field_utils_end|ovm_object_utils_end"
    r"|ovm_sequence_utils_end|ovm_sequencer_utils_end"
    r"|uvm_component_utils_end|uvm_field_utils_end|uvm_object_utils_end"
    r"|uvm_sequence_utils_end|uvm_sequencer_utils_end"
    r"|vmm_data_member_end|vmm_env_member_end"
    r"|vmm_scenario_member_end|vmm_subenv_member_end"
    r"|vmm_xactor_member_end)\b"
)

# Begin-block keywords (ordered, for verilog-calc-1)
_BEG_BLOCK_RE = re.compile(
    r"\b(?:begin|case|casex|casez|randcase|clocking|generate|fork"
    r"|function|property|specify|table|task|class|covergroup"
    r"|sequence|randsequence)\b"
    r"|`(?:ovm_[a-z_]+_begin|uvm_[a-z_]+_begin|vmm_[a-z_]+_member_begin)\b"
)

# Defun-level keywords (module/interface/package/class etc.)
_DEFUN_LEVEL_RE = re.compile(
    r"\b(?:connectmodule|module|macromodule|primitive|class|program"
    r"|interface|package|config"
    r"|initial|final|always|always_comb|always_ff|always_latch|analog"
    r"|endtask|endfunction)\b"
)

_DEFUN_LEVEL_NOT_GENERATE_RE = re.compile(
    r"\b(?:connectmodule|module|macromodule|primitive|class|program"
    r"|interface|package|config)\b"
)

_DEFUN_LEVEL_GENERATE_ONLY_RE = re.compile(
    r"\b(?:initial|final|always|always_comb|always_ff|always_latch"
    r"|analog|endtask|endfunction)\b"
)

# cpp-level (end of module etc.)
_CPP_LEVEL_RE = re.compile(
    r"\b(?:endconnectmodule|endmodule|endprimitive|endinterface"
    r"|endpackage|endprogram|endclass)\b"
)

# verilog-ends-re — used in else-matching backward search
# Group mapping:
#   1=else, 2=if, 3=assert, 4=end, 5=endcase, 6=endfunction,
#   7=endtask, 8=endspecify, 9=endtable, 10=endgenerate,
#   11=join*, 12=endclass, 13=endgroup
_ENDS_RE = re.compile(
    r"(?:\b(else)\b"
    r"|\b(if)\b"
    r"|\b(assert)\b"
    r"|\b(end)\b"
    r"|\b(endcase)\b"
    r"|\b(endfunction)\b"
    r"|\b(endtask)\b"
    r"|\b(endspecify)\b"
    r"|\b(endtable)\b"
    r"|\b(endgenerate)\b"
    r"|\b(join(?:_any|_none)?)\b"
    r"|\b(endclass)\b"
    r"|\b(endgroup)\b)"
)

# Zero-indent keywords (module declarations)
_ZERO_INDENT_RE = re.compile(
    r"\b(?:connectmodule|module|macromodule|primitive|program|interface|package|config"
    r"|endconnectmodule|endmodule|endprimitive|endprogram|endinterface|endpackage|endconfig)\b"
)

# Extended case regex
_EXTENDED_CASE_RE = re.compile(
    r"(?:unique0?\s+|priority\s+)?case[xz]?\b|\brandcase\b"
)

# disable/wait fork
_DISABLE_FORK_RE = re.compile(r"(?:disable|wait)\s+fork\b")

# Default clocking
_DEFAULT_CLOCKING_RE = re.compile(r"\bdefault\s+clocking\s+[A-Za-z_]\w*\s*;")

# DPI import/export
_DPI_IMPORT_EXPORT_RE = re.compile(
    r'(?:import|export)\s+"DPI(?:-C)?"\s+(?:(?:context|pure)\s+)?'
    r'(?:[A-Za-z_]\w*\s*=\s*)?(?:function|task)\b'
)

# Property/assert regex
_PROPERTY_RE = re.compile(
    r"(?:\w+\s*:\s*)?(?:assert|assume|cover|restrict)\s+(?:property|sequence)\b"
)

# Complete-re (statement-completing keywords)
_COMPLETE_RE = re.compile(
    r"\b(?:always(?:_latch|_ff|_comb)?|analog|assign|connectmodule|constraint"
    r"|import|initial|final|module|macromodule|repeat|randcase|while"
    r"|if|for|forever|foreach|else|parameter|do|localparam|assert|default|generate)\b"
    r"|(?:(?:extern\s+|(?:(?:pure|context)\s+)?virtual\s+|local\s+|protected\s+"
    r"|static\s+)*(?:function|task)\b)"
    r"|(?:(?:typedef\s+)*(?:struct|union|class)\b)"
)

# Behavioral block beginning
_BEHAVIORAL_BLOCK_BEG_RE = re.compile(
    r"\b(?:initial|final|always|always_comb|always_latch|always_ff|analog"
    r"|function|task)\b"
)

# No-indent-begin (keywords after which `begin` doesn't add extra indent)
_NO_INDENT_BEGIN_RE = re.compile(
    r"\b(?:always|always_comb|always_ff|always_latch|analog|initial|final"
    r"|if|else|while|for|foreach|repeat|do|forever)\b"
)

# Constraint keyword
_IN_CONSTRAINT_RE = re.compile(r"\b(?:if|else|solve|foreach)\b")

# Named block
_NAMED_BLOCK_RE = re.compile(r"begin\s*:")

# Combined indent-re (for verilog-calc-1 search)
_CALC1_RE = re.compile(
    r"[{}]"
    r"|\b(?:begin|end|case|casex|casez|randcase|endcase"
    r"|class|endclass|clocking|endclocking|config|endconfig"
    r"|covergroup|endgroup|fork|join|join_any|join_none"
    r"|function|endfunction|final|generate|endgenerate"
    r"|initial|interface|endinterface"
    r"|connectmodule|module|macromodule|endconnectmodule|endmodule"
    r"|package|endpackage|primitive|endprimitive"
    r"|program|endprogram|property|endproperty"
    r"|sequence|randsequence|endsequence"
    r"|specify|endspecify|table|endtable"
    r"|task|endtask|virtual"
    r"|always|always_latch|always_ff|always_comb|analog)\b"
    r"|`(?:ifdef|ifndef|if|else|elsif|endif"
    r"|ovm_[a-z_]+_(?:begin|end)"
    r"|uvm_[a-z_]+_(?:begin|end)"
    r"|vmm_[a-z_]+_member_(?:begin|end))\b"
)


class _NestingResult(Exception):
    """Used as throw/catch mechanism from elisp."""
    def __init__(self, kind: str, value: object = None):
        self.kind = kind
        self.value = value


class IndentEngine:
    """Port of the verilog-mode.el indentation engine."""

    def __init__(self, config: "VerilogConfig") -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Raw Python regex search (bypasses Emacs regex translation)
    # ------------------------------------------------------------------

    @staticmethod
    def _re_search_backward(buf: "VerilogBuffer", pattern: str,
                            bound: Optional[int] = None) -> Optional[int]:
        """Search backward for *pattern* (Python regex) before point.

        Moves point to start of match if found. Returns new point or None.
        Does NOT go through ``translate_emacs_regex``.
        """
        limit = bound if bound is not None else buf.point_min()
        limit = max(limit, buf.point_min())
        haystack = buf.buffer_substring(limit, buf.point())
        last = None
        for m in re.finditer(pattern, haystack):
            last = m
        if last is not None:
            buf.goto_char(limit + last.start())
            return buf.point()
        return None

    @staticmethod
    def _re_search_forward(buf: "VerilogBuffer", pattern: str,
                           bound: Optional[int] = None) -> Optional[int]:
        """Search forward for *pattern* (Python regex) from point.

        Moves point to end of match if found. Returns new point or None.
        """
        limit = bound if bound is not None else buf.point_max()
        limit = min(limit, buf.point_max())
        haystack = buf.buffer_substring(buf.point(), limit)
        m = re.search(pattern, haystack)
        if m:
            buf.goto_char(buf.point() + m.end())
            return buf.point()
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_indent(self, buf: "VerilogBuffer") -> tuple[str, int]:
        """Return (indent_type, indent_level) for the line at buf.point.

        Port of ``verilog-calculate-indent``.
        """
        with buf.save_excursion():
            return self._calculate_indent(buf)

    def do_indent(self, buf: "VerilogBuffer") -> None:
        """Indent the current line to the calculated level.

        Port of ``verilog-do-indent``.
        """
        buf.beginning_of_line()
        bol = buf.point()

        # Check if the entire line is inside a block comment — if so, skip
        regions = buf.scan_regions()
        from .token import Tokenizer  # noqa: F811
        from ..scanner import Scanner
        scanner = Scanner()
        if scanner.is_in_comment(bol, regions):
            # Don't re-indent lines inside block comments
            return

        # Skip whitespace to first non-blank
        while buf.point() < buf.point_max() and buf.char_after() in (" ", "\t"):
            buf.forward_char()

        # Skip blank lines
        if buf.point() >= buf.point_max() or buf.char_after() == '\n':
            return

        indent_type, ind = self.calculate_indent(buf)
        val = self._compute_indent_value(buf, indent_type, ind)
        if val is not None and val >= 0:
            buf.beginning_of_line()
            buf.indent_to(val)

    def indent_buffer(self, buf: "VerilogBuffer") -> None:
        """Indent every line in buf.

        Port of ``verilog-indent-buffer``.
        """
        buf.goto_char(buf.point_min())
        while buf.point() < buf.point_max():
            self.do_indent(buf)
            if buf.forward_line(1) == 0:
                break

    def indent_region(self, buf: "VerilogBuffer", beg: int, end: int) -> None:
        """Indent lines in [beg, end)."""
        buf.goto_char(beg)
        while buf.point() < end and buf.point() < buf.point_max():
            self.do_indent(buf)
            if buf.forward_line(1) == 0:
                break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_line_text(self, buf: "VerilogBuffer") -> str:
        """Get the text of the current line (stripped of leading whitespace)."""
        bol = buf._line_start(buf.point())
        eol = buf._line_end(buf.point())
        return buf.buffer_substring(bol, eol).lstrip()

    def _current_indent_level(self, buf: "VerilogBuffer") -> int:
        """Return indent level of current statement (verilog-current-indent-level)."""
        with buf.save_excursion():
            buf.beginning_of_line()
            # Walk up through parenthesized expressions
            depth = self._paren_depth(buf)
            while depth > 0:
                # Find matching open paren
                self._backward_up_paren(buf)
                buf.beginning_of_line()
                depth = self._paren_depth(buf)
            # Skip whitespace
            while buf.point() < buf.point_max() and buf.char_after() in (" ", "\t"):
                buf.forward_char()
            return buf.current_column()

    def _case_indent_level(self, buf: "VerilogBuffer") -> int:
        """Return indent level for case statements (verilog-case-indent-level)."""
        with buf.save_excursion():
            # Skip whitespace
            while buf.point() < buf.point_max() and buf.char_after() in (" ", "\t"):
                buf.forward_char()

            # Check for named block (begin :)
            bol = buf._line_start(buf.point())
            eol = buf._line_end(buf.point())
            line = buf.buffer_substring(bol, eol).lstrip()
            if _NAMED_BLOCK_RE.match(line):
                return buf.current_column()

            # Check for label (not case keyword)
            if not _EXTENDED_CASE_RE.match(line):
                if re.match(r'^[^:;]+:', line):
                    # Move past the colon
                    colon_pos = line.index(':')
                    col = buf.current_column() + colon_pos + 1
                    # Skip whitespace after colon
                    rest = line[colon_pos + 1:]
                    spaces = len(rest) - len(rest.lstrip())
                    return col + spaces

            return buf.current_column()

    def _paren_depth(self, buf: "VerilogBuffer") -> int:
        """Return parenthesis depth at point (verilog-in-paren-count)."""
        text = buf.buffer_substring(buf.point_min(), buf.point())
        regions = buf.scan_regions()
        from ..scanner import Scanner
        scanner = Scanner()

        depth = 0
        for i, ch in enumerate(text):
            pos = buf.point_min() + i
            if scanner.is_in_comment(pos, regions) or scanner.is_in_string(pos, regions):
                continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        return max(0, depth)

    def _in_paren(self, buf: "VerilogBuffer") -> bool:
        """Return True if point is inside parentheses."""
        return self._paren_depth(buf) > 0

    def _backward_up_paren(self, buf: "VerilogBuffer") -> bool:
        """Move backward up one paren level."""
        regions = buf.scan_regions()
        from ..scanner import Scanner
        scanner = Scanner()

        depth = 1
        while buf.point() > buf.point_min() and depth > 0:
            buf.backward_char()
            pos = buf.point()
            if scanner.is_in_comment(pos, regions) or scanner.is_in_string(pos, regions):
                continue
            ch = buf.char_after()
            if ch == ')':
                depth += 1
            elif ch == '(':
                depth -= 1
        return depth == 0

    def _in_star_comment(self, buf: "VerilogBuffer") -> bool:
        """Return True if point is inside a /* */ comment."""
        regions = buf.scan_regions()
        from ..scanner import Scanner
        scanner = Scanner()
        return scanner.is_in_comment(buf.point(), regions)

    def _in_coverage(self, buf: "VerilogBuffer") -> bool:
        """Check if inside a coverage/constraint block."""
        # Simplified heuristic
        with buf.save_excursion():
            bol = buf._line_start(buf.point())
            # Search backward for constraint keyword
            if self._re_search_backward(buf, r"\bconstraint\b", max(bol - 2000, buf.point_min())) is not None:
                return True
        return False

    def _skip_ws_backward(self, buf: "VerilogBuffer") -> None:
        """Skip backward over whitespace."""
        while buf.point() > buf.point_min() and buf.char_before() in (" ", "\t", "\n", "\r"):
            buf.backward_char()

    def _skip_ws_forward(self, buf: "VerilogBuffer") -> None:
        """Skip forward over whitespace."""
        while buf.point() < buf.point_max() and buf.char_after() in (" ", "\t"):
            buf.forward_char()

    # ------------------------------------------------------------------
    # calculate-indent-directive
    # ------------------------------------------------------------------

    def _calculate_indent_directive(self, buf: "VerilogBuffer") -> int:
        """Return indentation for a preprocessor directive line.

        Port of ``verilog-calculate-indent-directive``.
        """
        base = -1
        ind = 0

        with buf.save_excursion():
            buf.beginning_of_line()
            while base < 0 and self._re_search_backward(buf, r"[ \t]*`[a-zA-Z_]", None) is not None:
                # Check if this directive is at beginning of line
                bol = buf._line_start(buf.point())
                pre = buf.buffer_substring(bol, buf.point())
                if pre.strip() == "":
                    base = buf.current_indentation()

                # Get directive text
                eol = buf._line_end(buf.point())
                line_text = buf.buffer_substring(buf.point(), eol).strip()

                if _DIRECTIVE_END.match(line_text) and base < 0:
                    ind -= self.config.indent_level_directive
                elif _DIRECTIVE_MIDDLE.match(line_text) and base >= 0:
                    ind += self.config.indent_level_directive
                elif _DIRECTIVE_BEGIN.match(line_text):
                    ind += self.config.indent_level_directive

            ind = max(0, ind + base)

        # Adjust for current line being middle/end directive
        with buf.save_excursion():
            buf.beginning_of_line()
            self._skip_ws_forward(buf)
            eol = buf._line_end(buf.point())
            line_text = buf.buffer_substring(buf.point(), eol).strip()
            if _DIRECTIVE_MIDDLE.match(line_text) or _DIRECTIVE_END.match(line_text):
                ind = max(0, ind - self.config.indent_level_directive)

        return ind

    # ------------------------------------------------------------------
    # verilog-calc-1 — find block context
    # ------------------------------------------------------------------

    def _calc_1(self, buf: "VerilogBuffer") -> str:
        """Search backward for enclosing block type.

        Port of ``verilog-calc-1``.
        Returns a type string: 'block', 'case', 'defun', 'cpp', 'statement', 'constraint'.
        """
        tokenizer = Tokenizer(buf, self.config)

        while self._re_search_backward(buf,
            r"[{}]"
            r"|\b(?:begin|end|endcase|case|casex|casez|randcase"
            r"|class|endclass|clocking|endclocking"
            r"|covergroup|endgroup|fork|join|join_any|join_none"
            r"|function|endfunction|generate|endgenerate"
            r"|initial|final|always|always_comb|always_ff|always_latch|analog"
            r"|interface|endinterface"
            r"|connectmodule|module|macromodule|endconnectmodule|endmodule"
            r"|package|endpackage|primitive|endprimitive"
            r"|program|endprogram|property|endproperty"
            r"|sequence|randsequence|endsequence"
            r"|specify|endspecify|table|endtable"
            r"|task|endtask|config|endconfig)\b"
            r"|`(?:ifdef|ifndef|if|else|elsif|endif"
            r"|ovm_[a-z_]+_begin|ovm_[a-z_]+_end"
            r"|uvm_[a-z_]+_begin|uvm_[a-z_]+_end"
            r"|vmm_[a-z_]+_member_begin|vmm_[a-z_]+_member_end)\b",
            None,
        ) is not None:
            # Skip if inside comment or string
            regions = buf.scan_regions()
            from ..scanner import Scanner
            scanner = Scanner()
            if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                continue

            ch = buf.char_after()

            # Opening brace
            if ch == '{':
                # Check if constraint
                if self._at_constraint(buf):
                    if self._in_coverage(buf):
                        buf.beginning_of_line()
                        self._skip_ws_forward(buf)
                        return 'constraint'
                    else:
                        return 'statement'
                continue

            # Closing brace
            if ch == '}':
                # Try to find matching {
                self._skip_matching_brace_backward(buf)
                continue

            # Get the word at point
            p = buf.point()
            ep = p
            while ep < buf.point_max() and (buf.char_after(ep).isalnum() or buf.char_after(ep) in ('_', '`')):
                ep += 1
            word = buf.buffer_substring(p, ep)

            # ifdef/else/elsif — skip
            if word in ('`ifdef', '`ifndef', '`if', '`else', '`elsif', '`endif'):
                continue

            # Begin-block keywords
            if word == 'begin':
                return 'block'

            if word in ('case', 'casex', 'casez', 'randcase'):
                # Check for unique/priority prefix
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    bol = buf._line_start(buf.point())
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(bol, eol).lstrip()
                    if _EXTENDED_CASE_RE.match(line):
                        return 'case'
                return 'case'

            if word == 'fork':
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(buf.point(), eol).lstrip()
                    if _DISABLE_FORK_RE.match(line):
                        continue  # disable fork is a statement, not a block
                return 'block'

            if word == 'clocking':
                if self._in_paren(buf):
                    continue
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(buf.point(), eol).lstrip()
                    if _DEFAULT_CLOCKING_RE.match(line):
                        continue
                return 'block'

            if word in ('class', 'struct'):
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(buf.point(), eol).lstrip()
                    if _DPI_IMPORT_EXPORT_RE.match(line):
                        continue
                    if re.match(r'\bpure\b\s+\bvirtual\b', line):
                        return 'statement'
                    if re.match(r'\btypedef\b\s+(?:\bvirtual\b\s+)?\bclass\b', line):
                        return 'statement'
                return 'block'

            if word in ('function', 'task'):
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(buf.point(), eol).lstrip()
                    if _DPI_IMPORT_EXPORT_RE.match(line):
                        continue
                    if re.match(r'\bpure\b\s+\bvirtual\b', line):
                        return 'statement'
                    if re.match(r'\btypedef\b', line):
                        return 'statement'
                    # Check if it starts a block
                    if _BEG_BLOCK_RE.match(line):
                        return 'block'
                return 'defun'

            if word in ('property', 'sequence'):
                with buf.save_excursion():
                    self._beg_of_statement(buf)
                    eol = buf._line_end(buf.point())
                    line = buf.buffer_substring(buf.point(), eol).lstrip()
                    if _PROPERTY_RE.match(line):
                        continue  # assert property, complete statement
                return 'block'

            if word in ('table', 'specify', 'generate', 'covergroup'):
                return 'block'

            # UVM/OVM/VMM begin macros
            if word.startswith('`') and ('_begin' in word):
                return 'block'

            # End-block keywords — leap to matching begin
            if _END_BLOCK_RE.match(word):
                self._leap_to_head(buf)
                # Check if in case region
                if self._in_case_region(buf):
                    self._leap_to_case_head(buf)
                    return 'case'
                continue

            # Defun-level keywords
            if _DEFUN_LEVEL_RE.match(word):
                if _DEFUN_LEVEL_GENERATE_ONLY_RE.match(word):
                    if self._in_generate_region(buf):
                        continue  # always in generate, keep looking
                    return 'defun'
                return 'defun'

            # CPP-level keywords (endmodule etc.)
            if _CPP_LEVEL_RE.match(word):
                return 'cpp'

            if buf.point() <= buf.point_min():
                return 'cpp'

        return 'cpp'

    # ------------------------------------------------------------------
    # Block matching helpers
    # ------------------------------------------------------------------

    def _at_constraint(self, buf: "VerilogBuffer") -> bool:
        """Check if { at point is a constraint block."""
        if buf.char_after() != '{':
            return False
        with buf.save_excursion():
            buf.backward_char()
            tokenizer = Tokenizer(buf, self.config)
            tokenizer._skip_ws_and_comments_backward()
            if buf.char_before() == ')':
                return True
            # Look for constraint keyword on this line
            bol = buf._line_start(buf.point())
            line = buf.buffer_substring(bol, buf.point())
            return bool(re.search(r'\bconstraint\b', line))

    def _skip_matching_brace_backward(self, buf: "VerilogBuffer") -> None:
        """Find matching { for } at point."""
        depth = 1
        while buf.point() > buf.point_min() and depth > 0:
            buf.backward_char()
            regions = buf.scan_regions()
            from ..scanner import Scanner
            scanner = Scanner()
            if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                continue
            ch = buf.char_after()
            if ch == '}':
                depth += 1
            elif ch == '{':
                depth -= 1

    def _beg_of_statement(self, buf: "VerilogBuffer") -> None:
        """Move point to beginning of current statement."""
        buf.beginning_of_line()
        while buf.point() < buf.point_max() and buf.char_after() in (" ", "\t"):
            buf.forward_char()

    def _leap_to_head(self, buf: "VerilogBuffer") -> None:
        """Jump from end-block keyword to matching begin-block.

        Port of ``verilog-leap-to-head``.
        """
        p = buf.point()
        ep = p
        while ep < buf.point_max() and (buf.char_after(ep).isalnum() or buf.char_after(ep) == '_'):
            ep += 1
        word = buf.buffer_substring(p, ep)

        reg = None
        nesting = True
        nest = 1

        if word == 'end':
            reg = (r'\b(begin)\b', r'\b(end)\b|\b(endcase)\b|\b(join(?:_any|_none)?)\b')
        elif word == 'endcase':
            self._leap_to_case_head(buf)
            return
        elif word == 'endfunction':
            reg = (r'\b(function)\b', r'\b(endfunction)\b')
            nesting = False
        elif word == 'endtask':
            reg = (r'\b(task)\b', r'\b(endtask)\b')
            nesting = False
        elif word == 'endspecify':
            reg = (r'\b(specify)\b', r'\b(endspecify)\b')
        elif word == 'endtable':
            reg = (r'\b(table)\b', r'\b(endtable)\b')
        elif word == 'endgenerate':
            reg = (r'\b(generate)\b', r'\b(endgenerate)\b')
        elif word in ('join', 'join_any', 'join_none'):
            reg = (r'\b(fork)\b', r'\b(join(?:_any|_none)?)\b')
        elif word == 'endclass':
            reg = (r'\b(class)\b', r'\b(endclass)\b')
        elif word == 'endgroup':
            reg = (r'\b(covergroup)\b', r'\b(endgroup)\b')
        elif word == 'endproperty':
            reg = (r'\b(property)\b', r'\b(endproperty)\b')
        elif word == 'endinterface':
            reg = (r'\b(interface)\b', r'\b(endinterface)\b')
        elif word == 'endsequence':
            reg = (r'\b((?:rand)?sequence)\b', r'\b(endsequence)\b')
        elif word == 'endclocking':
            reg = (r'\b(clocking)\b', r'\b(endclocking)\b')
        elif word == 'endpackage':
            reg = (r'\b(package)\b', None)
            nesting = False
        elif word == 'endprogram':
            reg = (r'\b(program)\b', None)
            nesting = False
        elif word == '`endif':
            reg = (r'`(ifn?def)\b', r'`(endif)\b')
        elif word == '`else':
            reg = (r'`(ifn?def|elsif)\b', r'`(else)\b')
        elif word.startswith('`') and '_end' in word:
            # OVM/UVM/VMM end macros
            prefix = word.split('_end')[0].lstrip('`')
            reg = (r'`(' + re.escape(prefix) + r'[a-z_]*_begin)\b',
                   r'`(' + re.escape(prefix) + r'[a-z_]*_end)\b')
        else:
            return

        if reg is None:
            return

        beg_re, end_re = reg

        if nesting:
            combined = beg_re
            if end_re:
                combined = beg_re + '|' + end_re
            while self._re_search_backward(buf, combined, None) is not None:
                regions = buf.scan_regions()
                from ..scanner import Scanner
                scanner = Scanner()
                if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                    continue

                line_text = self._word_at_point(buf)

                # Check if it's a begin pattern
                if re.match(beg_re, line_text):
                    # Special: if fork, check for disable fork
                    if 'fork' in line_text:
                        with buf.save_excursion():
                            self._beg_of_statement(buf)
                            eol = buf._line_end(buf.point())
                            stmt = buf.buffer_substring(buf.point(), eol).lstrip()
                            if _DISABLE_FORK_RE.match(stmt):
                                continue
                    nest -= 1
                    if nest == 0:
                        return
                elif end_re and re.match(end_re, line_text):
                    nest += 1
        else:
            # No nesting: just find the first matching begin
            while self._re_search_backward(buf, beg_re, None) is not None:
                regions = buf.scan_regions()
                from ..scanner import Scanner
                scanner = Scanner()
                if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                    continue
                self._beg_of_statement(buf)
                return

    def _word_at_point(self, buf: "VerilogBuffer") -> str:
        """Get the word/directive at the current point."""
        p = buf.point()
        ep = p
        # Include backtick for directives
        if ep < buf.point_max() and buf.char_after(ep) == '`':
            ep += 1
        while ep < buf.point_max() and (buf.char_after(ep).isalnum() or buf.char_after(ep) == '_'):
            ep += 1
        return buf.buffer_substring(p, ep)

    def _leap_to_case_head(self, buf: "VerilogBuffer") -> None:
        """Jump from endcase to matching case."""
        nest = 1
        case_re = r'\b(randcase|(?:unique0?\s+|priority\s+)?case[xz]?)\b|\b(endcase)\b'
        while nest > 0 and self._re_search_backward(buf, case_re, None) is not None:
            regions = buf.scan_regions()
            from ..scanner import Scanner
            scanner = Scanner()
            if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                continue
            word = self._word_at_point(buf)
            if word == 'endcase':
                nest += 1
            elif re.match(r'(?:unique0?\s+|priority\s+)?case[xz]?\b|randcase\b', word):
                nest -= 1

    def _in_case_region(self, buf: "VerilogBuffer") -> bool:
        """Check if point is inside a case statement body."""
        with buf.save_excursion():
            p = buf.point()
            # Search backward for case or endcase
            while self._re_search_backward(buf, r'\b(?:case[xz]?|randcase|endcase)\b', None) is not None:
                regions = buf.scan_regions()
                from ..scanner import Scanner
                scanner = Scanner()
                if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                    continue
                word = self._word_at_point(buf)
                if word == 'endcase':
                    return False
                if re.match(r'case[xz]?|randcase', word):
                    return True
        return False

    def _in_generate_region(self, buf: "VerilogBuffer") -> bool:
        """Check if point is inside a generate block."""
        with buf.save_excursion():
            nest = 0
            while self._re_search_backward(buf, r'\b(?:generate|endgenerate)\b', None) is not None:
                regions = buf.scan_regions()
                from ..scanner import Scanner
                scanner = Scanner()
                if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                    continue
                word = self._word_at_point(buf)
                if word == 'endgenerate':
                    nest += 1
                elif word == 'generate':
                    if nest > 0:
                        nest -= 1
                    else:
                        return True
        return False

    # ------------------------------------------------------------------
    # continued-line-1
    # ------------------------------------------------------------------

    def _continued_line_1(self, buf: "VerilogBuffer", lim: Optional[int] = None) -> bool:
        """Return True if this is a continued line.

        Port of ``verilog-continued-line-1``.
        """
        tokenizer = Tokenizer(buf, self.config)
        moved = buf.forward_line(-1)
        if moved == 0:
            return False
        buf.end_of_line()
        # Skip directives & whitespace backward to lim
        tokenizer._skip_ws_and_comments_backward()
        if buf.point() <= buf.point_min():
            return False
        return tokenizer.backward_token()

    # ------------------------------------------------------------------
    # The main calculate-indent state machine
    # ------------------------------------------------------------------

    def _calculate_indent(self, buf: "VerilogBuffer") -> tuple[str, int]:
        """Core of calculate-indent. Called within save_excursion."""
        starting_position = buf.point()

        # Check if current line starts with 'begin'
        line_text = self._get_line_text(buf)
        begins_with_begin = line_text.startswith('begin') and (
            len(line_text) == 5 or not line_text[5].isalnum()
        )

        # Find backward limit (nearest begin or module)
        with buf.save_excursion():
            lim = None
            if self._re_search_backward(buf, r'\b(?:begin|(?:connect)?module)\b', None) is not None:
                lim = buf.point()

        par = 0

        # ---- Check if in comment ----
        if self._in_star_comment(buf):
            return self._indent_comment(buf)

        # ---- Check if directive ----
        bol = buf._line_start(buf.point())
        eol = buf._line_end(buf.point())
        full_line = buf.buffer_substring(bol, eol)
        stripped = full_line.lstrip()
        if stripped.startswith('`') and not stripped.startswith('`ovm_') and not stripped.startswith('`uvm_') and not stripped.startswith('`vmm_'):
            return ('directive', self._calculate_indent_directive(buf))

        # ---- Check parentheses ----
        if self._in_paren(buf) and not self._in_coverage(buf):
            par = 1
            return self._handle_paren_indent(buf, par)

        # ---- Continued line scan ----
        while True:
            if buf.point() <= buf.point_min():
                return ('cpp', 0)

            if self._continued_line_1(buf, lim):
                sp = buf.point()
                # Get text at continuation point
                cont_line = self._get_line_text(buf)

                # Check if line looks complete
                if not _COMPLETE_RE.match(cont_line):
                    if self._continued_line_1(buf, lim):
                        buf.goto_char(sp)
                        return ('cexp', self._current_indent_level(buf))
                    buf.goto_char(sp)

                # Check constraint context
                if self._in_coverage(buf) and _IN_CONSTRAINT_RE.match(cont_line):
                    buf.beginning_of_line()
                    self._skip_ws_forward(buf)
                    return ('constraint', buf.current_column())

                # Check begin with no-indent-begin
                if begins_with_begin and _NO_INDENT_BEGIN_RE.match(cont_line):
                    buf.beginning_of_line()
                    self._skip_ws_forward(buf)
                    return ('statement', buf.current_column())

                return ('cexp', self._current_indent_level(buf))
            else:
                # Not a continued line
                buf.goto_char(starting_position)

            # ---- Check for else ----
            line_text = self._get_line_text(buf)
            if line_text.startswith('else') and (len(line_text) == 4 or not line_text[4].isalnum()):
                return self._handle_else(buf)

            # ---- Search for enclosing block via calc-1 ----
            indent_type = self._calc_1(buf)

            if par > 0:
                return self._handle_paren_indent(buf, par)

            if indent_type == 'case':
                return ('case', self._case_indent_level(buf))
            elif indent_type == 'statement':
                return ('statement', buf.current_column())
            elif indent_type == 'defun':
                return ('defun', 0)
            elif indent_type == 'constraint':
                return ('block', buf.current_column())
            elif indent_type == 'nested-struct':
                return ('block', buf.current_column())
            else:
                return (indent_type, self._current_indent_level(buf))

    def _handle_else(self, buf: "VerilogBuffer") -> tuple[str, int]:
        """Handle indentation when current line starts with else."""
        elsec = 1
        while self._re_search_backward(buf,
            r'\b(?:(else)|(if)|(assert)|(end)|(endcase)|(endfunction)'
            r'|(endtask)|(endspecify)|(endtable)|(endgenerate)'
            r'|(join(?:_any|_none)?)|(endclass)|(endgroup))\b',
            None,
        ) is not None:
            regions = buf.scan_regions()
            from ..scanner import Scanner
            scanner = Scanner()
            if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                continue

            word = self._word_at_point(buf)

            if word == 'else':
                elsec += 1
            elif word == 'if':
                elsec -= 1
                if elsec == 0:
                    buf.beginning_of_line()
                    self._skip_ws_forward(buf)
                    return ('statement', buf.current_column())
            elif word == 'assert':
                elsec -= 1
                self._beg_of_statement(buf)
                return ('statement', buf.current_column())
            else:
                # End-block keyword — leap to matching head
                self._leap_to_head(buf)

        return ('cpp', 0)

    def _handle_paren_indent(self, buf: "VerilogBuffer", par: int) -> tuple[str, int]:
        """Handle indentation inside parentheses (cparenexp)."""
        return ('cparenexp', par)

    def _indent_comment(self, buf: "VerilogBuffer") -> tuple[str, int]:
        """Calculate indent for a line inside a block comment."""
        with buf.save_excursion():
            # Find the /* that starts this comment
            text = buf.buffer_substring(buf.point_min(), buf.point())
            idx = text.rfind('/*')
            if idx >= 0:
                return ('comment', idx + 1)
        return ('comment', 0)

    # ------------------------------------------------------------------
    # compute_indent_value — port of verilog-do-indent logic
    # ------------------------------------------------------------------

    def _compute_indent_value(self, buf: "VerilogBuffer", indent_type: str, ind: int) -> Optional[int]:
        """Compute the final indent column based on type and reference level.

        Port of the core logic in ``verilog-do-indent``.
        """
        cfg = self.config

        # Get current line text
        bol = buf._line_start(buf.point())
        eol = buf._line_end(buf.point())
        line = buf.buffer_substring(bol, eol).lstrip()

        # Continued expression
        if indent_type == 'cexp':
            return ind + cfg.cexp_indent

        # Parenthetical expression
        if indent_type == 'cparenexp':
            return self._compute_cparenexp_indent(buf, ind)

        # End-block keywords
        if _END_BLOCK_RE.match(line) or line.startswith('}'):
            if indent_type == 'statement':
                return max(0, ind - cfg.indent_level)
            return ind

        # Case
        if indent_type == 'case':
            if line.startswith('endcase'):
                return ind
            return ind + cfg.case_indent

        # Defun
        if indent_type == 'defun':
            if _ZERO_INDENT_RE.match(line):
                return 0
            return cfg.indent_level_module

        # Block
        if indent_type == 'block':
            return ind + cfg.indent_level

        # Statement
        if indent_type == 'statement':
            return ind

        # Directive
        if indent_type == 'directive':
            return ind  # already computed

        # Comment
        if indent_type == 'comment':
            return ind

        # cpp (top level)
        if indent_type == 'cpp':
            return 0

        # behavioral
        if indent_type == 'behavioral':
            return cfg.indent_level_behavioral + cfg.indent_level_module

        # tf (task/function)
        if indent_type == 'tf':
            return cfg.indent_level

        # declaration
        if indent_type == 'declaration':
            return cfg.indent_level_declaration

        # constraint
        if indent_type == 'constraint':
            return ind + cfg.indent_level

        # unknown
        return ind + cfg.indent_level

    def _compute_cparenexp_indent(self, buf: "VerilogBuffer", par: int) -> int:
        """Compute indent for lines inside parentheses.

        Port of ``verilog-cparenexp-indent-level``.
        """
        cfg = self.config
        with buf.save_excursion():
            # Find the opening paren
            if self._backward_up_paren(buf):
                # We're now at the opening paren
                open_paren_col = buf.current_column()
                buf.forward_char()  # move past (
                # Skip horizontal whitespace on same line (not newlines)
                eol = buf._line_end(buf.point())
                while buf.point() < eol and buf.char_after() in (" ", "\t"):
                    buf.forward_char()
                if buf.point() < eol and buf.char_after() not in ('\n', '\r', ''):
                    # There's content after the paren on the same line
                    return buf.current_column()
                else:
                    # Nothing after paren — Emacs returns column right after (
                    return open_paren_col + 1
        return par + cfg.indent_level
