"""AutoAssignModport / AutoInoutModport — modport-related AUTOs.

Ported from ``verilog-auto-assign-modport`` (lines 12162–12246) and
``verilog-auto-inout-modport`` (lines 13892–14032) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import (
    Signal,
    signals_in,
    signals_not_in,
    signals_sort_by_name,
    signals_matching_regexp,
)

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


def _get_modport_decls_shared(submoddecls, modport_re):
    """Get declarations from a modport, handling clocking-block references.

    When a modport references a clocking block (``modport mp(clocking blk)``),
    the DeclParser creates a separate Modport entry for the clocking block with
    its signals.  This function merges the clocking block's signals into the
    modport if the modport itself has no signals.
    """
    from ..signal import ModDecls

    mp_by_name = {mp.name: mp for mp in submoddecls.modports}

    for mp in submoddecls.modports:
        if not re.search(modport_re, mp.name):
            continue

        inputs = []
        outputs = []
        inouts = []

        if mp.signals:
            # Modport has its own signal declarations
            for sig in mp.signals:
                if sig.type == "input":
                    inputs.append(sig)
                elif sig.type == "output":
                    outputs.append(sig)
                elif sig.type == "inout":
                    inouts.append(sig)
        else:
            # Modport might reference a clocking block — check for
            # a clocking block modport that has signals and is NOT
            # the same modport we're looking at
            for other_mp in submoddecls.modports:
                if other_mp.name != mp.name and other_mp.signals:
                    for sig in other_mp.signals:
                        if sig.type == "input":
                            inputs.append(sig)
                        elif sig.type == "output":
                            outputs.append(sig)
                        elif sig.type == "inout":
                            inouts.append(sig)

        if not inputs and not outputs and not inouts:
            # Fallback: use interface vars as inputs (common pattern)
            inputs = list(submoddecls.vars)

        return ModDecls(inputs=inputs, outputs=outputs, inouts=inouts)

    return None


def _read_auto_params(text: str, pt: int, marker: str) -> list[str] | None:
    """Read quoted parameters from an AUTO marker."""
    marker_start = text.rfind(f"/*{marker}", 0, pt)
    if marker_start < 0:
        return None
    marker_end = text.find("*/", marker_start)
    if marker_end < 0:
        return None
    content = text[marker_start:marker_end + 2]
    params = re.findall(r'"([^"]*)"', content)
    # Also try space-separated quoted params
    if not params:
        params = re.findall(r'"([^"]*)"', content)
    return params if params else None


class AutoAssignModport:
    """Expand ``/*AUTOASSIGNMODPORT("if_name", "mp_name", "inst")*/``.

    Generates ``assign`` statements connecting a modport's signals between
    an interface instance and local signals.

    Port of ``verilog-auto-assign-modport``.
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

        params = _read_auto_params(text, pt, "AUTOASSIGNMODPORT")
        if not params or len(params) < 3:
            return

        submod = params[0]
        modport_re = params[1]
        inst_name = params[2]
        regexp = params[3] if len(params) > 3 else None
        prefix = params[4] if len(params) > 4 else ""

        # Look up the interface module
        submodi = self._db.lookup(submod, ignore_error=True)
        if submodi is None:
            return

        submoddecls = self._db.get_decls(submodi)

        # Find modport declarations
        modport_decls = self._get_modport_decls(submoddecls, submod, modport_re)
        if modport_decls is None:
            return

        indent_pt = self._current_indentation(text, pt)

        # Get signals from modport that are also in interface vars
        # (not ports — modport signals come from vars)
        sig_list_i = signals_in(
            submoddecls.vars,
            signals_not_in(modport_decls.inputs, self._get_ports(submoddecls))
        )
        sig_list_o = signals_in(
            submoddecls.vars,
            signals_not_in(modport_decls.outputs, self._get_ports(submoddecls))
        )

        # Apply regexp filter
        if regexp:
            sig_list_i = signals_matching_regexp(sig_list_i, regexp)
            sig_list_o = signals_matching_regexp(sig_list_o, regexp)

        sig_list_i = signals_sort_by_name(sig_list_i)
        sig_list_o = signals_sort_by_name(sig_list_o)

        if not sig_list_i and not sig_list_o:
            return

        helper = AutoWire(self._buf, self._config, self._db)

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic assignments from modport\n"
        )

        # Output signals: assign prefix+name = inst.name
        for sig in sig_list_o:
            lines.append(
                " " * indent_pt
                + f"assign {prefix}{sig.name} = {inst_name}.{sig.name};\n"
            )

        # Input signals: assign inst.name = prefix+name
        for sig in sig_list_i:
            lines.append(
                " " * indent_pt
                + f"assign {inst_name}.{sig.name} = {prefix}{sig.name};\n"
            )

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))

    # ------------------------------------------------------------------

    def _get_modport_decls(self, submoddecls, submod, modport_re):
        """Get declarations from a modport."""
        return _get_modport_decls_shared(submoddecls, modport_re)

    @staticmethod
    def _get_ports(decls):
        """Get all port signals from declarations."""
        return decls.outputs + decls.inputs + decls.inouts

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


