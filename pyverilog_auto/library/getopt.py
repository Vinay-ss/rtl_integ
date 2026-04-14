"""VerilogGetopt — parse Verilog-style command-line flags.

Ported from ``verilog-getopt`` (around lines 10518–10624 of
``verilog-mode.el``).
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import VerilogConfig


class VerilogGetopt:
    """Parse Verilog compiler flags (``-I``, ``-y``, ``+libext+``, etc.)
    into :class:`VerilogConfig` overrides."""

    def __init__(self, config: "VerilogConfig") -> None:
        self._config = deepcopy(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_flags(self, flags: list[str]) -> "VerilogConfig":
        """Parse Verilog-style flags and update the internal config.

        Supported flags::

            -I DIR / +incdir+DIR   Add to library_directories
            -y DIR                 Add to library_directories
            -v FILE                Add to library_files
            -f FILE                Read flags from FILE (recursive)
            +libext+.v+.sv         Set library_extensions
            +define+NAME=VAL       Add to defines
            -Dname=val             Add to defines (alternate syntax)

        Returns the updated :class:`VerilogConfig`.
        """
        # Tokenize: split each flag string on whitespace
        tokens: list[str] = []
        for flag in flags:
            tokens.extend(flag.split())

        next_param: str | None = None

        for token in tokens:
            if next_param is not None:
                self._handle_next_param(next_param, token)
                next_param = None
                continue

            # Two-argument flags
            if token in ("-f", "-F", "-v", "-y"):
                next_param = token
                continue

            # +libext+.v+.sv
            m = re.match(r"^\+libext\+(.+)", token)
            if m:
                exts = m.group(1).split("+")
                for ext in exts:
                    if ext and ext not in self._config.library_extensions:
                        self._config.library_extensions.append(ext)
                continue

            # +define+NAME=VAL or +define+NAME
            m = re.match(r"^\+define\+([^+=]+)[+=]?(.*)", token)
            if m:
                self._config.defines[m.group(1)] = m.group(2)
                continue

            # -DNAME=VAL or -DNAME
            m = re.match(r"^-D([^+=]+)[+=]?(.*)", token)
            if m:
                self._config.defines[m.group(1)] = m.group(2)
                continue

            # +incdir+DIR
            m = re.match(r"^\+incdir\+(.+)", token)
            if m:
                d = os.path.expandvars(m.group(1))
                if d not in self._config.library_directories:
                    self._config.library_directories.append(d)
                continue

            # -IDIR
            m = re.match(r"^-I(.+)", token)
            if m:
                d = os.path.expandvars(m.group(1))
                if d not in self._config.library_directories:
                    self._config.library_directories.append(d)
                continue

            # +librescan — ignore
            if token == "+librescan":
                continue

            # -U (undefine) — ignore
            if re.match(r"^-U", token):
                continue

            # Plain filename (doesn't start with - or +)
            if re.match(r"^[^-+]", token):
                if token not in self._config.library_files:
                    self._config.library_files.append(token)
                continue

        return self._config

    def parse_flag_file(self, filepath: str, relative_paths: bool = False) -> "VerilogConfig":
        """Read flags from a ``-f`` file (one flag per line, ``#`` / ``//`` comments).

        If *relative_paths* is True (``-F`` mode), ``-y`` and ``-v`` paths
        inside the file are resolved relative to the flag file's directory.
        """
        # Resolve relative to library directories if not absolute
        if not os.path.isabs(filepath):
            for d in self._config.library_directories:
                candidate = os.path.join(d, filepath)
                if os.path.isfile(candidate):
                    filepath = candidate
                    break
            else:
                filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            return self._config

        flag_dir = os.path.dirname(os.path.abspath(filepath))

        lines: list[str] = []
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                # Strip comments
                if "//" in line:
                    line = line[: line.index("//")]
                if "#" in line:
                    line = line[: line.index("#")]
                line = line.strip()
                if line:
                    lines.append(line)

        # For -F mode, resolve relative paths in the flag file
        if relative_paths:
            resolved: list[str] = []
            i = 0
            tokens = []
            for ln in lines:
                tokens.extend(ln.split())
            while i < len(tokens):
                tok = tokens[i]
                if tok in ("-y", "-v", "-f", "-F") and i + 1 < len(tokens):
                    path_val = tokens[i + 1]
                    if not os.path.isabs(path_val):
                        path_val = os.path.join(flag_dir, path_val)
                    resolved.append(tok)
                    resolved.append(path_val)
                    i += 2
                else:
                    resolved.append(tok)
                    i += 1
            return self.parse_flags(resolved)

        return self.parse_flags(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_next_param(self, flag: str, value: str) -> None:
        """Process a two-argument flag (``-f``, ``-F``, ``-v``, ``-y``)."""
        if flag == "-f":
            self.parse_flag_file(value)
        elif flag == "-F":
            # -F is like -f but paths are relative to flag file location
            self.parse_flag_file(value, relative_paths=True)
        elif flag == "-v":
            resolved = self._resolve_path(value)
            if resolved not in self._config.library_files:
                self._config.library_files.append(resolved)
        elif flag == "-y":
            resolved = self._resolve_path(value)
            if resolved not in self._config.library_directories:
                self._config.library_directories.append(resolved)

    def _resolve_path(self, path: str) -> str:
        """Resolve a relative path against library directories."""
        if os.path.isabs(path):
            return path
        for d in self._config.library_directories:
            candidate = os.path.join(d, path)
            if os.path.isdir(candidate) or os.path.isfile(candidate):
                return candidate
        return path
