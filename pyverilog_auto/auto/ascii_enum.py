"""AutoAsciiEnum — expand ``/*AUTOASCIIENUM*/`` markers.

Ported from ``verilog-auto-ascii-enum`` (lines 14585–14739) of
``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import Signal, signals_matching_regexp

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoAsciiEnum:
    """Expand ``/*AUTOASCIIENUM("signal", "ascii_reg", "prefix")*/``.

    Generates an ``always`` block that decodes an enumerated signal
    into an ASCII string register for debug display.
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
        """Insert ASCII enum decoder after the marker.

        Port of ``verilog-auto-ascii-enum``.
        """
        from .wire import AutoWire

        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Read parameters from the marker
        params = self._read_auto_params(text, pt)
        if not params or len(params) < 2:
            return

        undecode_name = params[0]
        ascii_name = params[1]
        elim_regexp = params[2] if len(params) > 2 and params[2] else None
        one_hot_flag = params[3] if len(params) > 3 else None

        indent_pt = self._current_indentation(text, pt)

        # Get module declarations
        helper = AutoWire(self._buf, self._config, self._db)
        moddecls = helper._get_current_moddecls()

        # Find the undecode signal
        # vars first, matching elisp verilog-decls-get-iovars order
        # so that a reg with an enum tag is found before an output
        # without one when a signal appears in both lists.
        all_sigs = (
            moddecls.vars + moddecls.outputs + moddecls.inouts
            + moddecls.inputs
        )
        undecode_sig = self._find_sig(undecode_name, all_sigs)
        if undecode_sig is None:
            return

        undecode_enum = undecode_sig.enum
        if not undecode_enum:
            return

        # Find all constants matching the enum tag.
        # First check the parsed declarations (traditional approach).
        sig_list_consts = moddecls.consts + moddecls.gparams
        enum_sigs = [s for s in sig_list_consts
                     if s.enum and s.enum == undecode_enum]
        # Also check config.enum_assocs (the "New scheme" from elisp's
        # verilog-signals-matching-enum / venum-* variables).  This picks
        # up constants defined in `include files that were scanned by
        # DefinesParser.
        known_names = {s.name for s in enum_sigs}
        # Reverse the enum_assocs list to match the elisp ordering.
        # In verilog-read-defines, add-to-list prepends each name,
        # reversing file order; verilog-signals-matching-enum then
        # iterates and pushes to a result that is finally nreversed,
        # ending up with the reversed-file-order.
        for extra_name in reversed(
            self._config.enum_assocs.get(undecode_enum, [])
        ):
            if extra_name not in known_names:
                enum_sigs.append(Signal(name=extra_name))
                known_names.add(extra_name)
        if not enum_sigs:
            return

        # Check for one-hot encoding
        one_hot = False
        if one_hot_flag and "onehot" in one_hot_flag:
            one_hot = True
        elif enum_sigs:
            # Auto-detect: if number of enum values equals the signal width
            # and enum values don't have matching widths
            sig_width = self._compute_width_str(undecode_sig)
            if sig_width and sig_width.isdigit():
                if str(len(enum_sigs)) == sig_width:
                    enum_width = self._compute_width_str(enum_sigs[0])
                    if not enum_width or enum_width != sig_width:
                        one_hot = True

        # Compute formatting widths
        enum_chars = 0
        ascii_chars = 0
        for sig in enum_sigs:
            enum_chars = max(enum_chars, len(sig.name))
            ascii_chars = max(ascii_chars, len(self._enum_ascii(sig.name, elim_regexp)))

        # Compute ascii register width in bits
        ascii_bits = ascii_chars * 8

        # Build the output
        lines: list[str] = []
        helper._forward_or_insert_line()

        lines.append(
            " " * indent_pt
            + "// Beginning of automatic ASCII enum decoding\n"
        )

        # Register declaration
        lines.append(
            " " * indent_pt
            + f"reg [{ascii_bits - 1}:0]"
        )
        # Pad for name alignment
        name_col = max(24, indent_pt + 16)
        decl_line = " " * indent_pt + f"reg [{ascii_bits - 1}:0]"
        if len(decl_line) < name_col:
            decl_line += " " * (name_col - len(decl_line))
        else:
            decl_line += " "
        decl_line += f"{ascii_name};"
        decl_line += f" // Decode of {undecode_name}\n"
        lines[-1] = decl_line

        # Always block
        lines.append(
            " " * indent_pt
            + f"always @({undecode_name}) begin\n"
        )

        case_indent = indent_pt + self._config.indent_level
        lines.append(
            " " * case_indent
            + f"case ({{" + undecode_name + "})\n"
        )

        body_indent = case_indent + self._config.case_indent

        # Format string for case entries
        for sig in enum_sigs:
            ascii_str = self._enum_ascii(sig.name, elim_regexp)

            if one_hot:
                label = f"({len(enum_sigs)}'b1<<{sig.name}):"
            else:
                label = f"{sig.name}:"

            padded_label = label
            target_label_width = (9 if one_hot else 1) + max(8, enum_chars)
            if len(padded_label) < target_label_width:
                padded_label += " " * (target_label_width - len(padded_label))

            # Pad ASCII string to consistent width
            padded_ascii = f'"{ascii_str:<{ascii_chars}s}"'

            lines.append(
                " " * body_indent
                + f"{padded_label} {ascii_name} = {padded_ascii};\n"
            )

        # Default case
        err_name = "%Error"[:ascii_chars]
        # Pad error name to ascii_chars like other entries
        err_name_padded = f"{err_name:<{ascii_chars}s}"
        default_label = "default:"
        target_label_width = (9 if one_hot else 1) + max(8, enum_chars)
        if len(default_label) < target_label_width:
            default_label += " " * (target_label_width - len(default_label))

        lines.append(
            " " * body_indent
            + f'{default_label} {ascii_name} = "{err_name_padded}";\n'
        )

        lines.append(" " * case_indent + "endcase\n")
        lines.append(" " * indent_pt + "end\n")
        lines.append(" " * indent_pt + "// End of automatics\n")

        self._buf.insert("".join(lines))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_auto_params(text: str, pt: int) -> list[str] | None:
        """Read parameters from ``/*AUTOASCIIENUM("a","b","c")*/``."""
        marker_start = text.rfind("/*AUTOASCIIENUM", 0, pt)
        if marker_start < 0:
            return None
        marker_end = text.find("*/", marker_start)
        if marker_end < 0:
            return None
        marker = text[marker_start:marker_end + 2]
        # Extract all quoted parameters
        params = re.findall(r'"([^"]*)"', marker)
        return params if params else None

    @staticmethod
    def _enum_ascii(name: str, elim_regexp: str | None) -> str:
        """Convert an enum constant name to its ASCII display string.

        Port of ``verilog-enum-ascii``.
        """
        result = name.lower()
        if elim_regexp:
            result = re.sub(elim_regexp, "", result, flags=re.IGNORECASE)
        return result

    @staticmethod
    def _find_sig(name: str, sigs: list[Signal]) -> Signal | None:
        for s in sigs:
            if s.name == name:
                return s
        return None

    @staticmethod
    def _compute_width_str(sig: Signal) -> str | None:
        """Compute width as a string."""
        w = sig.width_expression()
        return w

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
