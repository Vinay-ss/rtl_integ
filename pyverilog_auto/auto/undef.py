"""AutoUndef — expand ``/*AUTOUNDEF*/`` markers.

Ported from ``verilog-auto-undef`` (lines 14426–14491) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoUndef:
    """Expand ``/*AUTOUNDEF*/`` or ``/*AUTOUNDEF("regexp")*/``.

    Emits ```undef`` for all ```define``s since the last ``AUTOUNDEF``
    in the current file, minus any that have already been ```undef``'d.
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
        """Insert `undef statements after the marker.

        Port of ``verilog-auto-undef``.
        """
        from .wire import AutoWire

        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Read optional regexp parameter
        regexp = self._read_regexp_param(text, pt)
        indent_pt = self._current_indentation(text, pt)
        end_pt = pt

        # Search backward for previous AUTOUNDEF (to limit scope)
        prev_undef = text.rfind("/*AUTOUNDEF", 0, end_pt - 12)  # -12 to avoid matching self
        if prev_undef >= 0:
            # Find the end of that previous AUTOUNDEF marker
            prev_end = text.find("*/", prev_undef)
            if prev_end >= 0:
                scan_start = prev_end + 2
            else:
                scan_start = 0
        else:
            scan_start = 0

        # Scan for `define and `undef between scan_start and end_pt
        defs: list[str] = []
        define_re = re.compile(r'`(define|undef)\s+([a-zA-Z_][a-zA-Z_0-9]*)')

        for m in define_re.finditer(text, scan_start, end_pt):
            directive = m.group(1)
            name = m.group(2)
            if directive == "define":
                if regexp:
                    if re.search(regexp, name):
                        if name not in defs:
                            defs.append(name)
                else:
                    if name not in defs:
                        defs.append(name)
            else:  # undef
                if name in defs:
                    defs.remove(name)

        defs.sort()

        if not defs:
            return

        helper = AutoWire(self._buf, self._config, self._db)

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic undefs\n"
        )

        for d in defs:
            lines.append("`undef " + d + "\n")

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_regexp_param(text: str, pt: int) -> str | None:
        """Read optional regexp from ``/*AUTOUNDEF("regexp")*/``."""
        marker_start = text.rfind("/*AUTOUNDEF", 0, pt)
        if marker_start < 0:
            return None
        marker_end = text.find("*/", marker_start)
        if marker_end < 0:
            return None
        marker = text[marker_start:marker_end + 2]
        m = re.search(r'\("([^"]+)"\)', marker)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _current_indentation(text: str, pos: int) -> int:
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
