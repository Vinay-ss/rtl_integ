"""Tokenizer for backward/forward scanning of Verilog buffers.

Ported from ``verilog-backward-token`` and ``verilog-forward-token``
in ``verilog-mode.el`` (lines ~6537-6656).
"""

from __future__ import annotations

import re
from enum import Enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


class TokenKind(str, Enum):
    KEYWORD = "keyword"
    IDENTIFIER = "ident"
    NUMBER = "number"
    STRING = "string"
    OPERATOR = "op"
    DIRECTIVE = "directive"
    COMMENT = "comment"
    EOF = "eof"


@dataclass
class Token:
    kind: TokenKind
    text: str
    pos: int  # start position in buffer


# ---------------------------------------------------------------------------
# Regex patterns ported from verilog-mode.el
# ---------------------------------------------------------------------------

# Keywords that start behavioural blocks
_BEHAVIORAL_BLOCK_BEG = re.compile(
    r"\b(?:initial|final|always|always_comb|always_latch|always_ff|analog"
    r"|function|task)\b"
)

# Keywords that open indent blocks (verilog-indent-re)
_INDENT_RE = re.compile(
    r"\b(?:"
    r"always|always_latch|always_ff|always_comb|analog"
    r"|begin|end"
    r"|case|casex|casez|randcase|endcase"
    r"|class|endclass"
    r"|clocking|endclocking"
    r"|config|endconfig"
    r"|covergroup|endgroup"
    r"|fork|join|join_any|join_none"
    r"|function|endfunction"
    r"|final"
    r"|generate|endgenerate"
    r"|initial"
    r"|interface|endinterface"
    r"|connectmodule|module|macromodule|endconnectmodule|endmodule"
    r"|package|endpackage"
    r"|primitive|endprimitive"
    r"|program|endprogram"
    r"|property|endproperty"
    r"|sequence|randsequence|endsequence"
    r"|specify|endspecify"
    r"|table|endtable"
    r"|task|endtask"
    r"|virtual"
    r")\b"
    r"|`(?:case|default|define|undef|if|ifdef|ifndef|else|elsif|endif"
    r"|while|endwhile|for|endfor|format|include|let"
    r"|protect|endprotect|switch|endswitch|timescale|time_scale"
    r"|ovm_component_utils_begin|ovm_component_param_utils_begin"
    r"|ovm_field_utils_begin|ovm_object_utils_begin"
    r"|ovm_object_param_utils_begin|ovm_sequence_utils_begin"
    r"|ovm_sequencer_utils_begin"
    r"|ovm_component_utils_end|ovm_field_utils_end"
    r"|ovm_object_utils_end|ovm_sequence_utils_end"
    r"|ovm_sequencer_utils_end"
    r"|uvm_component_utils_begin|uvm_component_param_utils_begin"
    r"|uvm_field_utils_begin|uvm_object_utils_begin"
    r"|uvm_object_param_utils_begin|uvm_sequence_utils_begin"
    r"|uvm_sequencer_utils_begin"
    r"|uvm_component_utils_end|uvm_field_utils_end"
    r"|uvm_object_utils_end|uvm_sequence_utils_end"
    r"|uvm_sequencer_utils_end"
    r"|vmm_data_member_begin|vmm_env_member_begin"
    r"|vmm_scenario_member_begin|vmm_subenv_member_begin"
    r"|vmm_xactor_member_begin"
    r"|vmm_data_member_end|vmm_env_member_end"
    r"|vmm_scenario_member_end|vmm_subenv_member_end"
    r"|vmm_xactor_member_end"
    r")\b"
)

# Nameable-item keywords (label-able: begin, fork, etc.)
_NAMEABLE_ITEM_RE = re.compile(
    r"\b(?:begin|fork|join|join_any|join_none|end|endcase|endconfig"
    r"|endclass|endclocking|endfunction|endgenerate|endgroup"
    r"|endinterface|endmodule|endpackage|endprimitive|endprogram"
    r"|endproperty|endsequence|endspecify|endtable|endtask"
    r"|function|generate|interface|module|macromodule|connectmodule"
    r"|package|primitive|program|property|sequence|specify|table|task)\b"
)

# Statement-completing keywords after closing paren
_PAREN_COMPLETE_KW = re.compile(
    r"\b(?:always(?:_latch|_ff|_comb)?|case[xz]?"
    r"|for(?:each|ever)?|i(?:f|nitial)|repeat|while)\b"
)

