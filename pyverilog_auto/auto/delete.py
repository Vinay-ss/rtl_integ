"""AutoDeleter — remove AUTO-generated sections from a VerilogBuffer.

Ported from ``verilog-delete-auto-buffer`` (around line 11700 of
``verilog-mode.el``), ``verilog-delete-autos-lined`` (line 11580),
``verilog-delete-to-paren`` (line 11637), and
``verilog-delete-auto-star-all`` (line 11654).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


# Regex for the multi-line AUTO markers (e.g. /*AUTOWIRE*/, /*AUTOINPUT("regexp")*/)
# Matches /*AUTO<word>*/ with optional parenthesised or quoted parameters.
_AUTO_LINED_RE = re.compile(
    r"/\*AUTO[A-Za-z0-9_]+"
    r"(?:\([^)]*\)|(?:\"[^\"]*\"))?"
    r".*?"
    r"\*/",
    re.IGNORECASE,
)

# Regex for the parenthesis-delimited AUTO markers (AUTOARG, AUTOINST, etc.)
# These delete from the marker forward to the matching close-paren.
_AUTO_PAREN_KEYWORDS = (
    r"/\*(?:AS|AUTOARG|AUTOCONCATWIDTH|AUTOINST|AUTOINSTPARAM|AUTOSENSE)"
    r"(?:\(.*?\))?"
    r"\*/"
)
_AUTO_PAREN_RE = re.compile(_AUTO_PAREN_KEYWORDS, re.IGNORECASE)

# Comment section markers used by AUTOINST expansion
_INST_COMMENT_RE = re.compile(
    r"\b(?:Outputs|Inouts|Inputs|Interfaces|Interfaced)\b"
)

# Templated / Implicit .* trailing comments
_TEMPLATE_COMMENT_RE = re.compile(
    r"[ \t]*// (?:Templated(?:[ \t]*AUTONOHOOKUP)?|Implicit \.\*)"
    r"(?:[ \tLT0-9]*| LHS: .*)$",
    re.MULTILINE,
)


class AutoDeleter:
    """Remove AUTO-generated sections from a :class:`VerilogBuffer`."""

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self._buf = buf
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def delete(self) -> None:
        """Remove all AUTO-generated sections from *buf* in-place.

        Port of ``verilog-delete-auto-buffer``.
        """
        # 1) Remove multi-line AUTO insertions (// Beginning … // End of automatics)
        self._search_do(_AUTO_LINED_RE, self._delete_autos_lined)

        # 2) Remove paren-delimited AUTO insertions (AUTOARG, AUTOINST, etc.)
        self._search_do(_AUTO_PAREN_RE, self._delete_to_paren)

        # 3) Remove .* (AUTOSTAR) expansions
        self._search_do_literal(".*", self._delete_auto_star_all)

        # 4) Remove trailing // Templated and // Implicit .* comments
        self._delete_template_comments()

    def delete_auto_star(self) -> None:
        """Remove AUTOSTAR (``.*``) expansions only.

        Port of ``verilog-delete-auto-star-implicit``.
        Removes all ``// Implicit .*`` lines, preserves the closing
        ``)`` or ``);``, and removes empty section headers.
        """
        text = self._buf.buffer_string()
        if "// Implicit .*" not in text:
            return

        lines = text.split("\n")
        # Identify lines to remove: those containing "// Implicit .*"
        # For lines with the closing ));, detect by checking paren balance
        implicit_indices = set()
        close_on_implicit: dict[int, str] = {}  # line index -> ");" or ")"

        for i, line in enumerate(lines):
            if "// Implicit .*" not in line:
                continue
            implicit_indices.add(i)
            # Check if the instantiation close is on this line.
            # The pattern is: .name (expr)); or .name (expr))
            # Count parens before the comment
            pre = line[:line.index("// Implicit .*")]
            open_count = pre.count("(")
            close_count = pre.count(")")
            # If there are more ) than (, the extras close the instantiation
            if close_count > open_count:
                if ");" in pre:
                    close_on_implicit[i] = ");"
                else:
                    close_on_implicit[i] = ")"

        if not implicit_indices:
            return

        # Build new line list, removing implicit lines
        new_lines = []
        for i, line in enumerate(lines):
            if i in implicit_indices:
                # If this was the line with the close, we'll handle it below
                continue
            new_lines.append(line)

        # Rebuild text
        new_text = "\n".join(new_lines)

        # For each close that was on an implicit line, add it back
        # Find the .* in the instantiation and add ); after the last
        # remaining pin or after .*
        for idx, close_str in sorted(close_on_implicit.items(), reverse=True):
            # Re-split since we may modify
            nlines = new_text.split("\n")
            # Find the corresponding .* by searching backward from approximately
            # where the removed line was
            approx = min(idx, len(nlines) - 1)
            # Search backward for a line with .* or a pin connection or section header
            insert_at = approx
            for j in range(min(approx, len(nlines) - 1), -1, -1):
                stripped = nlines[j].strip()
                if stripped.startswith(".") or ".*" in stripped:
                    insert_at = j
                    break
                if re.match(r"//\s*(?:Outputs|Inouts|Inputs|Interfaces|Interfaced)\s*$", stripped):
                    insert_at = j
                    break

            # Modify the line at insert_at: remove trailing comma and add close
            target = nlines[insert_at].rstrip()
            if target.endswith(","):
                target = target[:-1]
            target += close_str
            nlines[insert_at] = target
            new_text = "\n".join(nlines)

        # Remove empty section headers
        nlines = new_text.split("\n")
        to_remove = set()
        for i, line in enumerate(nlines):
            stripped = line.strip()
            if not re.match(r"//\s*(?:Outputs|Inouts|Inputs|Interfaces|Interfaced)\s*$", stripped):
                continue
            has_content = False
            for j in range(i + 1, len(nlines)):
                ns = nlines[j].strip()
                if not ns:
                    continue
                if re.match(r"//\s*(?:Outputs|Inouts|Inputs|Interfaces|Interfaced)\s*$", ns):
                    break
                if ns.startswith(")") or ns == ");":
                    break
                if ".*" in ns:
                    break
                has_content = True
                break
            if not has_content:
                to_remove.add(i)

        if to_remove:
            nlines = [l for i, l in enumerate(nlines) if i not in to_remove]
            new_text = "\n".join(nlines)

        # Clean up trailing comma after .* when no pins follow
        new_text = re.sub(r"(\.\*)\s*,(\s*[\n\r]*\s*\))", r"\1\2", new_text)

        # Apply changes
        self._buf.goto_char(0)
        self._buf.delete_region(0, len(self._buf._text))
        self._buf.insert(new_text)

    # ------------------------------------------------------------------
    # Internal: search-and-apply loop
    # ------------------------------------------------------------------

    def _search_do(self, pattern: re.Pattern, handler) -> None:
        """Port of ``verilog-auto-re-search-do``.

        Scan from beginning of buffer; at each match call *handler*
        with point positioned after the match.
        Skips matches inside ``//`` line comments.
        """
        self._buf.goto_char(self._buf.point_min())
        text = self._buf.buffer_string()
        offset = 0
        for m in pattern.finditer(text):
            # Recompute because handler may have mutated the buffer
            cur_text = self._buf.buffer_string()
            # Find the match in the (possibly shifted) buffer
            new_m = pattern.search(cur_text, offset)
            if new_m is None:
                break
            # Skip matches inside // line comments
            line_start = cur_text.rfind("\n", 0, new_m.start())
            line_start = 0 if line_start < 0 else line_start + 1
            line_prefix = cur_text[line_start:new_m.start()]
            if "//" in line_prefix:
                self._buf.goto_char(new_m.end())
                offset = self._buf.point()
                continue
            self._buf.goto_char(new_m.end())
            handler()
            offset = self._buf.point()

    def _search_do_literal(self, literal: str, handler) -> None:
        """Like ``_search_do`` but for a literal string (``.*``)."""
        while True:
            text = self._buf.buffer_string()
            pos = text.find(literal, self._buf.point())
            if pos < 0:
                break
            # Make sure this .* is not inside a comment or string literal
            # and is actually a Verilog .* (preceded by valid context)
            self._buf.goto_char(pos + len(literal))
            handler()

    # ------------------------------------------------------------------
    # Deletion strategies
    # ------------------------------------------------------------------

    def _delete_autos_lined(self) -> None:
        """Port of ``verilog-delete-autos-lined``.

        After matching a ``/*AUTO..*/`` marker, delete the following
        ``// Beginning of automatic...`` through ``// End of automatics``
        block (inclusive).
        """
        # Point is right after the /*AUTO...*/ marker.
        # Move to the next line.
        self._buf.forward_line(1)
        pt = self._buf.point()

        # Check if the next line starts with "// Beginning"
        eol = self._buf._line_end(pt)
        line = self._buf.buffer_substring(pt, eol).lstrip()
        if not line.startswith("// Beginning"):
            return

        # Search forward for "// End of automatic"
        text = self._buf.buffer_string()
        idx = text.find("// End of automatic", pt)
        if idx < 0:
            return

        # Find end of that line + newline
        end_eol = text.find("\n", idx)
        if end_eol >= 0:
            end_pos = end_eol + 1
        else:
            end_pos = len(text)

        self._buf.delete_region(pt, end_pos)

    def _delete_to_paren(self) -> None:
        """Port of ``verilog-delete-to-paren``.

        After matching AUTOARG / AUTOINST / etc., delete from current
        point forward to just before the matching close-paren of the
        enclosing instantiation.
        """
        start = self._buf.point()

        # Walk backward to find the opening paren of the instantiation
        open_pos = self._backward_open_paren(start)
        if open_pos is None:
            return

        # Find matching close paren
        close_pos = self._forward_sexp(open_pos)
        if close_pos is None:
            return

        # Delete from current point to just before the close paren
        end = close_pos - 1  # just before the )
        if end > start:
            self._buf.delete_region(start, end)

    def _delete_auto_star_all(self) -> None:
        """Port of ``verilog-delete-auto-star-all``.

        Delete a .* AUTOINST expansion if it is safe (followed only by
        whitespace/commas and a close-paren or section comment).
        """
        if not self._config.auto_star_expand:
            return

        start = self._buf.point()
        text = self._buf.buffer_string()

        # Check what follows the .* — must be whitespace/commas then ) or // section comment
        rest = text[start:]
        m = re.match(r"[ \t\n\f,]*(?:\)|// (?:Outputs|Inouts|Inputs|Interfaces|Interfaced)\b)", rest)
        if not m:
            return

        # Safe to delete — use delete-to-paren logic
        self._delete_to_paren()

    def _delete_template_comments(self) -> None:
        """Remove trailing ``// Templated`` and ``// Implicit .*`` comments."""
        text = self._buf.buffer_string()
        result = _TEMPLATE_COMMENT_RE.sub("", text)
        if result != text:
            pos = self._buf.point()
            self._buf.goto_char(0)
            self._buf.delete_region(0, len(self._buf._text))
            self._buf.insert(result)
            self._buf.goto_char(min(pos, self._buf.point_max()))

    # ------------------------------------------------------------------
    # Paren-matching helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _backward_open_paren(pos: int, text: str = None, buf=None) -> int | None:
        """Find the ``(`` that opens the paren group containing *pos*."""
        # We need to work on the buffer
        return None  # overridden below

    def _backward_open_paren(self, pos: int) -> int | None:
        """Find the matching ``(`` scanning backward from *pos*."""
        text = self._buf.buffer_string()
        depth = 1
        i = pos - 1
        while i >= 0 and depth > 0:
            ch = text[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
            i -= 1
        if depth == 0:
            return i + 1  # position of the (
        return None

    def _forward_sexp(self, open_pos: int) -> int | None:
        """From an opening ``(``, find the matching ``)``.

        Returns position *after* the closing paren.
        """
        text = self._buf.buffer_string()
        n = len(text)
        depth = 0
        i = open_pos
        while i < n:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
