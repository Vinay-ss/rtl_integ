"""ModuleDatabase — find and cache module definitions.

Ported from ``verilog-modi-lookup`` and ``verilog-module-inside-filename-p``
(around lines 10727–11024 of ``verilog-mode.el``).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Optional

from ..signal import ModDecls, Modi, SubDecls

if TYPE_CHECKING:
    from ..config import VerilogConfig

# Regex for module/interface/program/connectmodule keyword
_MODULE_RE = re.compile(
    r"\b(connectmodule|module|interface|program)\b"
)

# Module end keywords
_END_MODULE_RE = re.compile(
    r"\b(endconnectmodule|endmodule|endinterface|endprogram)\b"
)


class ModuleDatabase:
    """Find modules by name, searching library paths.  Caches results
    by ``(filepath, mtime)``."""

    def __init__(self, config: "VerilogConfig", current_file: str) -> None:
        self._config = config
        self._current_file = os.path.abspath(current_file)
        # Cache: module_name -> Modi
        self._modi_cache: dict[str, Optional[Modi]] = {}
        # Cache: (filepath, mtime) -> ModDecls
        self._decls_cache: dict[tuple[str, float], ModDecls] = {}
        # Cache: (filepath, mtime) -> SubDecls
        self._sub_decls_cache: dict[tuple[str, float], SubDecls] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(
        self,
        module_name: str,
        ignore_error: bool = False,
    ) -> Optional[Modi]:
        """Find a module by name, searching library paths.

        Caches results.  Returns ``None`` if not found and
        *ignore_error* is ``True``; otherwise raises ``RuntimeError``.
        """
        if module_name in self._modi_cache:
            return self._modi_cache[module_name]

        # Substitute defines in module name
        realname = self._detick(module_name)

        # Build search list
        from .resolver import LibraryResolver

        resolver = LibraryResolver(self._config, self._current_file)
        filenames = resolver.library_filenames(realname, check_exists=True)

        # Also try the current file itself
        if self._current_file not in filenames:
            filenames.insert(0, self._current_file)

        modi: Optional[Modi] = None
        for fn in filenames:
            modi = self.module_inside_filename(realname, fn)
            if modi is not None:
                break

        if modi is None and not ignore_error:
            raise RuntimeError(
                f"Can't locate '{module_name}' module definition. "
                f"Check verilog-library-directories. "
                f"Searched in: {filenames}"
            )

        self._modi_cache[module_name] = modi
        return modi

    def get_decls(self, modi: Modi) -> ModDecls:
        """Return (cached) :class:`ModDecls` for a found module."""
        cache_key = self._cache_key_with_point(modi.filepath, modi.point)
        if cache_key in self._decls_cache:
            return self._decls_cache[cache_key]

        from ..buffer import VerilogBuffer
        from ..parser.decl_parser import DeclParser

        buf = VerilogBuffer.from_file(modi.filepath)
        buf.goto_char(modi.point)

        parser = DeclParser(buf, self._config)
        decls = parser.parse()

        self._decls_cache[cache_key] = decls
        return decls

    def get_sub_decls(self, modi: Modi) -> SubDecls:
        """Return (cached) :class:`SubDecls` for a found module."""
        cache_key = self._cache_key_with_point(modi.filepath, modi.point)
        if cache_key in self._sub_decls_cache:
            return self._sub_decls_cache[cache_key]

        # SubDecls parsing is a later phase — return empty for now
        sub = SubDecls()
        self._sub_decls_cache[cache_key] = sub
        return sub

    def module_inside_filename(
        self,
        module_name: str,
        filepath: str,
    ) -> Optional[Modi]:
        """Scan *filepath* for a module/interface/program named
        *module_name*.

        Port of ``verilog-module-inside-filename-p``.
        """
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            return None

        from ..buffer import VerilogBuffer

        buf = VerilogBuffer.from_file(filepath)
        text = buf.buffer_string()

        # Search for all module/interface/program declarations
        for m in _MODULE_RE.finditer(text):
            mod_type = m.group(1)
            start = m.end()

            # Read the module name after the keyword
            # Skip whitespace
            name_m = re.match(r"\s+([a-zA-Z0-9`_$]+)", text[start:])
            if not name_m:
                continue

            found_name = name_m.group(1)

            if found_name == module_name:
                # Find the point after the opening ( or ; of the module
                rest = text[start + name_m.end():]
                paren_m = re.search(r"[;(]", rest)
                if paren_m:
                    point = start + name_m.end() + paren_m.end()
                else:
                    point = start + name_m.end()

                return Modi(
                    name=module_name,
                    filepath=filepath,
                    point=point,
                    type=mod_type,
                )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cache_key(self, filepath: str) -> tuple[str, float]:
        """Build a cache key from filepath + mtime."""
        filepath = os.path.abspath(filepath)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0.0
        return (filepath, mtime)

    def _cache_key_with_point(self, filepath: str, point: int) -> tuple[str, float, int]:
        """Build a cache key from filepath + mtime + point.

        Used for decls/sub_decls caching where multiple modules may
        exist in the same file.
        """
        filepath = os.path.abspath(filepath)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0.0
        return (filepath, mtime, point)

    def _detick(self, name: str) -> str:
        """Substitute `DEFINE references in module names."""
        if "`" not in name:
            return name
        m = re.match(r"^`(\w+)$", name)
        if m and m.group(1) in self._config.defines:
            return self._config.defines[m.group(1)]
        return name
