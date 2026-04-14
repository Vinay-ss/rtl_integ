"""AutoOutput / AutoInput / AutoInout — expand AUTOOUTPUT, AUTOINPUT, AUTOINOUT.

Ported from ``verilog-auto-output``, ``verilog-auto-input``, and
``verilog-auto-inout`` of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..signal import (
    ModDecls,
    Signal,
    SubDecls,
    signals_combine_bus,
    signals_matching_regexp,
    signals_not_in,
    signals_not_matching_regexp,
    signals_sort_by_name,
)

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


def _read_auto_regexp(buf: "VerilogBuffer") -> str | None:
    """Read optional regexp from an AUTO marker like ``/*AUTOINPUT("^s")*/``."""
    text = buf.buffer_string()
    pt = buf.point()
    marker_start = text.rfind("/*AUTO", 0, pt)
    if marker_start < 0:
        return None
    marker_end = text.find("*/", marker_start)
    if marker_end < 0:
        return None
    marker = text[marker_start:marker_end + 2]
    m = re.search(r'\("([^"]+)"\)', marker)
    if m:
        return _translate_emacs_re(m.group(1))
    return None


def _translate_emacs_re(pattern: str) -> str:
    """Translate basic Emacs regex to Python regex."""
    from ..regex_compat import translate_emacs_regex
    try:
        return translate_emacs_regex(pattern)
    except Exception:
        return pattern


class AutoOutput:
    """Expand ``/*AUTOOUTPUT*/`` markers."""

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

        regexp = _read_auto_regexp(self._buf)

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOOUTPUT: sub-instance outputs not already declared as
        # output/inout/input AND not consumed by any sub-instance input/inout
        existing = (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
            + modsubdecls.inputs
            + modsubdecls.inouts
        )
        sub_outs = modsubdecls.outputs
        simplify = self._config.auto_simplify_expressions
        sig_list = signals_combine_bus(
            signals_not_in(sub_outs, existing), simplify=simplify)

        if regexp:
            sig_list = signals_matching_regexp(sig_list, regexp)
        if self._config.auto_output_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_output_ignore_regexp)

        if not sig_list:
            return

        # Populate modi-cache (reverse-alpha, like Emacs's signals-combine-bus)
        cache = helper._get_modi_cache()
        cache["outputs"] = list(reversed(sig_list)) + cache["outputs"]

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic outputs (from unused autoinst outputs)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "output", indent_pt, v2k=v2k)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()


class AutoInput:
    """Expand ``/*AUTOINPUT*/`` markers."""

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

        regexp = _read_auto_regexp(self._buf)

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOINPUT: sub-instance inputs not already declared as
        # output/inout/input AND not driven by any sub-instance output
        existing = (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
            + moddecls.vars
            + moddecls.consts
            + moddecls.gparams
            + modsubdecls.interfaced
        )
        # Also exclude signals that are sub-instance outputs
        # (those would be declared as wires/outputs, not inputs)
        driven = modsubdecls.outputs + modsubdecls.inouts
        sub_ins = modsubdecls.inputs
        simplify = self._config.auto_simplify_expressions
        candidates = signals_not_in(sub_ins, existing)
        sig_list = signals_combine_bus(
            signals_not_in(candidates, driven), simplify=simplify)

        if regexp:
            sig_list = signals_matching_regexp(sig_list, regexp)
        if self._config.auto_input_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_input_ignore_regexp)

        if not sig_list:
            return

        # Populate modi-cache (reverse-alpha, like Emacs's signals-combine-bus)
        cache = helper._get_modi_cache()
        cache["inputs"] = list(reversed(sig_list)) + cache["inputs"]

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic inputs (from unused autoinst inputs)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "input", indent_pt, v2k=v2k)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()


class AutoOutputEvery:
    """Expand ``/*AUTOOUTPUTEVERY*/`` markers.

    Unlike AUTOOUTPUT (which only considers sub-instance outputs),
    this generates output declarations for every signal declared in
    the module that is not already a port (output/inout/input).
    This mirrors ``verilog-auto-output-every`` from verilog-mode.el,
    which computes ``(signals-not-in (decls-get-signals) (decls-get-ports))``.
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

        regexp = _read_auto_regexp(self._buf)

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        # AUTOOUTPUTEVERY: all declared signals minus existing ports.
        # Mirrors the Emacs algorithm:
        #   (signals-not-in (decls-get-signals) (decls-get-ports))
        # "all signals" = outputs + inouts + inputs + vars + consts + gparams
        # "ports"       = outputs + inouts + inputs
        all_signals = (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
            + moddecls.vars
            + moddecls.consts
            + moddecls.gparams
        )
        ports = (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
        )
        sig_list = signals_combine_bus(signals_not_in(all_signals, ports))

        # Enrich signals with metadata from sub-instance outputs.
        # Earlier AUTO expansions (e.g. AUTOWIRE) insert declarations
        # whose type/comment info comes from sub-module ports, but
        # re-parsing the buffer text loses that metadata (built-in type
        # keywords like ``logic`` are not stored by the DeclParser).
        # This mirrors the Emacs modi-cache behaviour where AUTOWIRE
        # adds signals to the cache with full type info preserved.
        if modsubdecls.outputs:
            sub_by_name: dict[str, "Signal"] = {}
            for s in modsubdecls.outputs:
                if s.name not in sub_by_name:
                    sub_by_name[s.name] = s
            enriched: list["Signal"] = []
            for sig in sig_list:
                sub = sub_by_name.get(sig.name)
                if sub is not None:
                    enriched.append(Signal(
                        name=sig.name,
                        bits=sig.bits or sub.bits,
                        comment=sig.comment or sub.comment,
                        memory=sig.memory or sub.memory,
                        enum=sig.enum or sub.enum,
                        signed=sig.signed or sub.signed,
                        type=sig.type or sub.type,
                        multidim=sig.multidim or sub.multidim,
                        modport=sig.modport or sub.modport,
                    ))
                else:
                    enriched.append(sig)
            sig_list = enriched

        if regexp:
            sig_list = signals_matching_regexp(sig_list, regexp)
        if self._config.auto_output_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_output_ignore_regexp)

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()

        if not sig_list:
            if v2k:
                helper._repair_close_comma()
            return

        # Populate modi-cache (reverse-alpha, like Emacs's signals-combine-bus)
        cache = helper._get_modi_cache()
        cache["outputs"] = list(reversed(sig_list)) + cache["outputs"]

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic outputs (every signal)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "output", indent_pt, v2k=v2k)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()


