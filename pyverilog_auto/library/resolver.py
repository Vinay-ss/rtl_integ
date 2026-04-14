"""LibraryResolver — locate Verilog module files.

Ported from ``verilog-library-filenames`` and ``verilog-expand-dirnames``
(around lines 10811–10894 of ``verilog-mode.el``).
"""

from __future__ import annotations

import glob
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import VerilogConfig


class LibraryResolver:
    """Resolve module names to file paths using library search paths."""

    def __init__(self, config: "VerilogConfig", current_file: str) -> None:
        self._config = config
        self._current_file = os.path.abspath(current_file)
        self._current_dir = os.path.dirname(self._current_file)
        # Cache: (module_name, check_exists) -> list[str]
        self._cache: dict[tuple[str, bool], list[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def library_filenames(
        self,
        module_name: str,
        check_exists: bool = True,
    ) -> list[str]:
        """Return candidate file paths for *module_name*, in search order.

        Searches ``library_directories`` for files matching
        ``module_name + extension``.  Also checks ``library_files``
        directly.
        """
        cache_key = (module_name, check_exists)
        if cache_key in self._cache:
            return self._cache[cache_key]

        outlist: list[str] = []

        # Search in library_directories
        chkdirs = self.expand_dirnames(self._config.library_directories)
        extensions = self._config.library_extensions if check_exists else [""]

        for d in chkdirs:
            # Resolve relative to current file's directory
            if not os.path.isabs(d):
                d = os.path.join(self._current_dir, d)
            d = os.path.normpath(d)

            for ext in extensions:
                fn = os.path.join(d, module_name + ext)
                fn = os.path.normpath(fn)
                if not check_exists or os.path.isfile(fn):
                    if fn not in outlist:
                        outlist.append(fn)

        # Also check library_files directly
        for lf in self._config.library_files:
            if not os.path.isabs(lf):
                lf = os.path.join(self._current_dir, lf)
            lf = os.path.normpath(lf)
            if not check_exists or os.path.isfile(lf):
                if lf not in outlist:
                    outlist.append(lf)

        self._cache[cache_key] = outlist
        return outlist

    def expand_dirnames(self, dirnames: list[str]) -> list[str]:
        """Expand directory patterns (globs, env vars) to actual paths."""
        if not dirnames:
            return ["."]

        result: list[str] = []

        for dirname in dirnames:
            # Substitute environment variables
            dirname = os.path.expandvars(dirname)

            # Check for wildcard characters
            if "*" in dirname or "?" in dirname:
                # Use glob to expand wildcards
                if not os.path.isabs(dirname):
                    dirname = os.path.join(self._current_dir, dirname)
                expanded = glob.glob(dirname)
                for p in expanded:
                    if os.path.isdir(p):
                        normed = os.path.normpath(p)
                        if normed not in result:
                            result.append(normed)
            else:
                # No wildcards — check if directory exists
                d = dirname
                if not os.path.isabs(d):
                    d = os.path.join(self._current_dir, d)
                d = os.path.normpath(d)
                if os.path.isdir(d):
                    if d not in result:
                        result.append(d)
                else:
                    # Still add it (may be relative, resolved later)
                    normed = os.path.normpath(dirname)
                    if normed not in result:
                        result.append(normed)

        return result
