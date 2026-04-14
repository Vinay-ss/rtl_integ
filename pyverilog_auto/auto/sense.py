"""AutoSense — expand ``/*AUTOSENSE*/`` and ``/*AS*/`` markers.

Ported from ``verilog-auto-sense`` (lines 14131–14225) and
``verilog-auto-sense-sigs`` (lines 14114–14129) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import Signal, signals_not_in, signals_not_params, signals_sort_by_name

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoSense:
    """Expand ``/*AUTOSENSE*/`` or ``/*AS*/`` at current point."""

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
        """Generate sensitivity list after the ``/*AUTOSENSE*/`` marker.

        Port of ``verilog-auto-sense``.
        """
        from .wire import AutoWire
        from ..parser.always_parser import AlwaysParser

        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Find the opening ( before the AUTOSENSE marker
        start_pt = text.rfind("(", 0, pt)
        if start_pt < 0:
            return

        # Calculate indentation column (column after the opening paren)
        line_start = text.rfind("\n", 0, start_pt)
        line_start = 0 if line_start < 0 else line_start + 1
        indent_pt = start_pt - line_start + 1

        # Get current module declarations
        helper = AutoWire(self._buf, self._config, self._db)
        moddecls = helper._get_current_moddecls()

        # Memory signals (arrays) — exclude from sensitivity list
        sig_memories = [s for s in moddecls.vars if s.memory]

        # Find the closing ) of the sensitivity list
        close_paren = self._find_close_paren(text, start_pt)
        if close_paren is None:
            return

        # Read already-present signals: only before the AUTOSENSE marker.
        # The AutoDeleter has already cleared text between /*AUTOSENSE*/ and )
        # before expand() is called, so there is nothing to read after pt.
        # (Mirrors elisp verilog-auto-sense which reads start-pt to (point) only.)
        presense_sigs = self._read_signals_in_region(text, start_pt, pt)

        # Parse the always block to get signal usage
        # Start after the closing ) of the sensitivity list
        with self._buf.save_excursion():
            self._buf.goto_char(close_paren + 1)
            parser = AlwaysParser(self._buf, self._config)
            sigss = parser.parse()

        # Compute sensitivity list:
        # inputs - (outputs_delayed + outputs_immediate + temps + consts + gparams + presense)
        exclude = sigss.temps + moddecls.consts + moddecls.gparams + presense_sigs
        if not self._config.auto_sense_include_inputs:
            exclude = exclude + sigss.outputs_delayed + sigss.outputs_immediate

        sig_list = signals_not_in(sigss.inputs, exclude)

        # Remove parameters and numeric constants
        sig_list = signals_not_params(sig_list, self._config.defines)

        # Remove memory signals and insert comment if any were removed
        if sig_memories:
            orig_len = len(sig_list)
            sig_list = signals_not_in(sig_list, sig_memories)
            if len(sig_list) != orig_len:
                self._buf.insert(" /*memory or*/ ")

        # Check if we need "or" before the first signal.
        # Mirror elisp: scan backward from AUTOSENSE skipping `endif tokens
        # to find whether the preceding real token is "or" (or just "(").
        not_first = False
        if presense_sigs:
            region = text[start_pt:pt]
            # Strip the /*AUTOSENSE*/ comment itself, then trailing whitespace
            cleaned = re.sub(r'/\*.*?\*/', '', region, flags=re.DOTALL).rstrip()
            # Skip `endif / `else / `elsif tokens looking backward
            while True:
                m_endif = re.search(r'`\w+\s*$', cleaned)
                if m_endif:
                    cleaned = cleaned[:m_endif.start()].rstrip()
                else:
                    break
            if cleaned and not cleaned.endswith("or") and not cleaned.endswith("("):
                not_first = True

        # Sort and insert
        sig_list = signals_sort_by_name(sig_list)

        fill_column = self._config.fill_column
        parts: list[str] = []
        for sig in sig_list:
            name = sig.name
            if not_first:
                # Check if we need a line break
                col = self._current_column_approx(text, self._buf.point(), len(" or ".join(p for p in [""] + [name])))
                if 4 + self._approx_col(parts, indent_pt) + len(name) > fill_column:
                    parts.append("\n" + " " * indent_pt + "or " + name)
                else:
                    parts.append(" or " + name)
            else:
                parts.append(name)
            not_first = True

        self._buf.insert("".join(parts))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_signals_in_region(
        self, text: str, start: int, end: int
    ) -> list[Signal]:
        """Read signal names from text between start and end.

        Port of ``verilog-read-signals`` + ``verilog-signals-from-signame``.
        Mirrors the Emacs implementation which scans character-by-character,
        skipping comments and strings (including escaped quotes), and
        collecting identifiers (including backtick-prefixed macro names
        with dots like `` `ot.BOZ``).
        """
        skip = {"or", "and", "posedge", "negedge", "edge", "sensitive",
                "AUTOSENSE", "AS", "always"}
        sigs: list[Signal] = []
        seen: set[str] = set()
        i = start
        while i < end:
            ch = text[i]
            # Skip line comments
            if text[i:i+2] == '//':
                nl = text.find('\n', i)
                i = nl + 1 if nl >= 0 else end
                continue
            # Skip block comments
            if text[i:i+2] == '/*':
                close = text.find('*/', i + 2)
                i = close + 2 if close >= 0 else end
                continue
            # Skip string literals (handle escaped quotes)
            if ch == '"':
                i += 1
                while i < end:
                    sc = text[i]
                    if sc == '\\':
                        i += 2
                    elif sc == '"':
                        i += 1
                        break
                    else:
                        i += 1
                continue
            # Read identifier / backtick-prefixed name
            if re.match(r'[a-zA-Z0-9$_.%`]', ch):
                j = i
                while j < end and re.match(r'[a-zA-Z0-9$_.%`]', text[j]):
                    j += 1
                name = text[i:j]
                i = j
                if name not in skip and name not in seen:
                    sigs.append(Signal(name=name))
                    seen.add(name)
                continue
            i += 1
        return sigs

    @staticmethod
    def _find_close_paren(text: str, open_pos: int) -> int | None:
        """Find matching ) for ( at open_pos."""
        depth = 0
        i = open_pos
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    @staticmethod
    def _current_column_approx(text: str, pos: int, extra: int) -> int:
        """Approximate current column at pos."""
        line_start = text.rfind("\n", 0, pos)
        line_start = 0 if line_start < 0 else line_start + 1
        return pos - line_start + extra

    @staticmethod
    def _approx_col(parts: list[str], indent_pt: int) -> int:
        """Approximate current column from accumulated parts."""
        if not parts:
            return 0
        last = parts[-1]
        nl = last.rfind("\n")
        if nl >= 0:
            return len(last) - nl - 1
        total = sum(len(p) for p in parts)
        return total
