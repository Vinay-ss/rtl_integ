"""AutoReset — expand ``/*AUTORESET*/`` markers.

Ported from ``verilog-auto-reset`` (lines 14227–14335) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import Signal, signals_not_in_struct, signals_sort_by_name

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoReset:
    """Expand ``/*AUTORESET*/`` at current point.

    Generates reset assignments for all signals assigned in the enclosing
    ``always`` block.
    """

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
        """Insert reset assignments after ``/*AUTORESET*/``.

        Port of ``verilog-auto-reset``.
        """
        from .wire import AutoWire
        from ..parser.always_parser import AlwaysParser

        text = self._buf.buffer_string()
        pt = self._buf.point()

        indent_pt = self._current_indentation(text, pt)

        # Get module declarations
        helper = AutoWire(self._buf, self._config, self._db)
        moddecls = helper._get_current_moddecls()
        all_list = (
            moddecls.outputs + moddecls.inouts + moddecls.inputs
            + moddecls.vars + moddecls.consts + moddecls.gparams
        )

        # Read signals already in the reset block (between if/begin and AUTORESET)
        prereset_sigs = self._read_prereset_signals(text, pt)

        # Find the enclosing always block and parse it
        always_pt = self._find_always_start(text, pt)
        if always_pt is None:
            return

        with self._buf.save_excursion():
            self._buf.goto_char(always_pt)
            parser = AlwaysParser(self._buf, self._config)
            sigss = parser.parse()

        # Build the list of signals to reset:
        # delayed outputs + (immediate outputs if blocking_in_non or no delayed)
        dly_list = sigss.outputs_delayed
        sig_candidates = list(dly_list)
        if not sigss.outputs_delayed or self._config.auto_reset_blocking_in_non:
            sig_candidates.extend(sigss.outputs_immediate)

        # Exclude temporaries and pre-reset signals (struct-aware)
        sig_list = signals_not_in_struct(sig_candidates, sigss.temps + prereset_sigs)
        sig_list = signals_sort_by_name(sig_list)

        if not sig_list:
            return

        # Build the output text
        lines: list[str] = []
        lines.append("\n")
        lines.append(" " * indent_pt + "// Beginning of autoreset for uninitialized flops\n")

        dly_names = {s.name for s in dly_list}

        for sig in sig_list:
            # Look up full signal info from module declarations
            full_sig = self._find_sig(sig.name, all_list) or sig

            # Determine assignment operator
            if sig.name in dly_names:
                assign_op = " <= "
                if self._config.assignment_delay:
                    assign_op = f" <= {self._config.assignment_delay}"
            else:
                assign_op = " = "

            # Compute reset value
            reset_val = self._reset_value(full_sig)

            lines.append(
                " " * indent_pt
                + sig.name
                + assign_op
                + reset_val
                + ";\n"
            )

        lines.append(" " * indent_pt + "// End of automatics")

        self._buf.insert("".join(lines))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_value(self, sig: Signal) -> str:
        """Return the reset expression for this signal.

        Port of ``verilog-sig-tieoff``.
        """
        return sig.tieoff_value(self._config)

    def _read_prereset_signals(self, text: str, pt: int) -> list[Signal]:
        """Read signals already assigned between if/begin/case and AUTORESET.

        Port of reading prereset-sigs in ``verilog-auto-reset``.
        Uses ``verilog-read-signals`` approach: collects ALL identifiers
        (including dotted struct names) in the region, excluding keywords.
        """
        from ..parser.always_parser import _KEYWORDS

        # Search backward for the if/begin/case/always that opens this block
        block_start = self._find_block_start(text, pt)
        if block_start is None:
            return []

        region = text[block_start:pt]
        # Remove comments
        region = re.sub(r'//[^\n]*', '', region)
        region = re.sub(r'/\*.*?\*/', '', region, flags=re.DOTALL)

        # Collect all identifiers (including dotted names like csi.cmd)
        # This mirrors the elisp verilog-read-signals which is "overly
        # aggressive but fast" — it just grabs every identifier.
        sigs = []
        seen: set[str] = set()
        for m in re.finditer(r'[a-zA-Z_][a-zA-Z_0-9.]*[a-zA-Z_0-9]|[a-zA-Z_]', region):
            name = m.group(0)
            if name not in _KEYWORDS and name not in seen:
                sigs.append(Signal(name=name))
                seen.add(name)
        return sigs

    @staticmethod
    def _find_always_start(text: str, pt: int) -> int | None:
        """Find the start of the enclosing always block.

        Searches backward for ``@`` or ``always`` keyword.
        """
        # Search backward for always keyword or @
        patterns = [
            (r'\balways(?:_latch|_ff|_comb)?\b', True),
            (r'@', False),
        ]
        best = -1
        for pat, is_kw in patterns:
            for m in re.finditer(pat, text[:pt]):
                if m.start() > best:
                    best = m.start()

        if best >= 0:
            # Position after the sensitivity list
            # Find the ) that closes the sensitivity list
            at_pos = text.find("@", best)
            if at_pos >= 0 and at_pos < pt:
                paren = text.find("(", at_pos)
                if paren >= 0 and paren < pt:
                    # Find matching )
                    depth = 1
                    i = paren + 1
                    while i < pt and depth > 0:
                        if text[i] == "(":
                            depth += 1
                        elif text[i] == ")":
                            depth -= 1
                        i += 1
                    if depth == 0:
                        return i
            # always_comb / always_latch with no sensitivity list
            return best
        return None

    @staticmethod
    def _find_block_start(text: str, pt: int) -> int | None:
        """Find the start of the if/begin/case block before AUTORESET."""
        pattern = re.compile(
            r'@|'
            r'\b(?:begin|if|case[xz]?|always(?:_latch|_ff|_comb)?)\b'
        )
        best = None
        for m in pattern.finditer(text[:pt]):
            best = m.start()
        return best

    @staticmethod
    def _current_indentation(text: str, pos: int) -> int:
        """Return indentation of the line containing pos."""
        line_start = text.rfind("\n", 0, pos)
        line_start = 0 if line_start < 0 else line_start + 1
        col = 0
        i = line_start
        while i < len(text) and text[i] in " \t":
            if text[i] == "\t":
                col = (col + 8) & ~7
            else:
                col += 1
            i += 1
        return col

    @staticmethod
    def _find_sig(name: str, sigs: list[Signal]) -> Signal | None:
        """Find a signal by name in a list."""
        for s in sigs:
            if s.name == name:
                return s
        return None