class AutoTieoff:
    """Expand ``/*AUTOTIEOFF*/`` markers.

    Generates tie-off assignments for the module's outputs that have
    no driver (no reg/wire assignment in the module body).
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

        # AUTOTIEOFF: outputs that have no driver (not in vars/consts)
        existing = moddecls.vars + moddecls.consts + moddecls.gparams
        simplify = self._config.auto_simplify_expressions
        sig_list = signals_combine_bus(
            signals_not_in(moddecls.outputs, existing), simplify=simplify)

        if not sig_list:
            return

        sig_list = signals_sort_by_name(sig_list)

        decl_type = self._config.auto_tieoff_declaration or "wire"

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic tieoffs (for this module's unterminated outputs)\n"
        )

        for sig in sig_list:
            # Calculate the tie-off value based on width
            width = self._compute_width(sig)
            if width >= 1:
                hex_digits = (width + 3) // 4
                tieoff = f"{width}'h" + "0" * hex_digits
            else:
                # Unknown width — use 'h0
                tieoff = "'0"

            # Build the definition line
            type_str = decl_type
            parts = [type_str]
            if sig.signed:
                parts.append(f" {sig.signed}")
            if sig.multidim:
                parts.append(" " + " ".join(sig.multidim))
            if sig.bits:
                parts.append(f" {sig.bits}")
            full_type = "".join(parts)

            line = " " * indent_pt + full_type

            name_col = max(24, indent_pt + 16)
            if len(line) < name_col:
                line += " " * (name_col - len(line))
            elif not line.endswith(" "):
                line += " "

            line += f"{sig.name} = {tieoff};\n"
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        self._buf.insert("".join(lines))

    @staticmethod
    def _compute_width(sig: Signal) -> int:
        """Try to compute the bit width from the signal's bits field."""
        if not sig.bits:
            return 1
        import re
        # Try [N:0] or [N-1:0] pattern
        m = re.match(r"\[(\d+):0\]", sig.bits)
        if m:
            return int(m.group(1)) + 1
        m = re.match(r"\[(\d+)-1:0\]", sig.bits)
        if m:
            return int(m.group(1))
        # Try [0:N]
        m = re.match(r"\[0:(\d+)\]", sig.bits)
        if m:
            return int(m.group(1)) + 1
        # Unknown width
        return 0


class AutoInout:
    """Expand ``/*AUTOINOUT*/`` markers."""

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

        regexp = _read_auto_regexp(self._buf)

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        modsubdecls = helper._get_current_sub_decls()

        existing = (
            moddecls.outputs
            + moddecls.inouts
            + moddecls.inputs
            + modsubdecls.inputs
            + modsubdecls.outputs
        )
        simplify = self._config.auto_simplify_expressions
        sub_io = modsubdecls.inouts
        sig_list = signals_combine_bus(
            signals_not_in(sub_io, existing), simplify=simplify)

        if regexp:
            sig_list = signals_matching_regexp(sig_list, regexp)
        if self._config.auto_inout_ignore_regexp:
            sig_list = signals_not_matching_regexp(
                sig_list, self._config.auto_inout_ignore_regexp)

        if not sig_list:
            return

        # Populate modi-cache (reverse-alpha, like Emacs's signals-combine-bus)
        cache = helper._get_modi_cache()
        cache["inouts"] = list(reversed(sig_list)) + cache["inouts"]

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic inouts (from unused autoinst inouts)\n"
        )

        for sig in sig_list:
            line = helper._format_definition(sig, "inout", indent_pt, v2k=v2k)
            lines.append(line)

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()
