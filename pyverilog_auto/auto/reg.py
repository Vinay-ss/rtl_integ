"""AutoReg / AutoRegInput — expand AUTOREG and AUTOREGINPUT markers.

Ported from ``verilog-auto-reg`` (around lines 13029–13082) and
``verilog-auto-reg-input`` (around lines 13084–13139) of
``verilog-mode.el``.

AUTOREG generates ``reg`` declarations for a module's outputs that
aren't already declared as reg/logic in the module body.

AUTOREGINPUT generates ``reg`` declarations for sub-instance inputs
that aren't already declared or assigned to.
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


class AutoReg:
    """Expand ``/*AUTOREG*/`` markers in a :class:`VerilogBuffer`."""

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
        """Expand ``/*AUTOREG*/`` at current point."""
        from .wire import AutoWire

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        moddecls = helper._get_current_moddecls()

        modsubdecls = helper._get_current_sub_decls()

        # AUTOREG: outputs of this module that don't have explicit
        # reg/wire/logic declarations in the module body.
        # Also exclude outputs that already have a type (reg/wire/logic),
        # and sub-instance outputs/inouts (those are wires).
        typed_outputs = [s for s in moddecls.outputs if s.type]
        existing = (
            typed_outputs
            + moddecls.vars      # existing reg/wire/logic declarations
            + moddecls.assigns   # continuous assign targets
            + moddecls.consts    # existing parameter/localparam
            + moddecls.gparams
            + modsubdecls.interfaced
            + modsubdecls.outputs
            + modsubdecls.inouts
        )
        simplify = self._config.auto_simplify_expressions
        sig_list = signals_combine_bus(
            signals_not_in(moddecls.outputs, existing), simplify=simplify)

        if not sig_list:
            return

        sig_list = signals_sort_by_name(sig_list)

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic regs (for this module's undeclared outputs)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "reg", indent_pt)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))


class AutoRegInput:
    """Expand ``/*AUTOREGINPUT*/`` markers in a :class:`VerilogBuffer`.

    AUTOREGINPUT generates ``reg`` declarations for sub-instance inputs
    (and inouts) that aren't already declared or assigned to.

    Port of ``verilog-auto-reg-input``.
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
        """Expand ``/*AUTOREGINPUT*/`` at current point."""
        from .wire import AutoWire

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOREGINPUT: sub-instance inputs + inouts that are not
        # already declared as anything, minus assigned signals
        # (unless they match the ignore regexp)
        all_signals = helper._get_all_signals(moddecls)

        # Build exclude list: all declared signals + assigns
        # (but filter assigns through the ignore regexp)
        assigns = moddecls.assigns
        ignore_re = self._config.auto_reg_input_assigned_ignore_regexp
        if ignore_re:
            assigns = signals_not_matching_regexp(assigns, ignore_re)

        exclude = all_signals + assigns

        simplify = self._config.auto_simplify_expressions
        sub_inputs = modsubdecls.inputs + modsubdecls.inouts
        sig_list = signals_combine_bus(
            signals_not_in(sub_inputs, exclude), simplify=simplify)

        if not sig_list:
            return

        sig_list = signals_sort_by_name(sig_list)

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic reg inputs (for undeclared instantiated-module inputs)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "reg", indent_pt)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))
