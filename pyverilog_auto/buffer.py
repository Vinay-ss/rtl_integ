"""VerilogBuffer — mutable text buffer with an Emacs-style cursor.

Every parser and AUTO function operates through this class, which
mimics the subset of Emacs buffer primitives that ``verilog-mode.el``
actually uses.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Optional

from .regex_compat import translate_emacs_regex


class VerilogBuffer:
    """Mutable character buffer with a ``point`` cursor."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, text: str, filepath: str = "<string>") -> None:
        self._text: list[str] = list(text)
        self._point: int = 0
        self._filepath: str = filepath
        self._last_match: Optional[re.Match] = None

        # Narrowing support
        self._narrow_beg: int = 0
        self._narrow_end: Optional[int] = None  # None means "full buffer"

        # Scan-region cache (populated lazily by scanner.py)
        self._scan_cache: object = None
        self._scan_tick: int = 0
        self._modify_tick: int = 0

        # Modi-cache: stores AUTO-generated signal lists so AUTOARG can
        # reproduce the Emacs ordering (reverse-alpha for auto-generated
        # signals, declaration order for explicit ones).
        # Keyed by module-keyword start position to isolate per-module.
        self._modi_cache: dict[int, dict[str, list]] = {}

    @classmethod
    def from_file(cls, filepath: str) -> "VerilogBuffer":
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return cls(text, os.path.abspath(filepath))

    @classmethod
    def from_string(cls, text: str, filepath: str = "<string>") -> "VerilogBuffer":
        return cls(text, filepath)

    def write_to_file(self, filepath: str | None = None) -> None:
        fp = filepath or self._filepath
        with open(fp, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.buffer_string())

    def buffer_string(self) -> str:
        """Return the entire buffer contents as a string."""
        return "".join(self._text)

    @property
    def filepath(self) -> str:
        return self._filepath

    # ------------------------------------------------------------------
    # Cursor movement
    # ------------------------------------------------------------------

    def point(self) -> int:
        return self._point

    def point_min(self) -> int:
        return self._narrow_beg

    def point_max(self) -> int:
        if self._narrow_end is not None:
            return self._narrow_end
        return len(self._text)

    def goto_char(self, pos: int) -> None:
        self._point = max(self.point_min(), min(pos, self.point_max()))

    def forward_char(self, n: int = 1) -> None:
        self.goto_char(self._point + n)

    def backward_char(self, n: int = 1) -> None:
        self.goto_char(self._point - n)

    def forward_line(self, n: int = 1) -> int:
        """Move forward *n* lines. Return the number of lines actually moved."""
        moved = 0
        if n > 0:
            for _ in range(n):
                # Find next newline from point
                idx = self._point
                pmax = self.point_max()
                while idx < pmax and self._text[idx] != "\n":
                    idx += 1
                if idx < pmax:
                    # Move past the newline
                    self._point = idx + 1
                    moved += 1
                else:
                    # Hit end of buffer — move to end
                    self._point = pmax
                    break
        elif n < 0:
            for _ in range(-n):
                if self._point <= self.point_min():
                    break
                # Move back to start of current line first
                self.beginning_of_line()
                if self._point > self.point_min():
                    # Move before the preceding newline
                    self._point -= 1
                    self.beginning_of_line()
                    moved += 1
        return moved

    def beginning_of_line(self) -> None:
        pmin = self.point_min()
        idx = self._point
        while idx > pmin and self._text[idx - 1] != "\n":
            idx -= 1
        self._point = idx

    def end_of_line(self) -> None:
        pmax = self.point_max()
        idx = self._point
        while idx < pmax and self._text[idx] != "\n":
            idx += 1
        self._point = idx

    def at_end(self) -> bool:
        """Equivalent to ``(eobp)``."""
        return self._point >= self.point_max()

    def at_start(self) -> bool:
        """Equivalent to ``(bobp)``."""
        return self._point <= self.point_min()

    # ------------------------------------------------------------------
    # Character access
    # ------------------------------------------------------------------

    def following_char(self) -> str:
        """Char at point (empty string at end)."""
        if self._point < self.point_max():
            return self._text[self._point]
        return ""

    def preceding_char(self) -> str:
        """Char before point (empty string at start)."""
        if self._point > self.point_min():
            return self._text[self._point - 1]
        return ""

    def char_after(self, pos: int | None = None) -> str:
        p = pos if pos is not None else self._point
        if self.point_min() <= p < self.point_max():
            return self._text[p]
        return ""

    def char_before(self, pos: int | None = None) -> str:
        p = pos if pos is not None else self._point
        if self.point_min() < p <= self.point_max():
            return self._text[p - 1]
        return ""

    # ------------------------------------------------------------------
    # Text inspection
    # ------------------------------------------------------------------

    def current_column(self) -> int:
        """Column number of point (0-indexed)."""
        idx = self._point
        col = 0
        while idx > self.point_min() and self._text[idx - 1] != "\n":
            idx -= 1
            col += 1
        return col

    def current_indentation(self) -> int:
        """Column of first non-whitespace character on the current line."""
        self_bol = self._line_start(self._point)
        idx = self_bol
        pmax = self.point_max()
        col = 0
        while idx < pmax and self._text[idx] in (" ", "\t"):
            if self._text[idx] == "\t":
                col = (col + 8) & ~7  # tab stops every 8
            else:
                col += 1
            idx += 1
        return col

    def _line_start(self, pos: int) -> int:
        """Return position of the start of the line containing *pos*."""
        idx = pos
        pmin = self.point_min()
        while idx > pmin and self._text[idx - 1] != "\n":
            idx -= 1
        return idx

    def _line_end(self, pos: int) -> int:
        """Return position of the end of the line containing *pos*."""
        idx = pos
        pmax = self.point_max()
        while idx < pmax and self._text[idx] != "\n":
            idx += 1
        return idx

    def line_beginning_position(self, n: int = 1) -> int:
        """Return beginning of line position.

        *n* = 1 → current line, *n* = 2 → next line, *n* = 0 → prev line.
        """
        pos = self._point
        if n > 1:
            for _ in range(n - 1):
                eol = self._line_end(pos)
                if eol < self.point_max():
                    pos = eol + 1
                else:
                    pos = eol
                    break
        elif n < 1:
            for _ in range(1 - n):
                ls = self._line_start(pos)
                if ls > self.point_min():
                    pos = ls - 1
                else:
                    pos = ls
                    break
        return self._line_start(pos)

    def line_end_position(self, n: int = 1) -> int:
        """Return end of line position.

        *n* = 1 → current line, *n* = 2 → next line, etc.
        """
        pos = self._point
        if n > 1:
            for _ in range(n - 1):
                eol = self._line_end(pos)
                if eol < self.point_max():
                    pos = eol + 1
                else:
                    pos = eol
                    break
        elif n < 1:
            for _ in range(1 - n):
                ls = self._line_start(pos)
                if ls > self.point_min():
                    pos = ls - 1
                else:
                    pos = ls
                    break
        return self._line_end(pos)

    def buffer_substring(self, beg: int, end: int) -> str:
        b = max(beg, self.point_min())
        e = min(end, self.point_max())
        if b >= e:
            return ""
        return "".join(self._text[b:e])

    # ------------------------------------------------------------------
    # Pattern matching (Emacs regex via regex_compat)
    # ------------------------------------------------------------------

    def _get_haystack(self, start: int, end: int) -> str:
        """Build substring for regex operations."""
        return "".join(self._text[start:end])

    def looking_at(self, pattern: str, flags: int = 0) -> Optional[re.Match]:
        """Match *pattern* (Emacs syntax) at point.  Sets ``_last_match``."""
        py_pat = translate_emacs_regex(pattern)
        haystack = self._get_haystack(self._point, self.point_max())
        m = re.match(py_pat, haystack, flags)
        if m:
            # Adjust match positions to buffer coordinates
            self._last_match = _OffsetMatch(m, self._point)
            return self._last_match
        self._last_match = None
        return None

    def looking_back(self, pattern: str, limit: int | None = None) -> bool:
        """Return ``True`` if text before point matches *pattern* (Emacs syntax)."""
        start = limit if limit is not None else self.point_min()
        haystack = self._get_haystack(start, self._point)
        py_pat = translate_emacs_regex(pattern)
        # Emacs looking-back checks if pattern matches ending at point
        m = re.search(py_pat + r"\Z", haystack)
        return m is not None

    def re_search_forward(
        self,
        pattern: str,
        bound: int | None = None,
        noerror: bool = True,
    ) -> Optional[int]:
        """Search forward for *pattern* from point.

        If found, move point to end of match and set ``_last_match``.
        Return the new point (end of match) or ``None``.
        """
        limit = bound if bound is not None else self.point_max()
        limit = min(limit, self.point_max())
        haystack = self._get_haystack(self._point, limit)
        py_pat = translate_emacs_regex(pattern)
        m = re.search(py_pat, haystack)
        if m:
            self._last_match = _OffsetMatch(m, self._point)
            self._point = self._point + m.end()
            return self._point
        self._last_match = None
        if not noerror:
            raise RuntimeError(f"Search failed: {pattern!r}")
        return None

    def re_search_backward(
        self,
        pattern: str,
        bound: int | None = None,
        noerror: bool = True,
    ) -> Optional[int]:
        """Search backward for *pattern* before point.

        If found, move point to start of match and set ``_last_match``.
        Return the new point (start of match) or ``None``.
        """
        limit = bound if bound is not None else self.point_min()
        limit = max(limit, self.point_min())
        haystack = self._get_haystack(limit, self._point)
        py_pat = translate_emacs_regex(pattern)
        # Find the *last* match in the haystack
        last: Optional[re.Match] = None
        for m in re.finditer(py_pat, haystack):
            last = m
        if last is not None:
            self._last_match = _OffsetMatch(last, limit)
            self._point = limit + last.start()
            return self._point
        self._last_match = None
        if not noerror:
            raise RuntimeError(f"Search failed: {pattern!r}")
        return None

    def match_string(self, group: int) -> Optional[str]:
        if self._last_match is None:
            return None
        try:
            return self._last_match.group(group)
        except IndexError:
            return None

    def match_beginning(self, group: int) -> Optional[int]:
        if self._last_match is None:
            return None
        try:
            return self._last_match.start(group)
        except IndexError:
            return None

    def match_end(self, group: int) -> Optional[int]:
        if self._last_match is None:
            return None
        try:
            return self._last_match.end(group)
        except IndexError:
            return None

    # ------------------------------------------------------------------
    # Text mutation
    # ------------------------------------------------------------------

    def insert(self, text: str) -> None:
        """Insert *text* at point and advance point past it."""
        chars = list(text)
        self._text[self._point:self._point] = chars
        self._point += len(chars)
        # Adjust narrowing end if set
        if self._narrow_end is not None:
            self._narrow_end += len(chars)
        self._invalidate_scan_cache()

    def delete_region(self, beg: int, end: int) -> None:
        """Delete text between *beg* and *end*."""
        b = max(beg, 0)
        e = min(end, len(self._text))
        if b >= e:
            return
        del self._text[b:e]
        removed = e - b
        # Adjust point
        if self._point > e:
            self._point -= removed
        elif self._point > b:
            self._point = b
        # Adjust narrowing
        if self._narrow_end is not None:
            if self._narrow_end > e:
                self._narrow_end -= removed
            elif self._narrow_end > b:
                self._narrow_end = b
        self._invalidate_scan_cache()

    def delete_char(self, n: int = 1) -> None:
        """Delete *n* characters forward from point."""
        if n > 0:
            self.delete_region(self._point, self._point + n)
        elif n < 0:
            self.delete_region(self._point + n, self._point)

    def replace_match(self, replacement: str, group: int = 0) -> None:
        """Replace the last match (or *group*) with *replacement*."""
        if self._last_match is None:
            return
        start = self._last_match.start(group)
        end = self._last_match.end(group)
        self.delete_region(start, end)
        saved = self._point
        self._point = start
        self.insert(replacement)
        # leave point after the replacement (Emacs behaviour)

    # ------------------------------------------------------------------
    # Indentation helpers
    # ------------------------------------------------------------------

    def indent_to(self, col: int) -> None:
        """Delete existing indentation on the current line and insert
        spaces to reach *col*."""
        bol = self._line_start(self._point)
        idx = bol
        pmax = self.point_max()
        while idx < pmax and self._text[idx] in (" ", "\t"):
            idx += 1
        self.delete_region(bol, idx)
        self._point = bol
        if col > 0:
            self.insert(" " * col)

    # ------------------------------------------------------------------
    # Context managers
    # ------------------------------------------------------------------

    @contextmanager
    def save_excursion(self):
        """Context manager that restores point (and narrowing) after the block."""
        saved_point = self._point
        saved_narrow_beg = self._narrow_beg
        saved_narrow_end = self._narrow_end
        try:
            yield
        finally:
            self._point = saved_point
            self._narrow_beg = saved_narrow_beg
            self._narrow_end = saved_narrow_end

    @contextmanager
    def save_restriction(self):
        """Context manager that restores narrowing bounds after the block."""
        saved_narrow_beg = self._narrow_beg
        saved_narrow_end = self._narrow_end
        try:
            yield
        finally:
            self._narrow_beg = saved_narrow_beg
            self._narrow_end = saved_narrow_end

    def narrow_to_region(self, beg: int, end: int) -> None:
        """Restrict buffer operations to ``[beg, end)``."""
        self._narrow_beg = max(beg, 0)
        self._narrow_end = min(end, len(self._text))
        # Clamp point
        if self._point < self._narrow_beg:
            self._point = self._narrow_beg
        if self._point > self._narrow_end:
            self._point = self._narrow_end

    def widen(self) -> None:
        """Remove narrowing restriction."""
        self._narrow_beg = 0
        self._narrow_end = None

    # ------------------------------------------------------------------
    # Scan-cache support (used by scanner.py)
    # ------------------------------------------------------------------

    def _invalidate_scan_cache(self) -> None:
        self._modify_tick += 1

    def scan_regions(self):
        """Return cached scan results, recomputing if stale.

        Imported lazily to avoid circular imports.
        """
        if self._scan_cache is None or self._scan_tick != self._modify_tick:
            from .scanner import Scanner

            self._scan_cache = Scanner().scan(self)
            self._scan_tick = self._modify_tick
        return self._scan_cache


# ------------------------------------------------------------------
# Helper: offset-adjusted Match wrapper
# ------------------------------------------------------------------

class _OffsetMatch:
    """Wraps a ``re.Match`` so that ``.start()``/``.end()`` return
    buffer-absolute positions rather than haystack-relative ones."""

    def __init__(self, match: re.Match, offset: int) -> None:
        self._match = match
        self._offset = offset

    def group(self, g: int = 0) -> str:
        return self._match.group(g)

    def start(self, g: int = 0) -> int:
        return self._match.start(g) + self._offset

    def end(self, g: int = 0) -> int:
        return self._match.end(g) + self._offset

    def __bool__(self) -> bool:
        return True
