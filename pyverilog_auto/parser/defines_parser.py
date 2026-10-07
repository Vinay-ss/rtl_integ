"""DefinesParser — read `define, `undef, `include and parameter constants.

Ported from ``verilog-read-defines`` and ``verilog-read-includes``
(around lines 10338–10482 of ``verilog-mode.el``).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


class DefinesParser:
    r"""Extract ``\`define`` / ``\`undef`` / ``parameter`` values from Verilog sources."""

    def __init__(self, config: "VerilogConfig") -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(
        self,
        filepath: str,
        recurse: bool = True,
        visited: Optional[set[str]] = None,
    ) -> dict[str, str]:
        r"""Scan *filepath* for ``\`define`` and ``\`include``.

        Return a defines dict.  Recursively processes ``\`include``
        files when *recurse* is ``True``.
        """
        if visited is None:
            visited = set()

        filepath = os.path.abspath(filepath)
        if filepath in visited:
            return {}
        visited.add(filepath)

        if not os.path.isfile(filepath):
            return {}

        from ..buffer import VerilogBuffer

        buf = VerilogBuffer.from_file(filepath)
        text = buf.buffer_string()
        defines: dict[str, str] = {}

        # --- recurse phase: process `include ---
        if recurse:
            for m in re.finditer(
                r"^[ \t]*`include\s+\"?([^\"\s]+)\"?", text, re.MULTILINE
            ):
                inc_name = m.group(1)
                inc_path = self._resolve_include(inc_name, filepath)
                if inc_path:
                    sub = self.parse_file(inc_path, recurse=True, visited=visited)
                    defines.update(sub)

        # --- defines phase ---
        buf_defines = self.parse_buffer(buf)
        defines.update(buf_defines)

        return defines

    def parse_buffer(
        self, buf: "VerilogBuffer", include_macros: bool = True
    ) -> dict[str, str]:
        r"""Scan *buf* for ``\`define NAME VALUE`` lines.  Return ``{name: value}``.

        When *include_macros* is ``False``, ``\`define`` / ``\`undef``
        directives are skipped and only ``parameter`` / ``localparam``
        values are collected.  This matches the Emacs behaviour where
        ``verilog-read-defines`` is only called when
        ``verilog-auto-read-includes`` is set.

        Also handles ``\`undef``, ``parameter``, and ``localparam``.
        """
        text = buf.buffer_string()
        defines: dict[str, str] = {}

        if include_macros:
            # Track ifdef/ifndef conditional compilation state
            cond_stack: list[bool] = []  # True = active region
            cond_active = True

            for line in text.splitlines():
                stripped = line.strip()

                # --- Conditional compilation directives ---
                m_ifdef = re.match(r"^`ifdef\s+(\w+)", stripped)
                m_ifndef = re.match(r"^`ifndef\s+(\w+)", stripped)
                m_elsif = re.match(r"^`elsif\s+(\w+)", stripped)

                if m_ifdef:
                    cond_stack.append(cond_active)
                    cond_active = cond_active and (m_ifdef.group(1) in defines)
                    continue
                if m_ifndef:
                    cond_stack.append(cond_active)
                    cond_active = cond_active and (m_ifndef.group(1) not in defines)
                    continue
                if m_elsif:
                    # Restore parent active state then re-check
                    parent_active = cond_stack[-1] if cond_stack else True
                    cond_active = parent_active and (m_elsif.group(1) in defines)
                    continue
                if stripped == "`else":
                    parent_active = cond_stack[-1] if cond_stack else True
                    cond_active = parent_active and not cond_active
                    continue
                if stripped == "`endif":
                    if cond_stack:
                        cond_active = cond_stack.pop()
                    continue

                if not cond_active:
                    continue

                # --- `define ---
                m = re.match(
                    r"^`define\s+([a-zA-Z0-9_$]+)\s*(.*?)$", stripped
                )
                if m:
                    defname = m.group(1)
                    defvalue = m.group(2).strip()
                    # Strip trailing comments
                    defvalue = re.sub(r"\s*//.*$", "", defvalue)
                    defvalue = re.sub(r"\s*/\*.*$", "", defvalue)
                    defines[defname] = defvalue
                    continue

                # --- `undef ---
                m = re.match(r"^`undef\s+([a-zA-Z0-9_$]+)", stripped)
                if m:
                    defines.pop(m.group(1), None)
                    continue

        # --- parameter / localparam (may span multiple lines) ---
        # Also extract enum tags from synopsys/auto enum comments,
        # mirroring the "Hack: Read parameters" section of verilog-read-defines.
        # Strip line comments but preserve block comments for enum detection.
        cleaned_for_params = re.sub(r'//[^\n]*', '', text)
        cleaned_for_params = re.sub(r'/\*.*?\*/', '', cleaned_for_params, flags=re.DOTALL)
        for m in re.finditer(
            r'\b(?:parameter|localparam)\s*(?:\[[^\]]*\])?\s*([^;]+);',
            cleaned_for_params,
            re.DOTALL,
        ):
            rest = m.group(1)
            for pm in re.finditer(
                r'([a-zA-Z_][a-zA-Z0-9_$]*)\s*=\s*([^,;]+)', rest
            ):
                pname = pm.group(1).strip()
                pvalue = pm.group(2).strip()
                defines[pname] = pvalue

        # Now scan the original text (with comments) to find enum tags
        # on parameter/localparam declarations and associate constants.
        # Port of the enum-reading part of verilog-read-defines.
        # The elisp scans each parameter/localparam statement for an
        # adjacent ``synopsys enum`` / ``auto enum`` comment, then
        # records every name = value pair in the same statement.
        _PARAM_HEAD_RE = re.compile(
            r'^\s*(?:parameter|localparam)\s*(?:\[[^\]]*\])?\s*', re.MULTILINE
        )
        _ENUM_TAG_RE = re.compile(
            r'(?:auto|synopsys)\s+enum\s+([a-zA-Z0-9_]+)'
        )
        for pm in _PARAM_HEAD_RE.finditer(text):
            # Find the semicolon that ends this statement
            semi_pos = text.find(';', pm.start())
            if semi_pos < 0:
                continue
            stmt = text[pm.end():semi_pos]
            # Look for an enum tag in the statement (including comments)
            em = _ENUM_TAG_RE.search(stmt)
            if not em:
                # Also check the matched portion itself (enum before names)
                em = _ENUM_TAG_RE.search(text[pm.start():pm.end()])
            if not em:
                continue
            enumname = em.group(1)
            # Strip comments from the statement to find constant names
            stmt_clean = re.sub(r'//[^\n]*', '', stmt)
            stmt_clean = re.sub(r'/\*.*?\*/', '', stmt_clean, flags=re.DOTALL)
            for cm in re.finditer(
                r'([a-zA-Z_][a-zA-Z0-9_$]*)\s*=', stmt_clean
            ):
                cname = cm.group(1).strip()
                if cname not in ('parameter', 'localparam'):
                    if enumname not in self._config.enum_assocs:
                        self._config.enum_assocs[enumname] = []
                    if cname not in self._config.enum_assocs[enumname]:
                        self._config.enum_assocs[enumname].append(cname)

        return defines

    def substitute(self, text: str, defines: dict[str, str]) -> str:
        r"""Substitute ``\`NAME`` with its define value in *text*."""
        if not defines:
            return text
        # Build a regex that matches `NAME for all defined names
        pattern = re.compile(
            r"`(" + "|".join(re.escape(k) for k in defines) + r")\b"
        )

        def _replace(m: re.Match) -> str:
            return defines.get(m.group(1), m.group(0))

        return pattern.sub(_replace, text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_include(self, inc_name: str, current_file: str) -> Optional[str]:
        """Resolve an include filename relative to *current_file* and
        the library directories."""
        # Strip quotes if present
        inc_name = inc_name.strip().strip('"').strip("'")

        # Try relative to current file first
        base_dir = os.path.dirname(os.path.abspath(current_file))
        candidate = os.path.join(base_dir, inc_name)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

        # Try library directories
        for d in self._config.library_directories:
            candidate = os.path.join(base_dir, d, inc_name)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        return None
