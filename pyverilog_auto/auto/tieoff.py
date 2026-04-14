"""AutoTieoff / AutoUnused — expand ``/*AUTOTIEOFF*/`` and ``/*AUTOUNUSED*/``.

Ported from ``verilog-auto-tieoff`` (lines 14337–14424) and
``verilog-auto-unused`` (lines 14493–14575) of ``verilog-mode.el``.

Note: AutoTieoff was originally in ``io.py`` (Phase 4).  This file
supersedes that implementation with proper sub-decl handling and the
additional AutoUnused class.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import (
    Signal,
    signals_combine_bus,
    signals_not_in,
    signals_not_matching_regexp,
    signals_sort_by_name,
)

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoTieoff:
    """Expand ``/*AUTOTIEOFF*/`` markers.

    Generates tie-off assignments for the module's outputs that have
    no driver (no reg/wire assignment, not driven by sub-instances).

    Port of ``verilog-auto-tieoff``.
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
        from .wire import AutoWire

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOTIEOFF: outputs not driven by vars/assigns/consts/gparams
        # or sub-instance outputs/inouts/interfaced
        existing = (
            moddecls.vars
            + moddecls.assigns
            + moddecls.consts
            + moddecls.gparams
            + modsubdecls.interfaced
            + modsubdecls.outputs
            + modsubdecls.inouts
        )
        sig_list = signals_not_in(moddecls.outputs, existing)

        if self._config.auto_tieoff_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_tieoff_ignore_regexp
            )

        if not sig_list:
            return

        sig_list = signals_sort_by_name(sig_list)

        # Add to modi-cache for AUTOARG ordering
        cache = helper._get_modi_cache()
        # vars cache for tieoff signals
        # (tieoff signals become wire declarations)

        decl_type = self._config.auto_tieoff_declaration or "wire"

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic tieoffs"
            + " (for this module's unterminated outputs)\n"
        )

        for sig in sig_list:
            tieoff = sig.tieoff_value(self._config)

            if decl_type == "assign":
                line = " " * indent_pt + "assign " + sig.name
            else:
                # Use verilog-insert-one-definition style
                line = " " * indent_pt
                line += helper._format_definition(sig, decl_type, indent_pt).rstrip(";\n").rstrip(",\n")

                # Strip trailing whitespace from definition for = alignment
                line = line.rstrip()

            # Pad to column max(48, indent_pt + 40) for the = sign
            target_col = max(48, indent_pt + 40)
            if len(line) < target_col:
                line += " " * (target_col - len(line))
            else:
                line += " "

            line += f"= {tieoff};\n"
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))


class AutoUnused:
    """Expand ``/*AUTOUNUSED*/`` markers.

    Lists all unused input and inout signals (those not consumed by
    any sub-instance).

    Port of ``verilog-auto-unused``.
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
        from .wire import AutoWire

        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Find the /* before point to determine indent
        marker_start = text.rfind("/*", 0, pt)
        if marker_start < 0:
            return
        line_start = text.rfind("\n", 0, marker_start)
        line_start = 0 if line_start < 0 else line_start + 1
        indent_pt = marker_start - line_start

        helper = AutoWire(self._buf, self._config, self._db)
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOUNUSED: inputs + inouts not consumed by sub-instances
        all_inputs = moddecls.inputs + moddecls.inouts
        consumed = modsubdecls.inputs + modsubdecls.inouts
        sig_list = signals_not_in(all_inputs, consumed)

        if self._config.auto_unused_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_unused_ignore_regexp
            )

        if not sig_list:
            return

        sig_list = signals_sort_by_name(sig_list)

        lines: list[str] = []
        lines.append(" " * indent_pt + "// Beginning of automatic unused inputs\n")

        for sig in sig_list:
            lines.append(" " * indent_pt + sig.name + ",\n")

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))
