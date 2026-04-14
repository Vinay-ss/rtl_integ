"""AutoInjector — inject AUTO markers into existing code.

Ported from ``verilog-inject-arg``, ``verilog-inject-sense``, and
``verilog-inject-inst`` (lines 11799–11872) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoInjector:
    """Insert ``/*AUTO*/`` markers into existing Verilog code."""

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._buf = buf
        self._config = config
        self._db = db

    def inject_arg(self) -> None:
        """Add ``/*AUTOARG*/`` to module port lists that don't have it.

        Port of ``verilog-inject-arg``.
        """
        text = self._buf.buffer_string()
        module_re = re.compile(r'\b(?:connect)?module\b', re.IGNORECASE)
        endmod_re = re.compile(r'\bend(?:connect)?module\b', re.IGNORECASE)

        offset = 0
        for m in module_re.finditer(text):
            mod_start = m.start()
            # Find matching endmodule
            em = endmod_re.search(text, m.end())
            if not em:
                continue
            endmod_pt = em.start()

            # Check if AUTOARG already exists in this module
            region = text[mod_start:endmod_pt]
            if "/*AUTOARG*/" in region:
                continue

            # Find the first ; in the module header
            semi = text.find(";", m.end())
            if semi < 0 or semi > endmod_pt:
                continue

            # Look backward from ; for )
            i = semi - 1
            while i >= m.end() and text[i] in " \t\n\r\f":
                i -= 1
            if i >= m.end() and text[i] == ")":
                # Insert /*AUTOARG*/ before the )
                insert_pos = i
                self._buf.goto_char(insert_pos)
                self._buf.insert("/*AUTOARG*/")
                # Refresh text
                text = self._buf.buffer_string()

    def inject_sense(self) -> None:
        """Add ``/*AUTOSENSE*/`` to ``always @(...)`` blocks.

        Port of ``verilog-inject-sense``.
        """
        text = self._buf.buffer_string()
        always_re = re.compile(r'\balways\s*@\s*\(', re.IGNORECASE)

        for m in always_re.finditer(text):
            start_pt = m.end()  # right after (

            # Find matching )
            depth = 1
            i = start_pt
            while i < len(text) and depth > 0:
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1

            if depth != 0:
                continue

            close_pt = i - 1  # position of )
            region = text[start_pt:close_pt]

            # Check if AUTOSENSE or AS already present
            if "AUTOSENSE" in region or "/*AS*/" in region:
                continue

            # Check if it's already * or posedge/negedge
            stripped = region.strip()
            if stripped == "*":
                continue
            if "posedge" in stripped or "negedge" in stripped:
                continue

            # Simple case: replace the contents with /*AS*/
            # Only do this if the sens list exactly matches what we'd generate
            # For safety, just insert /*AS*/ at the start
            self._buf.goto_char(start_pt)
            # Delete existing contents
            self._buf.delete_region(start_pt, close_pt)
            self._buf.insert("/*AS*/")
            # Refresh text
            text = self._buf.buffer_string()

    def inject_inst(self) -> None:
        """Add ``/*AUTOINST*/`` to bare module instantiations.

        Port of ``verilog-inject-inst``.
        """
        text = self._buf.buffer_string()
        # Find patterns like: .port_name (signal_name)
        # which indicate a module instantiation
        port_re = re.compile(
            r'\.\s*[a-zA-Z0-9`_$]+\s*\(\s*[a-zA-Z0-9`_$]+\s*\)'
        )

        offset = 0
        for m in port_re.finditer(text):
            pos = m.start()
            # Find the opening ( of the instantiation
            open_paren = self._find_open_paren(text, pos)
            if open_paren is None:
                continue

            # Check for #( — this is a parameter block, skip
            if open_paren > 0 and text[open_paren - 1] == "#":
                continue

            # Find the closing )
            close_paren = self._find_close_paren(text, open_paren)
            if close_paren is None:
                continue

            # Check if AUTOINST or .* already present
            region = text[open_paren:close_paren]
            if "AUTOINST" in region or ".*" in region:
                continue

            # TODO: Full injection logic removes matching .name(name) pairs
            # and inserts /*AUTOINST*/. For now, just skip already-instantiated
            # modules.

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_open_paren(text: str, pos: int) -> int | None:
        """Find the ( that opens the instantiation containing pos."""
        depth = 1
        i = pos - 1
        while i >= 0 and depth > 0:
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
            i -= 1
        return (i + 1) if depth == 0 else None

    @staticmethod
    def _find_close_paren(text: str, open_pos: int) -> int | None:
        """Find matching ) for ( at open_pos."""
        depth = 0
        i = open_pos
        n = len(text)
        while i < n:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
