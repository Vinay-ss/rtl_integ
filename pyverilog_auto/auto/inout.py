"""AutoInoutModule / AutoInoutComp / AutoInoutIn / AutoInoutParam.

Ported from ``verilog-auto-inout-module`` (lines 13551–13697),
``verilog-auto-inout-comp`` (lines 13699–13768),
``verilog-auto-inout-in`` (lines 13770–13821), and
``verilog-auto-inout-param`` (lines 13823–13890) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from ..signal import (
    Signal,
    signals_matching_regexp,
    signals_not_in,
    signals_not_matching_regexp,
)


def _translate_emacs_re(pattern: str) -> str:
    """Translate basic Emacs regex to Python regex."""
    from ..regex_compat import translate_emacs_regex
    try:
        return translate_emacs_regex(pattern)
    except Exception:
        return pattern

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

def _read_auto_params(buf: "VerilogBuffer", max_params: int = 4) -> list[str]:
    """Read comma-separated quoted parameters from an AUTO marker.

    Handles markers like::

        /*AUTOINOUTMODULE("mod")*/
        /*AUTOINOUTMODULE("mod","^regexp")*/
        /*AUTOINOUTMODULE("mod","","^output.*","excl")*/
    """
    text = buf.buffer_string()
    pt = buf.point()

    # Find the AUTO marker ending at (or just before) pt
    marker_start = text.rfind("/*AUTO", 0, pt)
    if marker_start < 0:
        return []
    marker_end = text.find("*/", marker_start)
    if marker_end < 0:
        return []
    marker = text[marker_start:marker_end + 2]

    # Extract the parenthesised section
    paren_start = marker.find("(")
    if paren_start < 0:
        return []
    paren_end = marker.rfind(")")
    if paren_end < 0 or paren_end <= paren_start:
        return []
    inner = marker[paren_start + 1:paren_end]

    # Split by commas, respecting quotes
    params: list[str] = []
    current = ""
    in_quote = False
    for ch in inner:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            params.append(current.strip().strip('"'))
            current = ""
            continue
        else:
            current += ch
    if current:
        params.append(current.strip().strip('"'))

    return params[:max_params]


def _signals_matching_dir_re(
    sigs: list[Signal], direction: str, regexp: str
) -> list[Signal]:
    """Filter signals by matching direction + type string against regexp.

    Port of ``verilog-signals-matching-dir-re``.  Constructs a string
    like ``"input [3:0]"`` and checks if *regexp* matches it.
    """
    if not regexp:
        return sigs
    compiled = re.compile(regexp)
    out: list[Signal] = []
    for sig in sigs:
        to_match = direction
        if sig.signed:
            to_match += f" {sig.signed}"
        if sig.multidim:
            to_match += " " + "".join(sig.multidim)
        if sig.bits:
            to_match += sig.bits
        if compiled.search(to_match):
            out.append(sig)
    return out


def _signals_edit_wire_reg(sigs: list[Signal]) -> list[Signal]:
    """Strip ``wire``/``reg`` type from signals (keep ``logic`` etc.).

    Port of ``verilog-signals-edit-wire-reg``.
    """
    out: list[Signal] = []
    for sig in sigs:
        if sig.type in ("wire", "reg"):
            out.append(replace(sig, type=None))
        else:
            out.append(sig)
    return out


# ------------------------------------------------------------------
# AutoInoutModule
# ------------------------------------------------------------------

class AutoInoutModule:
    """Expand ``/*AUTOINOUTMODULE("mod")*/`` markers.

    Copies input/output/inout ports from the named module into the
    current module.  Already-declared ports are excluded.

    Port of ``verilog-auto-inout-module``.
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
        self._expand_common(complement=False, all_in=False)

    def _expand_common(
        self,
        complement: bool = False,
        all_in: bool = False,
    ) -> None:
        from .wire import AutoWire

        params = _read_auto_params(self._buf, 4)
        if not params:
            return
        submod_name = params[0]
        regexp = _translate_emacs_re(params[1]) if len(params) > 1 and params[1] else None
        direction_re = _translate_emacs_re(params[2]) if len(params) > 2 and params[2] else None
        not_re = _translate_emacs_re(params[3]) if len(params) > 3 and params[3] else None

        # Lookup the referenced module
        submodi = self._db.lookup(submod_name, ignore_error=True)
        if submodi is None:
            return

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        submoddecls = self._db.get_decls(submodi)

        # Compute signal lists based on mode
        if all_in:
            # AUTOINOUTIN: all ports become inputs
            sig_list_i = signals_not_in(
                submoddecls.inputs + submoddecls.inouts + submoddecls.outputs,
                moddecls.inputs,
            )
            sig_list_o: list[Signal] = []
            sig_list_io: list[Signal] = []
        elif complement:
            # AUTOINOUTCOMP: flip directions
            sig_list_i = signals_not_in(
                submoddecls.outputs,
                moddecls.inputs,
            )
            sig_list_o = signals_not_in(
                submoddecls.inputs,
                moddecls.outputs,
            )
            sig_list_io = signals_not_in(
                submoddecls.inouts,
                moddecls.inouts,
            )
        else:
            # AUTOINOUTMODULE: same directions
            sig_list_i = signals_not_in(
                submoddecls.inputs,
                moddecls.inputs,
            )
            sig_list_o = signals_not_in(
                submoddecls.outputs,
                moddecls.outputs,
            )
            sig_list_io = signals_not_in(
                submoddecls.inouts,
                moddecls.inouts,
            )

        sig_list_if = signals_not_in(
            submoddecls.interfaces,
            moddecls.interfaces,
        )

        # Apply regexp filter (on signal name)
        if regexp:
            sig_list_i = signals_matching_regexp(sig_list_i, regexp)
            sig_list_o = signals_matching_regexp(sig_list_o, regexp)
            sig_list_io = signals_matching_regexp(sig_list_io, regexp)
            sig_list_if = signals_matching_regexp(sig_list_if, regexp)

        # Apply direction_re filter (matches against "direction [bits]")
        if direction_re:
            sig_list_i = _signals_matching_dir_re(sig_list_i, "input", direction_re)
            sig_list_o = _signals_matching_dir_re(sig_list_o, "output", direction_re)
            sig_list_io = _signals_matching_dir_re(sig_list_io, "inout", direction_re)
            sig_list_if = _signals_matching_dir_re(sig_list_if, "interface", direction_re)

        # Apply not-re filter
        if not_re:
            sig_list_i = signals_not_matching_regexp(sig_list_i, not_re)
            sig_list_o = signals_not_matching_regexp(sig_list_o, not_re)
            sig_list_io = signals_not_matching_regexp(sig_list_io, not_re)
            sig_list_if = signals_not_matching_regexp(sig_list_if, not_re)

        # Strip wire/reg type (keep logic)
        sig_list_i = _signals_edit_wire_reg(sig_list_i)
        sig_list_o = _signals_edit_wire_reg(sig_list_o)
        sig_list_io = _signals_edit_wire_reg(sig_list_io)

        if not (sig_list_i or sig_list_o or sig_list_io or sig_list_if):
            return

        # Populate modi-cache (declaration order — unlike AUTOOUTPUT/AUTOINPUT
        # which use signals_combine_bus, these signals come directly from
        # submodule declarations and should NOT be reversed)
        cache = helper._get_modi_cache()
        if sig_list_o:
            cache["outputs"] = list(sig_list_o) + cache["outputs"]
        if sig_list_i:
            cache["inputs"] = list(sig_list_i) + cache["inputs"]
        if sig_list_io:
            cache["inouts"] = list(sig_list_io) + cache["inouts"]

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic in/out/inouts (from specific module)\n"
        )

        # Insert in order: output, inout, input, interface
        for sig in sig_list_o:
            lines.append(helper._format_definition(sig, "output", indent_pt, v2k=v2k))
        for sig in sig_list_io:
            lines.append(helper._format_definition(sig, "inout", indent_pt, v2k=v2k))
        for sig in sig_list_i:
            lines.append(helper._format_definition(sig, "input", indent_pt, v2k=v2k))
        for sig in sig_list_if:
            lines.append(helper._format_definition(sig, "interface", indent_pt, v2k=v2k))

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()