# UVM/OVM/VMM macro patterns
_UVM_STATEMENT_RE = re.compile(r"`uvm_(?:info|warning|error|fatal)\b")
_UVM_BEGIN_RE = re.compile(r"`uvm_[a-z_]+_begin\b")
_UVM_END_RE = re.compile(r"`uvm_[a-z_]+_end\b")
_OVM_STATEMENT_RE = re.compile(r"`ovm_(?:info|warning|error|fatal)\b")
_OVM_BEGIN_RE = re.compile(r"`ovm_[a-z_]+_begin\b")
_OVM_END_RE = re.compile(r"`ovm_[a-z_]+_end\b")
_VMM_STATEMENT_RE = re.compile(r"`vmm_(?:data|env|scenario|subenv|xactor)_member_(?:scalar|string|enum|vmm_data|channel|xactor|user_defined)\b")
_VMM_BEGIN_RE = re.compile(r"`vmm_[a-z_]+_member_begin\b")
_VMM_END_RE = re.compile(r"`vmm_[a-z_]+_end\b")


class Tokenizer:
    """Backward/forward token scanner for Verilog buffers.

    Ports the logic from ``verilog-backward-token`` and related
    functions in verilog-mode.el.
    """

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self.buf = buf
        self.config = config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skip_ws_and_comments_backward(self) -> None:
        """Skip backward over whitespace and comments (verilog-backward-syntactic-ws)."""
        buf = self.buf
        changed = True
        while changed:
            changed = False
            # Skip whitespace
            while buf.point() > buf.point_min():
                ch = buf.char_before()
                if ch in (" ", "\t", "\n", "\r"):
                    buf.backward_char()
                    changed = True
                else:
                    break
            # Skip line comments backward
            if buf.point() > buf.point_min():
                # Check if we're at end of a line comment
                # Walk back to see if there's a // on this line
                p = buf.point()
                bol = buf._line_start(p)
                line = buf.buffer_substring(bol, p)
                # Find // not inside string
                in_str = False
                comment_pos = None
                for i, c in enumerate(line):
                    if c == '"' and (i == 0 or line[i - 1] != '\\'):
                        in_str = not in_str
                    elif c == '/' and i + 1 < len(line) and line[i + 1] == '/' and not in_str:
                        comment_pos = i
                        break
                if comment_pos is not None and p > bol + comment_pos:
                    buf.goto_char(bol + comment_pos)
                    changed = True
                    continue

            # Skip block comments backward
            if buf.point() >= buf.point_min() + 2:
                if buf.char_before() == '/' and buf.char_before(buf.point() - 1) == '*':
                    buf.backward_char(2)  # skip */
                    # Find matching /*
                    depth = 1
                    while buf.point() > buf.point_min() and depth > 0:
                        if buf.char_after() == '/' and buf.char_after(buf.point() + 1) == '*':
                            depth -= 1
                            if depth == 0:
                                break
                        buf.backward_char()
                    changed = True

    def _skip_ws_and_comments_forward(self) -> None:
        """Skip forward over whitespace and comments."""
        buf = self.buf
        changed = True
        while changed:
            changed = False
            while buf.point() < buf.point_max():
                ch = buf.char_after()
                if ch in (" ", "\t", "\n", "\r"):
                    buf.forward_char()
                    changed = True
                else:
                    break
            if buf.point() + 1 < buf.point_max():
                c0 = buf.char_after()
                c1 = buf.char_after(buf.point() + 1)
                if c0 == '/' and c1 == '/':
                    buf.end_of_line()
                    if buf.point() < buf.point_max():
                        buf.forward_char()  # skip newline
                    changed = True
                elif c0 == '/' and c1 == '*':
                    buf.forward_char(2)
                    while buf.point() + 1 < buf.point_max():
                        if buf.char_after() == '*' and buf.char_after(buf.point() + 1) == '/':
                            buf.forward_char(2)
                            break
                        buf.forward_char()
                    changed = True

    def _at_constraint_p(self) -> bool:
        """Check if point is at a constraint block opening ``{``.

        Port of verilog-at-constraint-p.
        """
        buf = self.buf
        if buf.char_after() != '{':
            return False
        with buf.save_excursion():
            buf.backward_char()
            self._skip_ws_and_comments_backward()
            # Look back for constraint keyword or ) before {
            if buf.char_before() == ')':
                return True
            # Check for constraint keyword
            p = buf.point()
            bol = buf._line_start(p)
            line = buf.buffer_substring(bol, p)
            if re.search(r'\bconstraint\b', line):
                return True
        return False

    def _at_close_constraint_p(self) -> bool:
        """Check if point is at a closing ``}`` of a constraint block.

        Port of verilog-at-close-constraint-p.
        """
        buf = self.buf
        if buf.char_after() != '}':
            ch = buf.char_before()
            if ch != '}':
                return False
        # Simple heuristic: check if matching { is a constraint
        with buf.save_excursion():
            p = buf.point()
            if buf.char_after() == '}':
                pass
            else:
                buf.backward_char()
            # Find matching {
            depth = 1
            while buf.point() > buf.point_min() and depth > 0:
                buf.backward_char()
                ch = buf.char_after()
                if ch == '}':
                    depth += 1
                elif ch == '{':
                    depth -= 1
            if depth == 0:
                return self._at_constraint_p()
        return False

    def _backward_up_list(self) -> bool:
        """Move backward up one level of parentheses. Return True if successful."""
        buf = self.buf
        depth = 1
        while buf.point() > buf.point_min() and depth > 0:
            buf.backward_char()
            ch = buf.char_after()
            # Skip comments and strings
            regions = buf.scan_regions()
            from ..scanner import Scanner
            scanner = Scanner()
            if scanner.is_in_comment(buf.point(), regions) or scanner.is_in_string(buf.point(), regions):
                continue
            if ch == ')':
                depth += 1
            elif ch == '(':
                depth -= 1
        return depth == 0

    # ------------------------------------------------------------------
    # Main token scanning
    # ------------------------------------------------------------------

    def backward_token(self) -> bool:
        """Step backward one token, returning True if this is a continued line.

        Port of ``verilog-backward-token`` in verilog-mode.el.
        Moves ``buf.point`` to the start of the previous token.
        Returns True if the line is continued (not yet complete),
        False if the statement is complete at this point.
        """
        buf = self.buf
        self._skip_ws_and_comments_backward()

        if buf.point() <= buf.point_min():
            return False

        pc = buf.char_before()

        # Semicolon ends a statement
        if pc == ';':
            return False

        # Closing brace: check constraint
        if pc == '}':
            buf.backward_char()
            return not self._at_close_constraint_p()

        # Opening brace: check constraint
        if pc == '{':
            buf.backward_char()
            return not self._at_constraint_p()

        # String
        if pc == '"':
            buf.backward_char()
            # Skip backward over string
            if buf.point() > buf.point_min():
                buf.backward_char()
                while buf.point() > buf.point_min():
                    ch = buf.char_after()
                    if ch == '"':
                        # Check not escaped
                        if buf.point() == buf.point_min() or buf.char_before() != '\\':
                            break
                    buf.backward_char()
            return False

        # Closing bracket
        if pc == ']':
            buf.backward_char()
            # Skip backward to matching [
            depth = 1
            while buf.point() > buf.point_min() and depth > 0:
                buf.backward_char()
                ch = buf.char_after()
                if ch == ']':
                    depth += 1
                elif ch == '[':
                    depth -= 1
            return True

        # Closing paren — could be case(foo), always @(bar), etc.
        if pc == ')':
            buf.backward_char()
            # Find matching (
            self._backward_up_list()
            self._skip_ws_and_comments_backward()
            back = buf.point()

            # Move back one word
            while buf.point() > buf.point_min() and (buf.char_before().isalnum() or buf.char_before() in ('_', '`')):
                buf.backward_char()

            word_start = buf.point()
            word = buf.buffer_substring(word_start, back)

            # Check if keyword completing statement
            if _PAREN_COMPLETE_KW.match(word):
                # case/casex/casez with [^:] check
                if re.match(r'\brandcase\b', word) or re.match(r'\bcase[xz]?\b', word):
                    return True
                return False

            # UVM/OVM/VMM checks
            if _UVM_STATEMENT_RE.match(word):
                return False
            if _UVM_BEGIN_RE.match(word):
                return True
            if _UVM_END_RE.match(word):
                return True
            if _OVM_STATEMENT_RE.match(word):
                return False
            if _OVM_BEGIN_RE.match(word):
                return True
            if _OVM_END_RE.match(word):
                return True
            if _VMM_STATEMENT_RE.match(word):
                return False
            if _VMM_BEGIN_RE.match(word):
                return True
            if _VMM_END_RE.match(word):
                return False
            # Backtick macro
            if word.startswith('`'):
                return False

            # Default: go back to saved position and check @ or #
            buf.goto_char(back)
            if buf.char_before() == '@':
                buf.backward_char()
                # Check if after always/initial/while
                with buf.save_excursion():
                    ret = self.backward_token()
                    p = buf.point()
                    end_p = buf._line_end(p)
                    text = buf.buffer_substring(p, end_p)
                    if re.match(r'\b(?:always(?:_latch|_ff|_comb)?|initial|while)\b', text):
                        return False
                return True
            elif buf.char_before() == '#':
                buf.backward_char()
                return True
            else:
                return True

        # Default: move back one word
        while buf.point() > buf.point_min() and (buf.char_before().isalnum() or buf.char_before() == '_'):
            buf.backward_char()
        # Also skip preceding _, @, .
        while buf.point() > buf.point_min() and buf.char_before() in ('_', '@', '.'):
            while buf.point() > buf.point_min() and (buf.char_before().isalnum() or buf.char_before() == '_'):
                buf.backward_char()
            if buf.point() > buf.point_min() and buf.char_before() in ('.', '@', '_'):
                buf.backward_char()
                continue
            break

        word_end = buf._line_end(buf.point())
        # Get the word at point
        p = buf.point()
        wp = p
        while wp < word_end and (buf.char_after(wp) if wp < buf.point_max() else '') in (' ', '\t'):
            wp += 1
        word_start = p
        we = word_start
        while we < buf.point_max() and (buf.char_after(we).isalnum() or buf.char_after(we) == '_'):
            we += 1
        word = buf.buffer_substring(word_start, we)

        if word == 'else':
            return True

        if _BEHAVIORAL_BLOCK_BEG.match(word):
            return True

        if _INDENT_RE.match(word) or (word.startswith('`') and _INDENT_RE.match(word)):
            return False

        # Check for colon (labels)
        self._skip_ws_and_comments_backward()
        if buf.point() > buf.point_min() and buf.char_before() == ':':
            buf.backward_char()
            self._skip_ws_and_comments_backward()
            # Skip back the label identifier
            label_end = buf.point()
            while buf.point() > buf.point_min() and (buf.char_before().isalnum() or buf.char_before() == '_'):
                buf.backward_char()
            label = buf.buffer_substring(buf.point(), label_end)
            if _NAMEABLE_ITEM_RE.match(label):
                return False
            return True

        if buf.point() > buf.point_min() and buf.char_before() == '#':
            buf.backward_char()
            return True

        if buf.point() > buf.point_min() and buf.char_before() == '`':
            buf.backward_char()
            return True

        return True

    def forward_token(self) -> Token:
        """Move buf.point past the current token and return it.

        Port of ``verilog-forward-token``.
        """
        buf = self.buf
        self._skip_ws_and_comments_forward()

        if buf.point() >= buf.point_max():
            return Token(TokenKind.EOF, "", buf.point())

        start = buf.point()
        ch = buf.char_after()

        # String
        if ch == '"':
            buf.forward_char()
            while buf.point() < buf.point_max():
                c = buf.char_after()
                if c == '\\':
                    buf.forward_char(2)
                    continue
                if c == '"':
                    buf.forward_char()
                    break
                buf.forward_char()
            return Token(TokenKind.STRING, buf.buffer_substring(start, buf.point()), start)

        # Number
        if ch.isdigit() or (ch == '\'' and buf.point() + 1 < buf.point_max()):
            while buf.point() < buf.point_max() and (buf.char_after().isalnum() or buf.char_after() in ("'", '_', '?', 'x', 'X', 'z', 'Z')):
                buf.forward_char()
            return Token(TokenKind.NUMBER, buf.buffer_substring(start, buf.point()), start)

        # Directive
        if ch == '`':
            buf.forward_char()
            while buf.point() < buf.point_max() and (buf.char_after().isalnum() or buf.char_after() == '_'):
                buf.forward_char()
            return Token(TokenKind.DIRECTIVE, buf.buffer_substring(start, buf.point()), start)

        # Identifier or keyword
        if ch.isalpha() or ch == '_':
            while buf.point() < buf.point_max() and (buf.char_after().isalnum() or buf.char_after() in ('_', '$')):
                buf.forward_char()
            text = buf.buffer_substring(start, buf.point())
            kind = TokenKind.KEYWORD if _INDENT_RE.match(text) or _BEHAVIORAL_BLOCK_BEG.match(text) else TokenKind.IDENTIFIER
            return Token(kind, text, start)

        # Operator / punctuation
        buf.forward_char()
        # Handle two-char operators
        if buf.point() < buf.point_max():
            two = ch + buf.char_after()
            if two in ("<=", ">=", "==", "!=", "&&", "||", "<<", ">>", "**", "->", "<-", "+=", "-=", "::", "=>"):
                buf.forward_char()
                return Token(TokenKind.OPERATOR, two, start)
        return Token(TokenKind.OPERATOR, ch, start)
