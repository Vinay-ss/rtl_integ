"""SubDeclParser — read sub-instance signal connections from a module body.

Ported from ``verilog-read-sub-decls`` (around lines 9833–9917 of
``verilog-mode.el``) and ``verilog-read-sub-decls-line`` (lines 9738–9800)
and ``verilog-read-sub-decls-sig`` (lines 9597–9681).

This parser scans an already-expanded module body (with ``// Outputs``,
``// Inputs``, ``// Inouts`` section comments) and collects all signals
that sub-instances drive or consume, building a :class:`SubDecls` object.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from ..signal import ModDecls, Signal, SubDecls, signals_combine_bus

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


# Gate-level primitive keywords
_GATE_KEYWORDS = {
    "and", "buf", "bufif0", "bufif1", "cmos", "nand", "nmos", "nor",
    "not", "notif0", "notif1", "or", "pmos", "pulldown", "pullup",
    "rcmos", "rnmos", "rpmos", "rtran", "rtranif0", "rtranif1",
    "tran", "tranif0", "tranif1", "xnor", "xor",
}

# Module-definition keywords (used to find beginning/end of module)
_MODULE_KW_RE = re.compile(
    r"\b(?:connectmodule|module|interface|program)\b"
)
_END_MODULE_KW_RE = re.compile(
    r"\b(?:endconnectmodule|endmodule|endinterface|endprogram)\b"
)

# Escaped identifier sub-pattern: a backslash followed by non-whitespace chars
_ESC_IDENT = "\\\\" + "[^ \\t\\n\\f]+"

# Port connection line:  .port_name  (signal_expr)
_PORT_LINE_RE = re.compile(
    r"\s*\.\s*([a-zA-Z0-9`_$]+|" + _ESC_IDENT + r")\s*\(\s*"
)

# Port connection with .name shorthand (no parentheses):  .port_name,
_DOT_NAME_RE = re.compile(
    r"\s*\.\s*([a-zA-Z0-9`_$]+|" + _ESC_IDENT + r")\s*[,)/]"
)

# Simple signal name (no hierarchy dots):  ident )
_SIG_SIMPLE_RE = re.compile(
    r"([a-zA-Z_][a-zA-Z_0-9]*)\s*\)"
)

# Signal name with vector:  ident [bits] )
_SIG_VEC_RE = re.compile(
    r"([a-zA-Z_][a-zA-Z_0-9]*)\s*(\[[^\]]+\])\s*\)"
)

# AUTOINST / .* marker that starts a sub-decl region
_AUTOINST_RE = re.compile(
    r"/\*AUTOINST(?:\(.*?\))?\*/|\.\*"
)

# Section comment that tags signal direction
_SECTION_COMMENT_RE = re.compile(
    r"\s*(?:\(?\s*)?//\s*(Outputs|Inouts|Inputs|Interfaces|Interfaced)"
)


class SubDeclParser:
    """Scan a module body for sub-instance pin connections.

    This only works on instantiations created with ``/*AUTOINST*/`` (or
    manually annotated with ``// Outputs`` / ``// Inputs`` section comments).
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> SubDecls:
        """Scan *buf* for all sub-instance pin connections.

        Return :class:`SubDecls` with outputs/inouts/inputs/interfaces
        populated.  Port of ``verilog-read-sub-decls``.
        """
        text = self._buf.buffer_string()

        # Find module boundaries
        beg_mod = self._find_beg_of_defun(text, self._buf.point())
        end_mod = self._find_end_of_defun(text, self._buf.point())

        sigs_out: list[Signal] = []
        sigs_inout: list[Signal] = []
        sigs_in: list[Signal] = []
        sigs_intf: list[Signal] = []
        sigs_intfd: list[Signal] = []

        # Find all /*AUTOINST*/ or .* markers in the module
        for m in _AUTOINST_RE.finditer(text, beg_mod, end_mod):
            marker_start = m.start()

            # Read context: module name and instance name
            submod = self._read_inst_module(text, marker_start)
            inst = self._read_inst_name(text, marker_start)
            if not submod:
                continue

            is_gate = submod in _GATE_KEYWORDS
            comment = f"{inst} of {submod}" + ("" if is_gate else ".v")

            # Get submodule declarations
            submoddecls: Optional[ModDecls] = None
            if not is_gate:
                submodi = self._db.lookup(submod, ignore_error=True)
                if submodi is None:
                    continue
                submoddecls = self._db.get_decls(submodi)
            else:
                submoddecls = ModDecls()

            # Read parameter values if configured
            par_values: list[tuple[str, str]] = []
            if self._config.auto_inst_param_value and self._config.auto_inst_param_value_type:
                par_values = self._read_inst_param_value(text, marker_start)

            # Find the instantiation paren range
            open_paren = self._find_open_paren_backward(text, marker_start)
            if open_paren is None:
                continue
            close_paren = self._find_close_paren(text, open_paren)
            if close_paren is None:
                continue
            inst_text = text[open_paren:close_paren]

            # Scan for section comments and read port lines
            pos = 0
            while pos < len(inst_text):
                sm = _SECTION_COMMENT_RE.match(inst_text, pos)
                if sm:
                    section = sm.group(1)
                    pos = sm.end()
                    # Skip to next line
                    nl = inst_text.find("\n", pos)
                    if nl >= 0:
                        pos = nl + 1
                    else:
                        break

                    # Read port lines until we hit a non-port line
                    while pos < len(inst_text):
                        # Try .port(signal) pattern
                        pm = _PORT_LINE_RE.match(inst_text, pos)
                        if pm:
                            port_name = pm.group(1)
                            after_paren = pm.end()

                            # Check for AUTONOHOOKUP (search the
                            # original text so comments past the
                            # closing paren are included)
                            abs_pos = open_paren + after_paren
                            rest_of_line_end = text.find("\n", abs_pos)
                            if rest_of_line_end < 0:
                                rest_of_line_end = len(text)
                            rest_of_line = text[abs_pos:rest_of_line_end]
                            if "AUTONOHOOKUP" in rest_of_line:
                                pos = rest_of_line_end + 1
                                continue

                            # Parse signal expression
                            sig_text = inst_text[after_paren:]
                            sig, vec, end_offset = self._parse_signal_expr(sig_text)

                            if sig:
                                self._add_sig(
                                    submoddecls, par_values, comment,
                                    port_name, sig, vec, None, None,
                                    section,
                                    sigs_out, sigs_inout, sigs_in,
                                    sigs_intf, sigs_intfd,
                                )
                            elif not self._config.auto_ignore_concat:
                                # Try to extract signals from concat expressions
                                concat_sigs = self._parse_concat_signals(sig_text)
                                for csig, cvec in concat_sigs:
                                    self._add_sig(
                                        submoddecls, par_values, comment,
                                        port_name, csig, cvec, None, None,
                                        section,
                                        sigs_out, sigs_inout, sigs_in,
                                        sigs_intf, sigs_intfd,
                                        from_concat=True,
                                    )

                            # Advance past this line
                            nl = inst_text.find("\n", after_paren)
                            pos = (nl + 1) if nl >= 0 else len(inst_text)
                            continue

                        # Try .name shorthand
                        dm = _DOT_NAME_RE.match(inst_text, pos)
                        if dm:
                            port_name = dm.group(1)
                            self._add_sig(
                                submoddecls, par_values, comment,
                                port_name, port_name, None, None, None,
                                section,
                                sigs_out, sigs_inout, sigs_in,
                                sigs_intf, sigs_intfd,
                            )
                            nl = inst_text.find("\n", pos)
                            pos = (nl + 1) if nl >= 0 else len(inst_text)
                            continue

                        # Not a port line — stop reading this section
                        break
                else:
                    # Not a section comment — advance to next line
                    nl = inst_text.find("\n", pos)
                    pos = (nl + 1) if nl >= 0 else len(inst_text)

        simplify = self._config.auto_simplify_expressions
        return SubDecls(
            outputs=signals_combine_bus(sigs_out, simplify=simplify),
            inouts=signals_combine_bus(sigs_inout, simplify=simplify),
            inputs=signals_combine_bus(sigs_in, simplify=simplify),
            interfaces=signals_combine_bus(sigs_intf, simplify=simplify),
            interfaced=signals_combine_bus(sigs_intfd, simplify=simplify),
        )

    # ------------------------------------------------------------------
    # Signal classification (port of verilog-read-sub-decls-sig)
    # ------------------------------------------------------------------

    def _add_sig(
        self,
        submoddecls: ModDecls,
        par_values: list[tuple[str, str]],
        comment: str,
        port: str,
        sig: str,
        vec: Optional[str],
        multidim: Optional[list[str]],
        mem: Optional[str],
        section: str,
        sigs_out: list[Signal],
        sigs_inout: list[Signal],
        sigs_in: list[Signal],
        sigs_intf: list[Signal],
        sigs_intfd: list[Signal],
        from_concat: bool = False,
    ) -> None:
        """Classify and add a signal to the appropriate list.

        Port of ``verilog-read-sub-decls-sig``.
        """
        if not sig or sig == "":
            return

        # In the elisp, sig is `t` (boolean) for .name shorthand and an
        # actual string for .port(signal) syntax.  Here we approximate:
        # dotname is only True when sig == port AND no vector/multidim was
        # provided, i.e. a genuine .name shorthand (no explicit connection).
        dotname = (sig == port) and not from_concat and vec is None and multidim is None

        # When the submodule has real declarations, only use section
        # fallback for gate-level primitives (empty decls).
        has_decls = bool(
            submoddecls.outputs or submoddecls.inputs
            or submoddecls.inouts or submoddecls.interfaces
        )

        # Find port in submodule declarations
        portdata = self._find_port(port, submoddecls.inouts)
        if portdata or (not has_decls and section == "Inouts"):
            bits = portdata.bits if (dotname and portdata) else vec
            md = (portdata.multidim if (dotname and portdata and portdata.multidim) else multidim)
            bits = self._apply_param_subst(bits, par_values)
            md = self._apply_param_subst_list(md, par_values)
            sig_type = self._decode_type(par_values, portdata)
            signed = portdata.signed if portdata else None
            sig_mem = mem or (portdata.memory if portdata else None)
            sigs_inout.append(Signal(
                name=sig, bits=bits,
                comment=f"To/From {comment}",
                memory=sig_mem, signed=signed, type=sig_type,
                multidim=md,
            ))
            return

        portdata = self._find_port(port, submoddecls.outputs)
        if portdata or (not has_decls and section == "Outputs"):
            bits = portdata.bits if (dotname and portdata) else vec
            md = (portdata.multidim if (dotname and portdata and portdata.multidim) else multidim)
            bits = self._apply_param_subst(bits, par_values)
            md = self._apply_param_subst_list(md, par_values)
            sig_type = self._decode_type(par_values, portdata)
            signed = portdata.signed if portdata else None
            sig_mem = mem or (portdata.memory if portdata else None)
            sigs_out.append(Signal(
                name=sig, bits=bits,
                comment=f"From {comment}",
                memory=sig_mem, signed=signed, type=sig_type,
                multidim=md,
            ))
            return

        portdata = self._find_port(port, submoddecls.inputs)
        if portdata or (not has_decls and section == "Inputs"):
            bits = portdata.bits if (dotname and portdata) else vec
            md = (portdata.multidim if (dotname and portdata and portdata.multidim) else multidim)
            bits = self._apply_param_subst(bits, par_values)
            md = self._apply_param_subst_list(md, par_values)
            sig_type = self._decode_type(par_values, portdata)
            signed = portdata.signed if portdata else None
            sig_mem = mem or (portdata.memory if portdata else None)
            sigs_in.append(Signal(
                name=sig, bits=bits,
                comment=f"To {comment}",
                memory=sig_mem, signed=signed, type=sig_type,
                multidim=md,
            ))
            return

        portdata = self._find_port(port, submoddecls.interfaces)
        if portdata or (not has_decls and section == "Interfaces"):
            bits = portdata.bits if (dotname and portdata) else vec
            md = (portdata.multidim if (dotname and portdata and portdata.multidim) else multidim)
            bits = self._apply_param_subst(bits, par_values)
            md = self._apply_param_subst_list(md, par_values)
            sig_type = self._decode_type(par_values, portdata)
            signed = portdata.signed if portdata else None
            sig_mem = mem or (portdata.memory if portdata else None)
            sigs_intf.append(Signal(
                name=sig, bits=bits,
                comment=f"To/From {comment}",
                memory=sig_mem, signed=signed, type=sig_type,
                multidim=md,
            ))
            return

        if section == "Interfaced":
            portdata = self._find_port(port, submoddecls.vars)
            bits = portdata.bits if (dotname and portdata) else vec
            md = (portdata.multidim if (dotname and portdata and portdata.multidim) else multidim)
            sig_type = self._decode_type(par_values, portdata) if portdata else None
            signed = portdata.signed if portdata else None
            sig_mem = mem or (portdata.memory if portdata else None)
            sigs_intfd.append(Signal(
                name=sig, bits=bits,
                comment=f"To/From {comment}",
                memory=sig_mem, signed=signed, type=sig_type,
                multidim=md,
            ))

    @staticmethod
    def _apply_param_subst(
        bits: Optional[str],
        par_values: list[tuple[str, str]],
    ) -> Optional[str]:
        """Substitute parameter values in a bits expression."""
        if not bits or not par_values:
            return bits
        for pname, pval in par_values:
            if pval == pname:
                continue
            repl = pval if re.fullmatch(r"[\w`$.]+", pval) else f"({pval})"
            bits = re.sub(r"\b" + re.escape(pname) + r"\b", repl, bits)
        return bits

    @staticmethod
    def _apply_param_subst_list(
        dims: Optional[list[str]],
        par_values: list[tuple[str, str]],
    ) -> Optional[list[str]]:
        """Substitute parameter values in a list of dimension strings."""
        if not dims or not par_values:
            return dims
        result = []
        for d in dims:
            for pname, pval in par_values:
                if pval == pname:
                    continue
                repl = pval if re.fullmatch(r"[\w`$.]+", pval) else f"({pval})"
                d = re.sub(r"\b" + re.escape(pname) + r"\b", repl, d)
            result.append(d)
        return result

    @staticmethod
    def _find_port(name: str, ports: list[Signal]) -> Optional[Signal]:
        """Find a port by name in a list (case-sensitive).

        Escaped Verilog identifiers (``\\name``) may have a trailing
        space stored by the declaration parser.  Normalize both sides
        before comparing.
        """
        norm = name.rstrip() if name.startswith("\\") else name
        for p in ports:
            pn = p.name.rstrip() if p.name.startswith("\\") else p.name
            if pn == norm:
                return p
        return None

    @staticmethod
    def _decode_type(
        par_values: list[tuple[str, str]],
        portdata: Optional[Signal],
    ) -> Optional[str]:
        """Port of ``verilog-read-sub-decls-type``.

        Decode signal type, substituting parameter values where applicable.
        """
        if portdata is None:
            return None
        sig_type = portdata.type
        if sig_type in ("wire", "reg", None):
            return None
        # Check if the type is a parameter that has been overridden
        for pname, pval in par_values:
            if sig_type == pname:
                return pval
        return sig_type

    # ------------------------------------------------------------------
    # Signal expression parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_signal_expr(text: str) -> tuple[Optional[str], Optional[str], int]:
        """Parse a signal expression after the opening ``(`` of a port connection.

        Returns ``(sig_name, vec_bits, chars_consumed)``.
        """
        # Simple: ident )
        m = _SIG_SIMPLE_RE.match(text)
        if m:
            return m.group(1), None, m.end()

        # With vector: ident[bits] )
        m = _SIG_VEC_RE.match(text)
        if m:
            return m.group(1), m.group(2), m.end()

        # Complex expression — try to extract at least the signal name
        # Find matching close paren
        depth = 1
        i = 0
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1

        if depth == 0:
            expr = text[:i - 1].strip()
            # Strip block comments (e.g. /*.[W-1:0]*/ or /*[A-1:0].[W-1:0]*/)
            # These are dimension annotations added by AUTOINST, not hierarchy
            expr_clean = re.sub(r"/\*.*?\*/", "", expr).strip()
            # Try to get a simple name from cleaned expression
            nm = re.match(r"([a-zA-Z_][a-zA-Z_0-9]*)(\[[^\]]+\])?$", expr_clean)
            if nm:
                return nm.group(1), nm.group(2), i
            # Handle type cast: type'(signal) — extract signal from inner expr
            cast_m = re.match(r"[a-zA-Z_]\w*'\((.+)\)$", expr_clean)
            if cast_m:
                inner = cast_m.group(1).strip()
                inm = re.match(r"([a-zA-Z_][a-zA-Z_0-9]*)(\[[^\]]+\])?$", inner)
                if inm:
                    return inm.group(1), inm.group(2), i
            # If expression contains hierarchy (.), concatenation ({), or
            # constants (digits), skip it
            if re.match(r"[a-zA-Z_]", expr_clean) and "." not in expr_clean:
                nm2 = re.match(r"([a-zA-Z_][a-zA-Z_0-9]*)", expr_clean)
                if nm2:
                    return nm2.group(1), None, i
        return None, None, max(i, 1)

    @staticmethod
    def _parse_concat_signals(text: str) -> list[tuple[str, Optional[str]]]:
        """Extract signal names from a concatenation expression like ``{a, b[1:0], 2'b0}``.

        Returns a list of ``(sig_name, vec_bits)`` tuples, skipping constants
        and operators (``~``, ``!``, etc.).

        Port of the concatenation handling in ``verilog-read-sub-decls-expr``.
        """
        # Find the full expression within parens
        depth = 1
        i = 0
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            return []
        expr = text[:i - 1].strip()
        if not expr.startswith("{"):
            return []

        # Strip outer braces
        inner = expr[1:]
        if inner.endswith("}"):
            inner = inner[:-1]

        # Split by commas (respecting nested braces/parens)
        parts: list[str] = []
        current = ""
        nest = 0
        for ch in inner:
            if ch in "({":
                nest += 1
                current += ch
            elif ch in ")}":
                nest -= 1
                current += ch
            elif ch == "," and nest == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())

        results: list[tuple[str, Optional[str]]] = []
        sig_re = re.compile(r"~?([a-zA-Z_][a-zA-Z_0-9]*)(\[[^\]]+\])?$")
        for part in parts:
            part = part.strip()
            # Skip constants like 2'b0, 1'h0, etc.
            if not part or re.match(r"\d+'", part):
                continue
            # Strip outer parentheses (e.g., "(u0)" -> "u0")
            if part.startswith("(") and part.endswith(")"):
                part = part[1:-1].strip()
            m = sig_re.match(part)
            if m:
                results.append((m.group(1), m.group(2)))
        return results

    # ------------------------------------------------------------------
    # Context reading helpers (direct text parsing, avoids Emacs-regex
    # translation issues in InstParser)
    # ------------------------------------------------------------------

    def _read_inst_module(self, text: str, pos: int) -> Optional[str]:
        """Read the instantiated module name from context around *pos*.

        Scans backward from the ``/*AUTOINST*/`` or ``.*`` marker to find
        the pattern ``ModuleName [#(...)] InstanceName (``.
        """
        # Find the opening paren of the instantiation
        open_paren = self._find_open_paren_backward(text, pos)
        if open_paren is None:
            return None

        pre = text[:open_paren].rstrip()

        # The instance name is the last identifier before (
        # But there might be #(...) parameter block before (
        # Pattern: ModuleName [#(params)] InstName (
        # Walk backward to find identifiers

        # Skip trailing whitespace and line comments
        idx = len(pre) - 1
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return None

        # Skip optional array range [...] (arrayed instances)
        if idx >= 0 and pre[idx] == "]":
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx < 0:
                return None

        # Read instance name backward
        inst_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        inst_name = pre[idx + 1:inst_end]

        # Skip whitespace and comments
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return inst_name  # Only one identifier — it's the module name

        # Check for #(...) parameter block
        if pre[idx] == ")":
            # Skip backward over #(...)
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == ")":
                    depth += 1
                elif pre[idx] == "(":
                    depth -= 1
                idx -= 1
            # Skip # character
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx >= 0 and pre[idx] == "#":
                idx -= 1
            # Skip whitespace
            idx = self._skip_ws_and_comments_backward(pre, idx)

        # Now read the module name
        mod_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        mod_name = pre[idx + 1:mod_end]

        return mod_name if mod_name else None

    def _read_inst_name(self, text: str, pos: int) -> Optional[str]:
        """Read the instance name from context around *pos*."""
        open_paren = self._find_open_paren_backward(text, pos)
        if open_paren is None:
            return None

        pre = text[:open_paren].rstrip()
        idx = len(pre) - 1
        idx = self._skip_ws_and_comments_backward(pre, idx)
        if idx < 0:
            return None

        # Skip optional array range [...] (arrayed instances)
        if idx >= 0 and pre[idx] == "]":
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            idx = self._skip_ws_and_comments_backward(pre, idx)
            if idx < 0:
                return None

        inst_end = idx + 1
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1
        return pre[idx + 1:inst_end] or None

    def _read_inst_param_value(self, text: str, pos: int) -> list[tuple[str, str]]:
        """Read parameter value overrides from ``#(...)`` syntax.

        Scans backward from the ``/*AUTOINST*/`` marker to find ``#(...)``
        and parse ``.param(value)`` entries.
        """
        # Find the opening ( of the instantiation
        open_paren = self._find_open_paren_backward(text, pos)
        if open_paren is None:
            return []

        pre = text[:open_paren].rstrip()
        idx = len(pre) - 1

        # Skip whitespace
        while idx >= 0 and pre[idx] in " \t\n\r\f":
            idx -= 1
        if idx < 0:
            return []

        # Skip instance name (and optional array range [...])
        if pre[idx] == "]":
            # Skip array range
            depth = 1
            idx -= 1
            while idx >= 0 and depth > 0:
                if pre[idx] == "]":
                    depth += 1
                elif pre[idx] == "[":
                    depth -= 1
                idx -= 1
            # Skip whitespace
            while idx >= 0 and pre[idx] in " \t\n\r\f":
                idx -= 1

        # Skip instance name
        while idx >= 0 and re.match(r"[a-zA-Z0-9`_$]", pre[idx]):
            idx -= 1

        # Skip whitespace and comments (AUTOINSTPARAM may leave
        # ``// Templated`` on the closing line of the param block)
        idx = self._skip_ws_and_comments_backward(pre, idx)

        # Check for ) of parameter block
        if idx < 0 or pre[idx] != ")":
            return []

        # Find matching ( for the #(...) block
        param_close = idx
        depth = 1
        idx -= 1
        while idx >= 0 and depth > 0:
            if pre[idx] == ")":
                depth += 1
            elif pre[idx] == "(":
                depth -= 1
            idx -= 1
        if depth != 0:
            return []
        param_open = idx + 1

        param_text = pre[param_open + 1:param_close]
        params: list[tuple[str, str]] = []

        # Parse .param(value) patterns
        param_re = re.compile(r"\.([a-zA-Z0-9`_$]+)\s*\(")
        p = 0
        while p < len(param_text):
            m = param_re.search(param_text, p)
            if m is None:
                break

            param_name = m.group(1)
            paren_start = m.end()

            # Find matching )
            depth = 1
            i = paren_start
            while i < len(param_text) and depth > 0:
                if param_text[i] == "(":
                    depth += 1
                elif param_text[i] == ")":
                    depth -= 1
                i += 1

            if depth == 0:
                param_value = param_text[paren_start:i - 1].strip()
                param_value = re.sub(r"\s+", "", param_value)
                params.append((param_name, param_value))

            p = i

        return params

    # ------------------------------------------------------------------
    # Module boundary helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _skip_ws_and_comments_backward(text: str, idx: int) -> int:
        """Skip whitespace and line comments when reading backward.

        Handles both full-line comments and inline comments.
        """
        while idx >= 0:
            if text[idx] in " \t\r\f":
                idx -= 1
            elif text[idx] == "\n":
                idx -= 1
            else:
                # Check if current position is inside a // comment
                line_start = text.rfind("\n", 0, idx + 1)
                line_start = 0 if line_start < 0 else line_start + 1
                line_before = text[line_start:idx + 1]
                comment_pos = line_before.find("//")
                if comment_pos >= 0:
                    idx = line_start + comment_pos - 1
                    continue
                break
        return idx

    @staticmethod
    def _find_beg_of_defun(text: str, pos: int) -> int:
        """Find the ``module`` keyword position before *pos*."""
        last = 0
        for m in _MODULE_KW_RE.finditer(text, 0, pos + 1):
            last = m.start()
        return last

    @staticmethod
    def _find_end_of_defun(text: str, pos: int) -> int:
        """Find the ``endmodule`` position after *pos*."""
        m = _END_MODULE_KW_RE.search(text, pos)
        return m.end() if m else len(text)

    @staticmethod
    def _find_open_paren_backward(text: str, pos: int) -> Optional[int]:
        """Find the ``(`` that opens the instantiation containing *pos*."""
        depth = 1
        i = pos - 1
        while i >= 0 and depth > 0:
            ch = text[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
            i -= 1
        return (i + 1) if depth == 0 else None

    @staticmethod
    def _find_close_paren(text: str, open_pos: int) -> Optional[int]:
        """Find the matching ``)`` for ``(`` at *open_pos*."""
        depth = 0
        i = open_pos
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