class AutoInoutModport:
    """Expand ``/*AUTOINOUTMODPORT("if_name", "mp_name")*/``.

    Generates input/output/inout declarations from an interface modport.

    Port of ``verilog-auto-inout-modport``.
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

        params = _read_auto_params(text, pt, "AUTOINOUTMODPORT")
        if not params or len(params) < 2:
            return

        submod = params[0]
        modport_re = params[1]
        regexp = params[2] if len(params) > 2 else None
        prefix = params[3] if len(params) > 3 else ""

        # Look up the interface module
        submodi = self._db.lookup(submod, ignore_error=True)
        if submodi is None:
            return

        submoddecls = self._db.get_decls(submodi)

        # Find modport declarations
        modport_decls = self._get_modport_decls(submoddecls, submod, modport_re)
        if modport_decls is None:
            return

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()

        # Get signals from modport that are also in interface vars
        sig_list_i = signals_in(
            submoddecls.vars,
            signals_not_in(modport_decls.inputs, self._get_ports(submoddecls))
        )
        sig_list_o = signals_in(
            submoddecls.vars,
            signals_not_in(modport_decls.outputs, self._get_ports(submoddecls))
        )
        sig_list_io = signals_in(
            submoddecls.vars,
            signals_not_in(modport_decls.inouts, self._get_ports(submoddecls))
        )

        # Apply regexp filter and prefix, then exclude already-declared ports
        existing_ports = moddecls.outputs + moddecls.inputs + moddecls.inouts

        if regexp:
            sig_list_i = signals_matching_regexp(sig_list_i, regexp)
            sig_list_o = signals_matching_regexp(sig_list_o, regexp)
            sig_list_io = signals_matching_regexp(sig_list_io, regexp)

        # Add prefix to signal names
        if prefix:
            sig_list_i = self._add_prefix(sig_list_i, prefix)
            sig_list_o = self._add_prefix(sig_list_o, prefix)
            sig_list_io = self._add_prefix(sig_list_io, prefix)

        # Remove already-declared ports
        sig_list_i = signals_not_in(sig_list_i, existing_ports)
        sig_list_o = signals_not_in(sig_list_o, existing_ports)
        sig_list_io = signals_not_in(sig_list_io, existing_ports)

        if not sig_list_i and not sig_list_o and not sig_list_io:
            return

        if v2k:
            helper._repair_open_comma()

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic in/out/inouts (from modport)\n"
        )

        # Output, then inout, then input
        for sig in sig_list_o:
            lines.append(helper._format_definition(sig, "output", indent_pt, v2k=v2k))
        for sig in sig_list_io:
            lines.append(helper._format_definition(sig, "inout", indent_pt, v2k=v2k))
        for sig in sig_list_i:
            lines.append(helper._format_definition(sig, "input", indent_pt, v2k=v2k))

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()

    # ------------------------------------------------------------------

    def _get_modport_decls(self, submoddecls, submod, modport_re):
        """Get declarations from a modport."""
        return _get_modport_decls_shared(submoddecls, modport_re)

    @staticmethod
    def _get_ports(decls):
        return decls.outputs + decls.inputs + decls.inouts

    @staticmethod
    def _add_prefix(sigs: list[Signal], prefix: str) -> list[Signal]:
        """Return new signals with prefix added to names."""
        return [
            Signal(
                name=prefix + s.name,
                bits=s.bits,
                comment=s.comment,
                memory=s.memory,
                enum=s.enum,
                signed=s.signed,
                type=s.type,
                multidim=s.multidim,
                modport=s.modport,
            )
            for s in sigs
        ]
