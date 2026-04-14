"""AlwaysParser — scan ``always``/``initial`` blocks for signal usage.

Ported from ``verilog-read-always-signals-recurse`` (lines 9989–10148)
and ``verilog-read-always-signals`` (lines 10150–10160) of
``verilog-mode.el``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from ..signal import Signal

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig

# Verilog keywords that should not be treated as signal names
_KEYWORDS = {
    "always", "always_comb", "always_ff", "always_latch",
    "and", "assign", "automatic", "begin", "buf", "bufif0", "bufif1",
    "case", "casex", "casez", "cmos", "deassign", "default", "defparam",
    "disable", "edge", "else", "end", "endcase", "endfunction",
    "endgenerate", "endmodule", "endprimitive", "endspecify", "endtable",
    "endtask", "event", "for", "force", "forever", "fork", "function",
    "generate", "genvar", "highz0", "highz1", "if", "ifnone", "initial",
    "inout", "input", "integer", "join", "large", "localparam", "macromodule",
    "medium", "module", "nand", "negedge", "nmos", "nor", "not", "notif0",
    "notif1", "or", "output", "parameter", "pmos", "posedge", "primitive",
    "pull0", "pull1", "pulldown", "pullup", "rcmos", "real", "realtime",
    "reg", "release", "repeat", "rnmos", "rpmos", "rtran", "rtranif0",
    "rtranif1", "scalared", "signed", "small", "specify", "specparam",
    "strength", "strong0", "strong1", "supply0", "supply1", "table",
    "task", "time", "tran", "tranif0", "tranif1", "tri", "tri0", "tri1",
    "triand", "trior", "trireg", "unsigned", "vectored", "wait", "wand",
    "weak0", "weak1", "while", "wire", "wor", "xnor", "xor",
    # SystemVerilog extras
    "bit", "byte", "chandle", "class", "clocking", "constraint", "const",
    "cover", "do", "endclass", "endclocking", "endinterface", "endpackage",
    "endprogram", "endproperty", "endsequence", "enum", "export", "extends",
    "extern", "final", "foreach", "import", "inside", "int", "interface",
    "local", "logic", "longint", "new", "null", "package", "packed",
    "priority", "program", "property", "protected", "pure", "rand",
    "randc", "randcase", "ref", "return", "sequence", "shortint",
    "shortreal", "static", "string", "struct", "super", "tagged", "this",
    "throughout", "timeprecision", "timeunit", "type", "typedef", "union",
    "unique", "var", "virtual", "void",
    # Compiler directives (with ` prefix) — from IEEE 1800-2012 section 22.1
    "`__FILE__", "`__LINE", "`begin_keywords", "`celldefine",
    "`default_nettype", "`define", "`else", "`elsif", "`end_keywords",
    "`endcelldefine", "`endif", "`ifdef", "`ifndef", "`include", "`line",
    "`nounconnected_drive", "`pragma", "`resetall", "`timescale",
    "`unconnected_drive", "`undef", "`undefineall",
    # Non-IEEE compiler directives
    "`case", "`default", "`endfor", "`endprotect", "`endswitch",
    "`endwhile", "`for", "`format", "`if", "`let", "`protect",
    "`switch", "`time_scale", "`uselib", "`while",
}


@dataclass
class AlwaysSignals:
    """Result of parsing an always/initial block."""
    outputs_delayed: list[Signal] = field(default_factory=list)   # assigned with <=
    outputs_immediate: list[Signal] = field(default_factory=list)  # assigned with =
    temps: list[Signal] = field(default_factory=list)              # temp vars (case labels etc.)
    inputs: list[Signal] = field(default_factory=list)             # signals read (rvalue)


class AlwaysParser:
    """Recursive-descent parser for always/initial blocks.

    Scans an ``always`` block from the current buffer point to find all
    signals that are read (sensitivity-list candidates for AUTOSENSE) and
    all signals that are assigned (targets for AUTORESET).
    """

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self._buf = buf
        self._config = config

    def parse(self, limit: int | None = None) -> AlwaysSignals:
        """Scan from the current buffer point through the always block
        and return :class:`AlwaysSignals`.

        Port of ``verilog-read-always-signals``.
        """
        self._text = self._buf.buffer_string()
        self._pos = self._buf.point()
        self._limit = limit if limit is not None else len(self._text)

        # Accumulator lists (parallel to elisp dynamic vars)
        self._sigs_out_d: list[list] = []   # delayed (<=)
        self._sigs_out_i: list[list] = []   # immediate (=)
        self._sigs_out_unk: list[list] = [] # unknown (before = or <=)
        self._sigs_temp: list[list] = []    # temporaries
        self._sigs_in: list[list] = []      # inputs

        self._recurse(exit_keywd=None, rvalue=False, temp_next=False)

        # Remaining unknowns become immediate outputs
        self._sigs_out_i.extend(self._sigs_out_unk)
        self._sigs_out_unk = []

        return AlwaysSignals(
            outputs_delayed=self._to_signals(self._sigs_out_d),
            outputs_immediate=self._to_signals(self._sigs_out_i),
            temps=self._to_signals(self._sigs_temp),
            inputs=self._to_signals(self._sigs_in),
        )

    # ------------------------------------------------------------------
    # Recursive descent (port of verilog-read-always-signals-recurse)
    # ------------------------------------------------------------------

    def _recurse(
        self,
        exit_keywd: str | None,
        rvalue: bool,
        temp_next: bool,
    ) -> None:
        semi_rvalue = (exit_keywd == "endcase")
        last_keywd = ""
        sig_tolk = False
        sig_last_tolk = False
        gotend = False
        got_sig: list | None = None
        got_list: str | None = None  # name of the accumulator list
        end_else_check = False
        ignore_next = False

        while not (self._at_end() or gotend):
            # Skip whitespace
            self._skip_ws()
            if self._at_end():
                break

            # Comments
            if self._looking_at("//"):
                nl = self._text.find("\n", self._pos)
                self._pos = nl + 1 if nl >= 0 else self._limit
                continue
            if self._looking_at("/*"):
                end = self._text.find("*/", self._pos + 2)
                if end >= 0:
                    self._pos = end + 2
                else:
                    self._pos = self._limit
                continue
            if self._looking_at("(*"):
                self._pos += 1
                end = self._text.find("*)", self._pos)
                if end >= 0:
                    self._pos = end + 2
                else:
                    self._pos = self._limit
                continue

            # Read next token (keyword or single character)
            keywd = self._read_token()
            sig_last_tolk = sig_tolk
            sig_tolk = False

            if keywd == '"':
                # Skip string literal — _pos is on the opening "
                # Handle escaped quotes (e.g. \"quote\") inside the string.
                self._pos += 1  # skip past opening "
                while self._pos < self._limit:
                    ch = self._text[self._pos]
                    if ch == '\\':
                        self._pos += 2  # skip escaped character
                    elif ch == '"':
                        self._pos += 1  # skip closing "
                        break
                    else:
                        self._pos += 1
                # If we fell off the end without finding closing ", _pos is at limit
            elif end_else_check and keywd == "else":
                end_else_check = False
                self._skip_identifier()  # advance past "else"
            elif end_else_check and keywd and not keywd[0].isspace():
                gotend = True
            elif (exit_keywd and
                  (keywd == exit_keywd or
                   (exit_keywd == "'}" and keywd == "}")) and
                  not self._looking_at("::")):
                gotend = True
                if len(keywd) <= 1:
                    self._pos += 1  # single-char exit keyword like ), ], }
                else:
                    self._skip_identifier()  # multi-char exit keyword like end, endcase
            elif keywd == ";":
                ignore_next = False
                rvalue = semi_rvalue
                if not exit_keywd:
                    end_else_check = True
                self._pos_advance(1)
            elif keywd == "'":
                if self._match_at(r"'[sS]?[hdxboHDXBO]?[ \t]*[0-9a-fA-F_xzXZ?]+"):
                    pass  # _match_at advances _pos
                elif self._looking_at("'{"):
                    self._pos += 2
                    self._recurse("'}", True, False)
                else:
                    self._pos += 1
            elif keywd == ":":
                if self._looking_at("::"):
                    self._pos += 1
                elif exit_keywd == "endcase":
                    ignore_next = False
                    rvalue = False
                elif exit_keywd == "?":
                    pass
                elif exit_keywd == "]":
                    pass
                elif exit_keywd == "'}":
                    pass
                elif got_sig:
                    ignore_next = False
                    rvalue = semi_rvalue
                    got_sig = None
                elif not rvalue:
                    ignore_next = True
                    rvalue = False
                self._pos += 1
            elif keywd == "=":
                if got_sig:
                    self._append_to(got_list, got_sig)
                    got_sig = None
                if not rvalue:
                    if self._pos > 0 and self._text[self._pos - 1] == '<':
                        # Non-blocking: <=
                        self._sigs_out_d.extend(self._sigs_out_unk)
                        self._sigs_out_unk = []
                    else:
                        # Blocking: =
                        self._sigs_out_i.extend(self._sigs_out_unk)
                        self._sigs_out_unk = []
                ignore_next = False
                rvalue = True
                self._pos += 1
            elif keywd == "?":
                self._pos += 1
                self._recurse(":", rvalue, False)
            elif keywd == "[":
                self._pos += 1
                self._recurse("]", True, False)
            elif keywd == "(":
                self._pos += 1
                if sig_last_tolk:
                    got_sig = None
                if last_keywd == "for":
                    self._recurse(";", False, True)
                    self._recurse(";", True, False)
                    self._recurse(")", False, False)
                else:
                    self._recurse(")", True, False)
            elif keywd == "begin":
                self._skip_identifier()
                self._recurse("end", False, False)
                ignore_next = False
                rvalue = semi_rvalue
                if not exit_keywd:
                    end_else_check = True
            elif keywd in ("case", "casex", "casez", "randcase"):
                self._skip_identifier()
                self._recurse("endcase", True, False)
                ignore_next = False
                rvalue = semi_rvalue
                if not exit_keywd:
                    gotend = True
            elif keywd and re.match(r'^[$`a-zA-Z_]', keywd):
                if keywd in ("`ifdef", "`ifndef", "`elsif"):
                    ignore_next = True
                elif ignore_next or keywd in _KEYWORDS or keywd.startswith("$"):
                    ignore_next = False
                else:
                    # It's a signal name
                    keywd = self._detick_denumber(keywd)
                    if got_sig:
                        self._append_to(got_list, got_sig)

                    if temp_next:
                        got_list = "sigs_temp"
                    elif rvalue:
                        got_list = "sigs_in"
                    else:
                        got_list = "sigs_out_unk"

                    # Only add if not already in the target list
                    target = self._get_list(got_list)
                    if keywd and not any(s[0] == keywd for s in target):
                        got_sig = [keywd, None, None]
                    else:
                        got_sig = None
                    temp_next = False
                    sig_tolk = True
                self._skip_identifier()
            else:
                self._pos += 1

            last_keywd = keywd
            self._skip_ws()

        if got_sig:
            self._append_to(got_list, got_sig)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _at_end(self) -> bool:
        return self._pos >= self._limit

    def _skip_ws(self) -> None:
        while self._pos < self._limit and self._text[self._pos] in " \t\n\r\f":
            self._pos += 1

    def _looking_at(self, s: str) -> bool:
        return self._text[self._pos:self._pos + len(s)] == s

    def _pos_advance(self, n: int) -> None:
        self._pos += n

    def _read_token(self) -> str:
        """Read the next token at _pos without advancing.

        For identifiers/numbers, read the full word.
        For single chars, return the char.
        The caller is responsible for advancing _pos via
        ``_skip_identifier()`` or explicit ``_pos += 1``.
        """
        if self._pos >= self._limit:
            return ""
        ch = self._text[self._pos]
        if re.match(r'[a-zA-Z0-9$_.%`]', ch):
            # Identifier — read full word
            end = self._pos
            while end < self._limit and re.match(r'[a-zA-Z0-9$_.%`]', self._text[end]):
                end += 1
            return self._text[self._pos:end]
        else:
            # Single character — just return it, don't advance
            return ch

    def _skip_identifier(self) -> None:
        """Skip past the current identifier at _pos."""
        while self._pos < self._limit and re.match(r'[a-zA-Z0-9$_.%`]', self._text[self._pos]):
            self._pos += 1

    def _match_at(self, pattern: str) -> bool:
        """Match regex at current position.  If matched, advance _pos."""
        m = re.match(pattern, self._text[self._pos:])
        if m:
            self._pos += m.end()
            return True
        return False

    def _detick_denumber(self, name: str) -> str | None:
        """Remove ` prefix and resolve defines.

        Port of ``verilog-symbol-detick-denumber``.
        If the define resolves to a valid identifier, return it.
        If undefined and not auto_sense_defines_constant, return the
        symbol name without the tick (so it appears as-is in sensitivity lists).
        If the result is purely numeric, return None.
        """
        if name.startswith("`"):
            dname = name[1:]
            if dname in self._config.defines:
                resolved = self._config.defines[dname]
                # If the resolved value is a valid identifier, use it
                if resolved and re.match(r'^[a-zA-Z_][a-zA-Z_0-9$]*$', resolved):
                    return resolved
                # Check if it's a number
                if resolved and re.match(r'^[0-9]', resolved):
                    return None
                # Non-identifier define (e.g. "1:0") — treat as constant
                return None
            # Not in defines — wing-it behavior
            if self._config.auto_sense_defines_constant:
                return None
            # Return symbol without tick (it's an unknown macro, keep as signal)
            return name  # keep the ` prefix for output
        # Check if the name is purely numeric
        if re.match(r'^\d', name):
            return None
        return name

    def _append_to(self, list_name: str | None, sig: list) -> None:
        if list_name is None or sig is None:
            return
        target = self._get_list(list_name)
        if not any(s[0] == sig[0] for s in target):
            target.append(sig)

    def _get_list(self, name: str) -> list:
        if name == "sigs_temp":
            return self._sigs_temp
        elif name == "sigs_in":
            return self._sigs_in
        elif name == "sigs_out_unk":
            return self._sigs_out_unk
        return self._sigs_out_unk

    @staticmethod
    def _to_signals(raw: list[list]) -> list[Signal]:
        """Convert raw [name, bits, comment] lists to Signal objects."""
        out: list[Signal] = []
        seen: set[str] = set()
        for item in raw:
            name = item[0]
            if name and name not in seen:
                out.append(Signal(name=name, bits=item[1] if len(item) > 1 else None))
                seen.add(name)
        return out