# ------------------------------------------------------------------
# AutoInoutComp
# ------------------------------------------------------------------

class AutoInoutComp:
    """Expand ``/*AUTOINOUTCOMP("mod")*/`` markers.

    Like AUTOINOUTMODULE but flips directions: the referenced module's
    inputs become outputs and vice-versa.

    Port of ``verilog-auto-inout-comp``.
    """

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._impl = AutoInoutModule(buf, config, db)

    def expand(self) -> None:
        self._impl._expand_common(complement=True, all_in=False)


# ------------------------------------------------------------------
# AutoInoutIn
# ------------------------------------------------------------------

class AutoInoutIn:
    """Expand ``/*AUTOINOUTIN("mod")*/`` markers.

    All ports of the referenced module become inputs in the current
    module.

    Port of ``verilog-auto-inout-in``.
    """

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._impl = AutoInoutModule(buf, config, db)

    def expand(self) -> None:
        self._impl._expand_common(complement=False, all_in=True)


# ------------------------------------------------------------------
# AutoInoutParam
# ------------------------------------------------------------------

class AutoInoutParam:
    """Expand ``/*AUTOINOUTPARAM("mod")*/`` markers.

    Copies parameter declarations from the named module into the
    current module.  Already-declared parameters are excluded.

    Port of ``verilog-auto-inout-param``.
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

        params = _read_auto_params(self._buf, 2)
        if not params:
            return
        submod_name = params[0]
        regexp = _translate_emacs_re(params[1]) if len(params) > 1 and params[1] else None

        submodi = self._db.lookup(submod_name, ignore_error=True)
        if submodi is None:
            return

        helper = AutoWire(self._buf, self._config, self._db)
        indent_pt = helper._current_indentation()
        v2k = helper._in_paren_count_quick() > 0
        moddecls = helper._get_current_moddecls()
        submoddecls = self._db.get_decls(submodi)

        sig_list = signals_not_in(
            submoddecls.gparams,
            moddecls.gparams,
        )

        if regexp:
            sig_list = signals_matching_regexp(sig_list, regexp)

        if not sig_list:
            return

        lines: list[str] = []
        lines.append(
            " " * indent_pt
            + "// Beginning of automatic parameters (from specific module)\n"
        )

        for sig in sig_list:
            lines.append(helper._format_definition(sig, "parameter", indent_pt, v2k=v2k))

        lines.append(" " * indent_pt + "// End of automatics\n")

        helper._forward_or_insert_line()
        if v2k:
            helper._repair_open_comma()
        self._buf.insert("".join(lines))

        if v2k:
            helper._repair_close_comma()
