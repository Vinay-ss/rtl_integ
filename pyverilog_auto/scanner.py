"""Scanner — identify comment, string, and attribute regions.

Ported from ``verilog-scan-region`` (around line 3609 of
``verilog-mode.el``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .buffer import VerilogBuffer


class CommentRegion(NamedTuple):
    start: int
    end: int
    kind: str  # "line_comment", "block_comment", "string", "attribute"


class Scanner:
    """Scan a buffer and return all non-code regions."""

    def scan(self, buf: "VerilogBuffer") -> list[CommentRegion]:
        """Parse the full buffer and return sorted ``CommentRegion`` list.

        The scanner mimics the Emacs ``verilog-scan-region`` logic:
        the *start* character(s) of a region (e.g. ``//``, ``/*``, ``"``)
        are **not** considered "inside" the region — only positions
        strictly after the opening delimiter are marked.
        """
        text = buf.buffer_string()
        n = len(text)
        regions: list[CommentRegion] = []
        i = 0

        while i < n:
            # --- line comment ---
            if text[i] == "/" and i + 1 < n and text[i + 1] == "/":
                start = i
                i += 2  # skip "//"
                while i < n and text[i] != "\n":
                    i += 1
                if i < n:
                    i += 1  # skip the newline
                regions.append(CommentRegion(start + 1, i, "line_comment"))

            # --- block comment ---
            elif text[i] == "/" and i + 1 < n and text[i + 1] == "*":
                start = i
                i += 2  # skip "/*"
                while i < n:
                    if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                        i += 2
                        break
                    i += 1
                regions.append(CommentRegion(start + 1, i, "block_comment"))

            # --- string literal ---
            elif text[i] == '"':
                start = i
                i += 1
                while i < n:
                    if text[i] == "\\" and i + 1 < n:
                        i += 2  # skip escaped char
                        continue
                    if text[i] == '"':
                        i += 1
                        break
                    i += 1
                regions.append(CommentRegion(start + 1, i, "string"))

            # --- attribute (* ... *) ---
            elif text[i] == "(" and i + 1 < n and text[i + 1] == "*":
                # Make sure it's not "(* ... *)" inside a comment/string
                # For simplicity we scan sequentially so we won't be
                # inside one already.
                start = i
                i += 2  # skip "(*"
                while i < n:
                    if text[i] == "*" and i + 1 < n and text[i + 1] == ")":
                        i += 2
                        break
                    i += 1
                regions.append(CommentRegion(start + 1, i, "attribute"))

            else:
                i += 1

        return regions

    def is_in_comment(self, pos: int, regions: list[CommentRegion]) -> bool:
        """Return ``True`` if *pos* is inside a comment region."""
        for r in regions:
            if r.start <= pos < r.end and r.kind in ("line_comment", "block_comment"):
                return True
        return False

    def is_in_string(self, pos: int, regions: list[CommentRegion]) -> bool:
        """Return ``True`` if *pos* is inside a string region."""
        for r in regions:
            if r.start <= pos < r.end and r.kind == "string":
                return True
        return False

    def next_non_comment(
        self, buf: "VerilogBuffer", regions: list[CommentRegion]
    ) -> int:
        """Advance ``buf.point`` past any comment/string region.
        Return new point."""
        pos = buf.point()
        for r in regions:
            if r.start <= pos < r.end:
                buf.goto_char(r.end)
                return buf.point()
        return pos
