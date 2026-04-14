"""AutoWire / AutoLogic — expand AUTOWIRE and AUTOLOGIC markers.

Ported from ``verilog-auto-wire`` (lines 13166–13230) and
``verilog-auto-logic`` / ``verilog-auto-logic-setup`` (lines 13141–13164)
and ``verilog-insert-definition`` (lines 11301–11363) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from ..signal import (
    ModDecls,
    Signal,
    SubDecls,
    signals_combine_bus,
    signals_not_in,
    signals_sort_by_name,
)

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoWire:
    """Expand ``/*AUTOWIRE*/`` markers in a :class:`VerilogBuffer`."""

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._buf = buf
        self._config = config
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def expand(self) -> None:
        """Insert wire declarations for undeclared instantiated-module
        outputs after ``/*AUTOWIRE*/``.

        Port of ``verilog-auto-wire``.
        """
        indent_pt = self._current_indentation()
        moddecls = self._get_current_moddecls()
        modsubdecls = self._get_current_sub_decls()

        # Signals needing wire declarations: sub-outputs + sub-inouts
        # that aren't already declared in the current module
        all_signals = self._get_all_signals(moddecls)
        sub_outs = modsubdecls.outputs + modsubdecls.inouts
        simplify = self._config.auto_simplify_expressions
        sig_list = signals_combine_bus(
            signals_not_in(sub_outs, all_signals), simplify=simplify)

        if not sig_list:
            return

        # Sort by name
        sig_list = signals_sort_by_name(sig_list)

        # Determine the wire type keyword
        wire_type = self._config.auto_wire_type or "wire"

        # Build the insertion text
        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic wires (for undeclared instantiated-module outputs)\n"
        )

        for sig in sig_list:
            line = self._format_definition(sig, wire_type, indent_pt)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        # Insert after current line
        self._forward_or_insert_line()
        self._buf.insert("".join(lines))

    # ------------------------------------------------------------------
    # Definition formatting (port of verilog-insert-one-definition +
    # verilog-insert-definition)
    # ------------------------------------------------------------------

    def _format_definition(
        self,
        sig: Signal,
        direction: str,
        indent_pt: int,
        v2k: bool = False,
    ) -> str:
        """Format a single wire/logic/reg definition line.

        Port of ``verilog-insert-one-definition`` + ``verilog-insert-definition``.
        When *v2k* is True, use ``,`` instead of ``;`` (V2K port list).
        """
        # Determine the type keyword
        sig_type_str = self._compute_type_keyword(sig, direction)

        # Build definition: type [signed] [multidim] [bits] name [memory];
        parts: list[str] = [sig_type_str]

        if sig.modport:
            parts.append(f".{sig.modport}")

        if sig.signed:
            parts.append(f" {sig.signed}")

        if sig.multidim:
            if self._config.auto_simplify_expressions:
                from ..signal import simplify_range
                parts.append(" " + "".join(
                    simplify_range(d) for d in sig.multidim
                ))
            else:
                parts.append(" " + "".join(sig.multidim))

        if sig.bits:
            if self._config.auto_simplify_expressions:
                from ..signal import simplify_range
                parts.append(f" {simplify_range(sig.bits)}")
            else:
                parts.append(f" {sig.bits}")

        # Join type parts
        type_str = "".join(parts)

        # Indent to start
        line = " " * indent_pt + type_str

        # Pad to name column: max(24, indent_pt + 16)
        name_col = max(24, indent_pt + 16)
        if len(line) < name_col:
            line += " " * (name_col - len(line))
        elif not line.endswith(" "):
            line += " "

        line += sig.name

        if sig.memory:
            line += f" {sig.memory}"

        # Escaped identifiers are terminated by whitespace — add a
        # space before the delimiter so the name doesn't merge with
        # the semicolon/comma.
        if sig.name.startswith("\\"):
            line += " "

        line += "," if v2k else ";"

        # Add comment
        if self._config.auto_wire_comment and sig.comment:
            comment_col = max(48, indent_pt + 40)
            if len(line) < comment_col:
                line += " " * (comment_col - len(line))
            else:
                line += " "
            line += f"// {sig.comment}"

        line += "\n"
        return line

    def _compute_type_keyword(self, sig: Signal, direction: str) -> str:
        """Determine the type keyword for a signal definition.

        Port of the type-selection logic in ``verilog-insert-definition``.
        """
        # For AUTOREG, direction is "reg" — use it directly
        if direction == "reg":
            return "reg"

        # For AUTOINOUTPARAM, direction is "parameter" — use it directly
        if direction == "parameter":
            return "parameter"

        # For AUTOINOUTMODULE interface signals
        if direction == "interface":
            return sig.type or direction

        auto_wire_type = self._config.auto_wire_type

        if auto_wire_type == "wire":
            if sig.type is None or sig.type == "logic":
                return "wire" if direction not in ("input", "output", "inout") else direction
            return sig.type or auto_wire_type

        if sig.type or auto_wire_type:
            prefix = ""
            if direction in ("input", "output", "inout"):
                prefix = direction + " "
            return prefix + (sig.type or auto_wire_type or "wire")

        if self._config.auto_declare_nettype and direction in ("input", "output", "inout"):
            return f"{direction} {self._config.auto_declare_nettype}"

        return direction if direction in ("input", "output", "inout") else "wire"

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _current_indentation(self) -> int:
        """Return the indentation of the current line."""
        text = self._buf.buffer_string()
        pos = self._buf.point()
        # Find start of line
        line_start = text.rfind("\n", 0, pos)
        line_start = 0 if line_start < 0 else line_start + 1
        # Count leading whitespace
        col = 0
        i = line_start
        while i < len(text) and text[i] in " \t":
            if text[i] == "\t":
                col = (col + 8) & ~7
            else:
                col += 1
            i += 1
        return col

    def _forward_or_insert_line(self) -> None:
        """Move to the next line, inserting a newline if needed."""
        text = self._buf.buffer_string()
        pos = self._buf.point()
        nl = text.find("\n", pos)
        if nl >= 0:
            self._buf.goto_char(nl + 1)
        else:
            self._buf.goto_char(len(text))
            self._buf.insert("\n")

    def _current_module_pos(self) -> int:
        """Return the start position of the current module keyword.

        Used as a cache key to isolate per-module state.
        """
        text = self._buf.buffer_string()
        pos = self._buf.point()
        mod_re = re.compile(r"\b(?:module|interface|program)\b")
        last_match = None
        for m in mod_re.finditer(text, 0, pos):
            if self._is_in_comment_or_string(text, m.start()):
                continue
            last_match = m
        return last_match.start() if last_match else 0

    def _get_modi_cache(self) -> dict[str, list]:
        """Get or create the modi-cache dict for the current module."""
        mod_pos = self._current_module_pos()
        if mod_pos not in self._buf._modi_cache:
            self._buf._modi_cache[mod_pos] = {
                "outputs": [],
                "inputs": [],
                "inouts": [],
            }
        return self._buf._modi_cache[mod_pos]

    def _get_current_moddecls(self) -> ModDecls:
        """Parse declarations of the current module."""
        from ..parser.decl_parser import DeclParser

        with self._buf.save_excursion():
            text = self._buf.buffer_string()
            pos = self._buf.point()
            mod_re = re.compile(r"\b(?:module|interface|program)\b")
            last_match = None
            for m in mod_re.finditer(text, 0, pos):
                # Skip matches inside comments or strings
                if self._is_in_comment_or_string(text, m.start()):
                    continue
                last_match = m
            if last_match:
                rest = text[last_match.end():]
                paren_m = re.search(r"[;(]", rest)
                if paren_m:
                    parse_pt = last_match.end() + paren_m.end()
                    self._buf.goto_char(parse_pt)
                    parser = DeclParser(self._buf, self._config)
                    return parser.parse()

        return ModDecls()

    @staticmethod
    def _is_in_comment_or_string(text: str, pos: int) -> bool:
        """Check if position is inside a comment or string."""
        # Find start of line
        line_start = text.rfind("\n", 0, pos)
        line_start = 0 if line_start < 0 else line_start + 1
        line_before = text[line_start:pos]
        # Check for // line comment
        if "//" in line_before:
            comment_pos = line_before.find("//")
            # Make sure it's not inside a string
            in_str = False
            for ch in line_before[:comment_pos]:
                if ch == '"':
                    in_str = not in_str
            if not in_str:
                return True
        # Check for /* block comment */
        # Search backwards for /* that isn't closed by */
        last_open = text.rfind("/*", 0, pos)
        if last_open >= 0:
            last_close = text.rfind("*/", last_open, pos)
            if last_close < 0:
                return True
        return False

    def _get_current_sub_decls(self) -> SubDecls:
        """Parse sub-instance declarations from the current module."""
        from ..parser.sub_decls import SubDeclParser

        parser = SubDeclParser(self._buf, self._config, self._db)
        return parser.parse()

    def _in_paren_count_quick(self) -> int:
        """Return parenthesis nesting depth at point.

        Port of ``verilog-in-paren-count-quick``.  Returns > 0 when
        inside a V2K port list.
        """
        text = self._buf.buffer_string()
        pos = self._buf.point()
        # Find the module keyword before point
        mod_re = re.compile(r"\b(?:module|interface|program)\b")
        mod_start = 0
        for m in mod_re.finditer(text, 0, pos):
            mod_start = m.start()
        # Count unmatched ( in the region from module to point
        depth = 0
        for ch in text[mod_start:pos]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        return max(depth, 0)

    def _repair_open_comma(self) -> None:
        """Insert comma after the previous port declaration if needed.

        Port of ``verilog-repair-open-comma``.  Skips backward over
        whitespace, line comments, and block comments (including AUTO
        markers like ``/*AUTOINPUT*/``).
        Preserves cursor position (adjusting for inserted comma).
        """
        saved_pt = self._buf.point()
        text = self._buf.buffer_string()
        i = self._backward_syntactic_ws(text, saved_pt - 1)
        if i < 0:
            return
        # Don't add comma after ( or existing ,
        if text[i] in ",(":
            return
        # Don't add comma after *) (attribute)
        if i > 0 and text[i - 1:i + 1] == "*)":
            return
        # Don't add comma after backtick-define (e.g. `endif, `ifdef)
        j = i
        while j >= 0 and (text[j].isalnum() or text[j] in "_`"):
            if text[j] == "`":
                return
            j -= 1
        # Insert comma after position i
        insert_pos = i + 1
        self._buf.goto_char(insert_pos)
        self._buf.insert(",")
        # Restore cursor position, adjusted for the inserted comma
        self._buf.goto_char(saved_pt + 1)

    def _repair_close_comma(self) -> None:
        """Remove trailing comma before the closing ``)`` of the port list.

        Port of ``verilog-repair-close-comma``.
        Preserves cursor position (adjusting for removed comma).
        """
        saved_pt = self._buf.point()
        text = self._buf.buffer_string()
        # Find the closing ) by scanning forward with depth tracking
        depth = 0
        i = saved_pt
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                if depth == 0:
                    break
                depth -= 1
            i += 1
        if i >= len(text):
            return
        close_paren = i
        # Scan backward over whitespace and comments to find last ,
        j = self._backward_syntactic_ws(text, close_paren - 1)
        if j >= 0 and text[j] == ",":
            self._buf.goto_char(j)
            self._buf.delete_char(1)
            # Restore cursor position, adjusted for the removed comma
            if saved_pt > j:
                self._buf.goto_char(saved_pt - 1)
            else:
                self._buf.goto_char(saved_pt)

    @staticmethod
    def _backward_syntactic_ws(text: str, idx: int) -> int:
        """Skip backward over whitespace, line comments and block comments."""
        while idx >= 0:
            if text[idx] in " \t\n\r\f":
                idx -= 1
            elif idx >= 1 and text[idx - 1:idx + 1] == "*/":
                # Block comment — find matching /*
                idx -= 2
                while idx >= 1:
                    if text[idx - 1:idx + 1] == "/*":
                        idx -= 2
                        break
                    idx -= 1
                else:
                    break
            else:
                # Check if inside a // line comment
                line_start = text.rfind("\n", 0, idx + 1)
                line_start = 0 if line_start < 0 else line_start + 1
                line_before = text[line_start:idx + 1]
                cp = line_before.find("//")
                if cp >= 0:
                    idx = line_start + cp - 1
                    continue
                break
        return idx

    @staticmethod
    def _get_all_signals(moddecls: ModDecls) -> list[Signal]:
        """Return all declared signals (port of verilog-decls-get-signals)."""
        return (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
            + moddecls.vars
            + moddecls.consts
            + moddecls.gparams
        )


class AutoLogic:
    """Expand ``/*AUTOLOGIC*/`` markers — same as AUTOWIRE but with logic type."""

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._buf = buf
        self._config = config
        self._db = db

    def expand(self) -> None:
        """Expand ``/*AUTOLOGIC*/`` at current point."""
        # Set auto_wire_type to "logic" if not already set
        if not self._config.auto_wire_type:
            self._config.auto_wire_type = "logic"
        wire = AutoWire(self._buf, self._config, self._db)
        wire.expand()
