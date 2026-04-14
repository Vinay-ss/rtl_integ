"""AutoArg — expand AUTOARG markers.

Ported from ``verilog-auto-arg`` (around lines 12092–12160) of
``verilog-mode.el``.

AUTOARG lists module ports as a comma-separated argument list,
grouped by direction (Outputs, Inouts, Inputs).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import Signal, signals_not_in

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoArg:
    """Expand ``/*AUTOARG*/`` markers in a :class:`VerilogBuffer`."""

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
        """Expand ``/*AUTOARG*/`` at current point.

        Point must be positioned right after the ``/*AUTOARG*/`` marker.
        """
        from ..parser.decl_parser import DeclParser

        pt = self._buf.point()
        text = self._buf.buffer_string()

        # Calculate indentation: column after the opening (
        indent_pt = self._find_indent_pt()

        # Parse declarations of the current module
        moddecls = self._get_current_moddecls()

        # Read already-declared pins (between ( and /*AUTOARG*/)
        skip_pins = self._read_arg_pins()
        skip_sigs = [Signal(name=p) for p in skip_pins]

        # Collect ports by direction, using modi-cache order for
        # auto-generated signals (reverse-alpha, like Emacs) and
        # declaration order for explicit signals.
        mod_cache = self._get_modi_cache()
        sections: list[tuple[str, list[Signal]]] = []

        outputs = self._merge_cached(
            moddecls.outputs, mod_cache.get("outputs", []), skip_sigs
        )
        if outputs:
            sections.append(("// Outputs", outputs))

        inouts = self._merge_cached(
            moddecls.inouts, mod_cache.get("inouts", []), skip_sigs
        )
        if inouts:
            sections.append(("// Inouts", inouts))

        inputs = self._merge_cached(
            moddecls.inputs, mod_cache.get("inputs", []), skip_sigs
        )
        if inputs:
            sections.append(("// Inputs", inputs))

        if not sections:
            return

        # Format the port list
        indent = " " * indent_pt
        lines: list[str] = []
        is_single = (self._config.auto_arg_format == "single")

        for sec_idx, (header, sigs) in enumerate(sections):
            if self._config.auto_arg_sort:
                sigs = sorted(sigs, key=lambda s: s.name.lower())

            lines.append(indent + header)

            if is_single:
                # Single format: one port per line, each followed by comma
                # except the very last one
                for i, sig in enumerate(sigs):
                    is_last_in_section = (i == len(sigs) - 1)
                    is_last_overall = is_last_in_section and (sec_idx == len(sections) - 1)
                    if is_last_overall:
                        lines.append(indent + sig.name)
                    else:
                        lines.append(indent + sig.name + ",")
            else:
                # Packed format (default): pack signals on lines up to fill_column
                fill_col = self._config.fill_column
                line = indent
                for i, sig in enumerate(sigs):
                    name = sig.name
                    is_last_in_section = (i == len(sigs) - 1)
                    is_last_overall = is_last_in_section and (sec_idx == len(sections) - 1)

                    if is_last_overall:
                        addition = name
                    else:
                        addition = name + ","

                    if line == indent:
                        line += addition
                    elif len(line) + 1 + len(addition) <= fill_col:
                        line += " " + addition
                    else:
                        lines.append(line)
                        line = indent + addition

                if line != indent:
                    lines.append(line)

        # Join with newlines
        text_block = "\n".join(lines)

        # Insert after /*AUTOARG*/
        self._buf.insert("\n" + text_block + "\n" + indent)

    @staticmethod
    def _merge_cached(
        decl_sigs: list[Signal],
        cached_sigs: list[Signal],
        skip_sigs: list[Signal],
    ) -> list[Signal]:
        """Merge cached (auto-generated) signals with parsed declarations.

        Returns cached signals first (in their cache order, which is
        reverse-alpha like Emacs), followed by explicit declarations
        not in the cache, excluding any signals in *skip_sigs*.

        Port of the Emacs ``verilog-modi-cache-add-*`` + ``verilog-decls-get-*``
        ordering that AUTOARG reads from.
        """
        cached_names = {s.name for s in cached_sigs}
        skip_names = {s.name for s in skip_sigs}

        # Filter cached signals: only keep those that are actually in
        # this module's declarations (handles multi-module files where
        # the cache may have signals from a different module).
        decl_names = {s.name for s in decl_sigs}
        filtered_cached = [s for s in cached_sigs
                           if s.name in decl_names and s.name not in skip_names]

        # Explicit signals not in cache and not in skip
        explicit = [s for s in decl_sigs
                    if s.name not in cached_names and s.name not in skip_names]

        # Dedup by name (ifdef/else may produce duplicate declarations)
        seen: set[str] = set()
        result: list[Signal] = []
        for s in filtered_cached + explicit:
            if s.name not in seen:
                result.append(s)
                seen.add(s.name)
        return result

    def _find_indent_pt(self) -> int:
        """Find the column of the character after the opening ``(``."""
        text = self._buf.buffer_string()
        pos = self._buf.point()

        # Search backward for the opening paren
        depth = 1
        i = pos - 1
        while i >= 0 and depth > 0:
            ch = text[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
            i -= 1
        open_paren = i + 1 if depth == 0 else 0

        # Calculate column of ( + 1
        col = 0
        j = open_paren
        while j > 0 and text[j - 1] != "\n":
            j -= 1
        for k in range(j, open_paren):
            if text[k] == "\t":
                col = (col + 8) & ~7
            else:
                col += 1
        return col + 1

    def _read_arg_pins(self) -> list[str]:
        """Read already-declared pins between ``(`` and ``/*AUTOARG*/``."""
        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Find the opening (
        marker_start = text.rfind("/*AUTOARG", 0, pt)
        if marker_start < 0:
            return []

        # Search backward from marker for (
        i = marker_start - 1
        while i >= 0 and text[i] in " \t\n\r\f":
            i -= 1

        # Find the opening paren
        depth = 0
        while i >= 0:
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                if depth == 0:
                    break
                depth -= 1
            i -= 1

        if i < 0:
            return []

        # Extract text between ( and /*AUTOARG*/
        pre_text = text[i + 1:marker_start]

        # Find pin names (identifiers after commas or at start)
        pins: list[str] = []
        pin_re = re.compile(r"[a-zA-Z_][a-zA-Z0-9_$]*")
        # Remove comments
        pre_text = re.sub(r"//.*$", "", pre_text, flags=re.MULTILINE)
        pre_text = re.sub(r"/\*.*?\*/", "", pre_text, flags=re.DOTALL)
        # Remove keywords and types
        for m in pin_re.finditer(pre_text):
            word = m.group(0)
            if word not in {"input", "output", "inout", "wire", "reg",
                            "logic", "signed", "unsigned"}:
                pins.append(word)

        return pins

    def _current_module_pos(self) -> int:
        """Return the start position of the current module keyword."""
        text = self._buf.buffer_string()
        pos = self._buf.point()
        mod_re = re.compile(r"\b(?:module|interface|program)\b")
        last_match = None
        for m in mod_re.finditer(text, 0, pos):
            last_match = m
        return last_match.start() if last_match else 0

    def _get_modi_cache(self) -> dict[str, list]:
        """Get the modi-cache for the current module."""
        mod_pos = self._current_module_pos()
        return self._buf._modi_cache.get(mod_pos, {})

    def _get_current_moddecls(self):
        """Get ModDecls for the current module being edited."""
        from ..parser.decl_parser import DeclParser
        from ..signal import ModDecls

        with self._buf.save_excursion():
            text = self._buf.buffer_string()
            pos = self._buf.point()
            mod_re = re.compile(r"\b(?:module|interface|program)\b")
            last_match = None
            for m in mod_re.finditer(text, 0, pos):
                last_match = m
            if last_match:
                rest = text[last_match.end():]
                paren_m = re.search(r"[;(]", rest)
                if paren_m:
                    parse_pt = last_match.end() + paren_m.end()
                    self._buf.goto_char(parse_pt)
                    parser = DeclParser(self._buf, self._config)
                    return parser.parse()

        return ModDecls()
