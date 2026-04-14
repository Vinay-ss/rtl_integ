"""InstParser — read instantiation information from a VerilogBuffer.

Ported from ``verilog-read-inst-module``, ``verilog-read-inst-name``,
``verilog-read-inst-pins``, ``verilog-read-inst-param-value``, and
``verilog-read-module-name`` (around lines 9177–9263 and 9919–9932
of ``verilog-mode.el``).

Note: This module uses direct text parsing with Python ``re`` instead of
the buffer's Emacs-regex search methods, because the patterns here are
inherently Python regex patterns.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig

# Regex for identifiers (including backtick for `defines)
_IDENT_RE = re.compile(r"[a-zA-Z0-9`_$]+")

# Module/interface/program/package declaration keywords
_DEFUN_KEYWORDS = {
    "macromodule", "connectmodule", "module", "class",
    "program", "interface", "package", "primitive", "config",
}

_DEFUN_RE = re.compile(
    r"\b(?:macromodule|connectmodule|module|class|program"
    r"|interface|package|primitive|config)\b"
)


class InstParser:
    """Extract instantiation information from a :class:`VerilogBuffer`."""

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self._buf = buf
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_module_name(self) -> Optional[str]:
        """From point inside a module, return this module's name.

        Port of ``verilog-read-module-name``.  Expects point to be
        after the module's ``(`` or ``;``.
        """
        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Search backward for ( or ;
        paren_pos = self._rfind_any(text, "([;", pt)
        if paren_pos is None:
            return None

        # Search backward for module/interface/program keyword
        last_kw = None
        for m in _DEFUN_RE.finditer(text, 0, paren_pos):
            last_kw = m

        if last_kw is None:
            return None

        # Read the identifier after the keyword
        end = last_kw.end()
        # Skip whitespace
        while end < len(text) and text[end] in " \t\n\r\f":
            end += 1
        m = _IDENT_RE.match(text, end)
        if m:
            name = m.group(0)
            return self._detick(name) if name else None
        return None

    def read_inst_module(self) -> Optional[str]:
        """From point inside an instantiation (e.g. at ``/*AUTOINST*/``),
        return the instantiated module name.

        Port of ``verilog-read-inst-module``.
        """
        text = self._buf.buffer_string()
        pt = self._buf.point()
        return self._read_inst_module_from_text(text, pt)

    def read_inst_name(self) -> Optional[str]:
        """From point inside an instantiation, return the instance name.

        Port of ``verilog-read-inst-name``.
        """
        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Find the opening ( of the instantiation
        open_paren = self._find_open_paren_backward(text, pt)
        if open_paren is None:
            return None

        pre = text[:open_paren].rstrip()
        idx = len(pre) - 1
        # Skip trailing whitespace and line comments
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return None

        # Skip optional array range [...] (arrayed instances)
        if idx >= 0 and pre[idx] == "]":
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx < 0:
                return None

        # Read instance name (identifier) backward
        inst_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        return pre[idx + 1:inst_end] or None

    def read_inst_pins(self) -> list[tuple[str, str]]:
        """From point at an instantiation's pin list (e.g. at
        ``/*AUTOINST*/``), return ``[(formal, actual), ...]``.

        Port of ``verilog-read-inst-pins``.
        """
        text = self._buf.buffer_string()
        end_mod_point = self._buf.point()

        # Find the opening ( of the instantiation
        open_paren = self._find_open_paren_backward(text, end_mod_point)
        if open_paren is None:
            return []

        # Scan from after ( to the AUTOINST marker
        search_text = text[open_paren + 1:end_mod_point]
        pins: list[tuple[str, str]] = []

        # Match .portname patterns
        pin_re = re.compile(r"\.([^(,) \t\n\f]*)\s*")
        pos = 0
        while pos < len(search_text):
            m = pin_re.search(search_text, pos)
            if m is None:
                break

            pin_name = m.group(1)
            pos = m.end()

            if not pin_name:
                continue

            # Skip pins inside // line comments (port of the
            # ``unless (verilog-inside-comment-or-string-p)`` guard)
            match_pos = m.start()
            line_start = search_text.rfind("\n", 0, match_pos)
            line_start = 0 if line_start < 0 else line_start + 1
            line_before = search_text[line_start:match_pos]
            if "//" in line_before:
                continue

            # Check if followed by (actual)
            actual = ""
            if pos < len(search_text) and search_text[pos] == "(":
                # Find matching )
                depth = 1
                i = pos + 1
                while i < len(search_text) and depth > 0:
                    if search_text[i] == "(":
                        depth += 1
                    elif search_text[i] == ")":
                        depth -= 1
                    i += 1
                if depth == 0:
                    actual = search_text[pos + 1:i - 1]
                    actual = re.sub(r"\s+", "", actual)
                    pos = i

            pins.append((pin_name, actual))

        return pins

    def read_inst_param_value(self) -> list[tuple[str, str]]:
        """From point inside an instantiation, return
        ``[(param, value), ...]`` from the ``#(...)`` parameter list.

        Port of ``verilog-read-inst-param-value``.
        """
        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Find opening ( of instantiation
        open_paren = self._find_open_paren_backward(text, pt)
        if open_paren is None:
            return []

        # Find the instance name before (
        pre = text[:open_paren].rstrip()
        idx = len(pre) - 1
        while idx >= 0 and pre[idx] in " \t\n\r\f":
            idx -= 1
        if idx < 0:
            return []

        # Skip optional array range [...] (arrayed instances)
        if idx >= 0 and pre[idx] == "]":
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            while idx >= 0 and pre[idx] in " \t\n\r\f":
                idx -= 1
            if idx < 0:
                return []

        # Skip instance name
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1

        # Skip whitespace and comments (AUTOINSTPARAM may leave
        # ``// Templated`` on the closing line of the param block)
        idx = self._skip_ws_and_comments_backward(pre, idx)

        # Check for ) of parameter block
        if idx < 0 or pre[idx] != ")":
            return []

        # Find matching ( for the #(...) block
        param_close = idx
        depth = 1
        idx -= 1
        while idx >= 0 and depth > 0:
            if pre[idx] == ")":
                depth += 1
            elif pre[idx] == "(":
                depth -= 1
            idx -= 1
        if depth != 0:
            return []
        param_open = idx + 1

        param_text = pre[param_open + 1:param_close]
        params: list[tuple[str, str]] = []

        # Parse .param(value) patterns
        param_re = re.compile(r"\.([a-zA-Z0-9`_$]+)\s*\(")
        pos = 0
        while pos < len(param_text):
            m = param_re.search(param_text, pos)
            if m is None:
                break

            param_name = m.group(1)
            paren_start = m.end()

            # Find matching )
            depth = 1
            i = paren_start
            while i < len(param_text) and depth > 0:
                if param_text[i] == "(":
                    depth += 1
                elif param_text[i] == ")":
                    depth -= 1
                i += 1

            if depth == 0:
                param_value = param_text[paren_start:i - 1].strip()
                param_value = re.sub(r"\s+", "", param_value)
                params.append((param_name, param_value))

            pos = i

        return params

    # ------------------------------------------------------------------
    # Internal: text-based module name resolution
    # ------------------------------------------------------------------

    def _read_inst_module_from_text(self, text: str, pos: int) -> Optional[str]:
        """Find the instantiated module name by parsing backward from *pos*.

        Pattern: ``ModuleName [#(...)] InstanceName (``
        """
        # Find the opening ( of the instantiation
        open_paren = self._find_open_paren_backward(text, pos)
        if open_paren is None:
            return None

        pre = text[:open_paren].rstrip()
        idx = len(pre) - 1

        # Skip trailing whitespace and line comments backward
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return None

        # Skip optional array range [...] (arrayed instances)
        if idx >= 0 and pre[idx] == "]":
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx < 0:
                return None

        # Read instance name backward (identifier)
        inst_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        inst_name = pre[idx + 1:inst_end]

        # Skip whitespace and comments
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return inst_name  # Only one identifier — it's the module

        # Check for #(...) parameter block
        if pre[idx] == ")":
            # Skip backward over #(...)
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == ")":
                    depth += 1
                elif pre[idx] == "(":
                    depth -= 1
                idx -= 1
            # Skip # character
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx >= 0 and pre[idx] == "#":
                idx -= 1
            # Skip whitespace
            idx = self._skip_ws_and_comments_backward(pre, idx)

        # Check for gate primitive #N syntax
        elif pre[idx] == "#":
            idx -= 1
            idx = self._skip_ws_and_comments_backward(pre, idx)

        # Now read the module name
        mod_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        mod_name = pre[idx + 1:mod_end]

        return mod_name if mod_name else None

    # ------------------------------------------------------------------
    # Text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _skip_ws_and_comments_backward(text: str, idx: int) -> int:
        """Skip whitespace and line comments when reading backward.

        Handles both full-line comments (``// comment``) and inline
        comments (``code // comment``).  When the current position is
        inside a ``//`` comment, skip back to just before the ``//``.
        """
        while idx >= 0:
            if text[idx] in " \t\r\f":
                idx -= 1
            elif text[idx] == "\n":
                idx -= 1  # Skip the newline
            else:
                # Check if current position is inside a // comment
                line_start = text.rfind("\n", 0, idx + 1)
                line_start = 0 if line_start < 0 else line_start + 1
                line_before = text[line_start:idx + 1]
                comment_pos = line_before.find("//")
                if comment_pos >= 0:
                    # We're inside a comment — skip back to before the //
                    idx = line_start + comment_pos - 1
                    continue
                break
        return idx

    @staticmethod
    def _find_open_paren_backward(text: str, pos: int) -> Optional[int]:
        """Find the matching ``(`` scanning backward from *pos*.

        Parentheses inside ``//`` line comments are ignored.
        """
        depth = 1
        i = pos - 1
        while i >= 0 and depth > 0:
            ch = text[i]
            if ch in ("(", ")"):
                # Check if this paren is inside a // line comment
                line_start = text.rfind("\n", 0, i)
                line_start = 0 if line_start < 0 else line_start + 1
                line_before = text[line_start:i]
                if "//" in line_before:
                    i -= 1
                    continue
                if ch == ")":
                    depth += 1
                else:
                    depth -= 1
            i -= 1
        return (i + 1) if depth == 0 else None

    @staticmethod
    def _rfind_any(text: str, chars: str, end: int) -> Optional[int]:
        """Find the last occurrence of any char in *chars* before *end*."""
        best = -1
        for ch in chars:
            pos = text.rfind(ch, 0, end)
            if pos > best:
                best = pos
        return best if best >= 0 else None

    def _detick(self, name: str) -> str:
        """Substitute ``\\`DEFINE`` references in *name* with their value."""
        if "`" not in name:
            return name
        m = re.match(r"^`(\w+)$", name)
        if m and m.group(1) in self._config.defines:
            return self._config.defines[m.group(1)]
        return name
