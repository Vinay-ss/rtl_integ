"""AutoInst / AutoInstParam — expand AUTOINST and AUTOINSTPARAM markers.

Ported from ``verilog-auto-inst`` (lines 12470–12920) and
``verilog-auto-inst-param`` (lines 12921–13027) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from ..regex_compat import translate_emacs_regex
from ..signal import Signal, signals_not_in, signals_matching_regexp

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase
    from ..parser.template_parser import TemplateResult


# Gate-level primitive keywords that don't need library lookup
_GATE_KEYWORDS = {
    "and", "buf", "bufif0", "bufif1", "cmos", "nand", "nmos", "nor",
    "not", "notif0", "notif1", "or", "pmos", "pulldown", "pullup",
    "rcmos", "rnmos", "rpmos", "rtran", "rtranif0", "rtranif1",
    "tran", "tranif0", "tranif1", "xnor", "xor",
}


class AutoInst:
    """Expand ``/*AUTOINST*/`` markers in a :class:`VerilogBuffer`."""

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

    def expand(self) -> None:
        """Expand ``/*AUTOINST*/`` at current point.

        Point must be positioned right after the ``/*AUTOINST*/`` marker
        (or ``/*AUTOINST(regexp)*/``).
        """
        self._expand_common(is_param=False)

    # ------------------------------------------------------------------
    # Core expansion logic (shared with AUTOINSTPARAM)
    # ------------------------------------------------------------------

    def _expand_common(self, is_param: bool = False) -> None:
        """Core expansion for both AUTOINST and AUTOINSTPARAM."""
        from ..parser.inst_parser import InstParser
        from ..parser.template_parser import TemplateParser

        pt = self._buf.point()

        # Read optional regexp parameter from the marker text
        regexp = self._read_auto_params(pt)

        # Check for .* (for-star mode)
        for_star = False
        if not is_param:
            text = self._buf.buffer_string()
            if pt >= 2 and text[pt - 2:pt] == ".*":
                for_star = True

        # Calculate indentation: column after the opening (
        indent_pt = self._find_indent_pt()

        # Effective auto-inst-column: max(config, 16 + next-tab-stop)
        auto_inst_column = max(
            self._config.auto_inst_column,
            16 + 8 * ((indent_pt + 7) // 8),
        )

        # First-port flags
        first_any = True
        first_section = True

        # Read instantiation context
        iparser = InstParser(self._buf, self._config)

        if is_param:
            # For AUTOINSTPARAM, we need to find the inst module by
            # looking forward to the actual instantiation ( or ;
            with self._buf.save_excursion():
                self._buf.re_search_forward(r"[(;]")
                submod = iparser.read_inst_module()
                inst = iparser.read_inst_name()
        else:
            submod = iparser.read_inst_module()
            inst = iparser.read_inst_name()

        if not submod:
            return

        # Read already-connected pins (skip-pins)
        skip_pins = iparser.read_inst_pins()
        skip_pin_names = [Signal(name=p[0]) for p in skip_pins]

        # Read parameter values if configured
        par_values: list[tuple[str, str]] = []
        if self._config.auto_inst_param_value and not is_param:
            par_values = iparser.read_inst_param_value()
            # Resolve parameter values against each other and evaluate
            if par_values:
                par_values = self._resolve_param_values(par_values)

        # Gate primitives don't need library lookup
        if submod in _GATE_KEYWORDS:
            return

        # Lookup submodule
        submodi = self._db.lookup(submod, ignore_error=True)
        if submodi is None:
            return
        submoddecls = self._db.get_decls(submodi)

        # Get current module declarations (for auto_inst_vector check)
        moddecls = self._get_current_moddecls()

        # Read AUTO_TEMPLATE
        tparser = TemplateParser(self._buf, self._config)
        tpl_result = tparser.parse(submod)

        # Extract instance number from template regexp
        # The regexp from the template parser is in Emacs regex syntax.
        # tpl_num is a string: the first capture group from matching
        # the template regexp against the instance name.
        tpl_num = ""
        if tpl_result.regexp and inst:
            try:
                py_regexp = translate_emacs_regex(tpl_result.regexp)
                m = re.search(py_regexp, inst, re.IGNORECASE if self._config.case_fold else 0)
                if m and m.lastindex and m.lastindex >= 1:
                    tpl_num = m.group(1)
            except re.error:
                pass

        # Build the port text
        lines: list[str] = []

        if is_param:
            # AUTOINSTPARAM: only parameters
            sig_list = signals_not_in(submoddecls.gparams, skip_pin_names)
            if regexp:
                sig_list = self._filter_regexp(sig_list, regexp)
            if sig_list:
                section_lines = self._format_section(
                    "// Parameters\n", sig_list, indent_pt, auto_inst_column,
                    moddecls, submoddecls, tpl_result, tpl_num,
                    for_star, par_values, first_any, first_section,
                    inst_name=inst or "", submod_name=submod or "",
                )
                lines.extend(section_lines)
                first_any = False
                first_section = True
        else:
            # AUTOINST: Interfaced, Interfaces, Outputs, Inouts, Inputs
            sections = []

            # Interfaced (interface vars)
            if self._config.auto_inst_interfaced_ports and submodi.type == "interface":
                sig_list = signals_not_in(submoddecls.vars, skip_pin_names)
                if regexp:
                    sig_list = self._filter_regexp(sig_list, regexp)
                if sig_list:
                    sections.append(("// Interfaced\n", sig_list))

            # Interfaces
            sig_list = signals_not_in(submoddecls.interfaces, skip_pin_names)
            if regexp:
                sig_list = self._filter_regexp(sig_list, regexp)
            if sig_list:
                sections.append(("// Interfaces\n", sig_list))

            # Outputs
            sig_list = signals_not_in(submoddecls.outputs, skip_pin_names)
            if regexp:
                sig_list = self._filter_regexp(sig_list, regexp)
            if sig_list:
                sections.append(("// Outputs\n", sig_list))

            # Inouts
            sig_list = signals_not_in(submoddecls.inouts, skip_pin_names)
            if regexp:
                sig_list = self._filter_regexp(sig_list, regexp)
            if sig_list:
                sections.append(("// Inouts\n", sig_list))

            # Inputs
            sig_list = signals_not_in(submoddecls.inputs, skip_pin_names)
            if regexp:
                sig_list = self._filter_regexp(sig_list, regexp)
            if sig_list:
                sections.append(("// Inputs\n", sig_list))

            for section_label, sigs in sections:
                section_lines = self._format_section(
                    section_label, sigs, indent_pt, auto_inst_column,
                    moddecls, submoddecls, tpl_result, tpl_num,
                    for_star, par_values, first_any, first_section,
                    inst_name=inst or "", submod_name=submod or "",
                )
                lines.extend(section_lines)
                first_any = False
                first_section = True

        if not lines:
            return

        # Join all port lines
        text_block = "".join(lines)

        # Replace the final comma with "); or )
        # Find the last comma in text_block
        last_comma = text_block.rfind(",")
        if last_comma >= 0:
            if is_param:
                # AUTOINSTPARAM: close with )
                close_str = ")"
            else:
                # AUTOINST: close with );
                close_str = ");"

            # Check if there's a comment right after the comma
            after_comma = text_block[last_comma + 1:]
            # If there's "  // comment\n" after comma, replace ", " with "); "
            m_after = re.match(r"( +)(//.*)\n$", after_comma)
            if m_after:
                # Replace comma with close, keep one less space for alignment
                spaces = m_after.group(1)
                comment = m_after.group(2)
                if len(spaces) > 1:
                    spaces = spaces[1:]  # Remove one space so ); fits
                text_block = text_block[:last_comma] + close_str + spaces + comment + "\n"
            else:
                text_block = text_block[:last_comma] + close_str + after_comma

        # Remove trailing newline (will be handled by existing text)
        if text_block.endswith("\n"):
            text_block = text_block[:-1]

        # Insert the expanded text
        # In for-star mode (.*), insert a comma after .* if ports follow
        if for_star and lines:
            # Check if .* already has a comma after it
            text = self._buf.buffer_string()
            pt = self._buf.point()
            # Look backward from current position to find .*
            before = text[:pt]
            # Strip whitespace/newlines to see if last non-ws is *
            stripped = before.rstrip()
            if stripped.endswith("*") and not stripped.endswith(","):
                # Insert comma after .*
                self._buf.insert(",")
        self._buf.insert("\n" + text_block)

        # Clean up: remove user-provided ) and ; that follow
        self._cleanup_close(is_param)

    # ------------------------------------------------------------------
    # Section formatting
    # ------------------------------------------------------------------

    def _format_section(
        self,
        section_label: str,
        sig_list: list[Signal],
        indent_pt: int,
        auto_inst_column: int,
        moddecls,
        submoddecls,
        tpl_result: "TemplateResult",
        tpl_num: str,
        for_star: bool,
        par_values: list[tuple[str, str]],
        first_any: bool,
        first_section: bool,
        inst_name: str = "",
        submod_name: str = "",
    ) -> list[str]:
        """Format a section of ports (e.g. // Outputs) into lines."""
        from ..parser.template_parser import TemplateParser

        lines: list[str] = []

        # Determine the direction from section_label
        section_lbl = section_label.lower()
        if "output" in section_lbl:
            vl_dir = "output"
        elif "inout" in section_lbl:
            vl_dir = "inout"
        elif "input" in section_lbl:
            vl_dir = "input"
        elif "interface" in section_lbl:
            vl_dir = "interface"
        elif "parameter" in section_lbl:
            vl_dir = "parameter"
        else:
            vl_dir = ""

        if self._config.auto_inst_sort:
            sig_list = sorted(sig_list, key=lambda s: s.name.lower())

        for port in sig_list:
            port_name = port.name
            tparser = TemplateParser(self._buf, self._config)

            # Compute default connection
            vl_bits = port.bits or ""
            vl_mbits = ""
            if port.multidim:
                vl_mbits = "".join(port.multidim)
            vl_memory = port.memory or ""
            vl_modport = port.modport or ""

            # Apply parameter substitutions to bits
            if par_values:
                for pname, pval in par_values:
                    if pval == pname:
                        continue  # skip self-referential (e.g. .WIDTH(WIDTH))
                    # Simple values (numbers, identifiers) don't need parens
                    repl = pval if re.fullmatch(r"[\w`$.]+", pval) else f"({pval})"
                    pat = r"\b" + re.escape(pname) + r"\b"
                    if vl_bits:
                        vl_bits = re.sub(pat, repl, vl_bits)
                    if vl_mbits:
                        vl_mbits = re.sub(pat, repl, vl_mbits)
                    if vl_memory:
                        vl_memory = re.sub(pat, repl, vl_memory)
                # Evaluate constant expressions after substitution
                if vl_bits:
                    from ..signal import simplify_range
                    simplified = simplify_range(vl_bits)
                    if simplified != vl_bits:
                        vl_bits = simplified
                if vl_mbits:
                    from ..signal import simplify_range as _sr
                    # Simplify each dimension in mbits
                    parts = re.findall(r"\[[^\]]*\]", vl_mbits)
                    if parts:
                        vl_mbits = "".join(_sr(p) for p in parts)

            # Compute auto_inst_vector: whether to show bits
            auto_inst_vector = self._compute_auto_inst_vector(
                port, vl_bits, moddecls,
            )

            # Default bits for the connection
            if (port.bits and port.multidim) or port.memory:
                mem_part = ""
                if vl_memory:
                    mem_part = "." + vl_memory
                dflt_bits = f"/*{vl_mbits}{vl_bits}{mem_part}*/"
            else:
                dflt_bits = auto_inst_vector

            # Default net = port + modport + bits
            modport_suffix = ""
            if vl_modport:
                modport_suffix = f".{vl_modport}"

            tpl_net = f"{port_name}{modport_suffix}{dflt_bits}"

            # Build lisp evaluation context for @"..." substitution
            vl_width = self._compute_vl_width(vl_bits)
            vl_context = {
                "vl-cell-name": inst_name,
                "vl-cell-type": submod_name,
                "vl-name": port_name,
                "vl-dir": vl_dir,
                "vl-bits": vl_bits,
                "vl-mbits": vl_mbits,
                "vl-width": vl_width,
                "vl-memory": vl_memory,
            }
            # Add vh-* variables from defines AND bare names
            # AUTO_LISP (setq num 1) sets bare "num"; verilog-read-defines
            # sets "vh-PARAM".  Templates can reference either form.
            for dname, dval in self._config.defines.items():
                if not dname.startswith("vh-"):
                    vl_context[f"vh-{dname}"] = dval
                vl_context[dname] = dval
            # Add vh-* variables from current module's parameters
            # Scan the buffer for parameter assignments like "parameter foo = 1"
            buf_text = self._buf.buffer_string()
            for sig in moddecls.gparams:
                param_re = re.compile(
                    r"\bparameter\b[^;]*\b" + re.escape(sig.name)
                    + r"\s*=\s*([^;,\s]+)", re.IGNORECASE
                )
                pm = param_re.search(buf_text)
                if pm:
                    vl_context[f"vh-{sig.name}"] = pm.group(1)

            # Try template match
            tpl_ass = tparser.get_entry(port_name, tpl_result)
            tpl_matched = False

            if tpl_ass is not None:
                # Exact or wildcard template match
                applied = tparser.apply(port, tpl_result, tpl_num, vl_context)
                if applied is not None:
                    tpl_net = applied
                    tpl_matched = True
            elif tpl_result.wild_list:
                # Try wildcard
                applied = tparser.apply(port, tpl_result, tpl_num, vl_context)
                if applied is not None:
                    tpl_net = applied
                    tpl_matched = True
                    tpl_ass = tparser.get_entry(port_name, tpl_result)

            # Check if template is required and no match
            if not tpl_matched and getattr(self._config, 'auto_inst_template_required', False):
                continue

            # Build port line
            line_parts = []

            # Handle first-any: insert newline + possible comma
            if first_any:
                # Will be handled by the caller inserting \n
                pass

            # Section header
            if first_section:
                first_section = False
                header = " " * indent_pt + section_label
                lines.append(header)

            # Port line: indent, .port, pad, (net),
            port_str = " " * indent_pt + f".{port_name}"

            # Check for dot-name shorthand
            if self._config.auto_inst_dot_name and port_name == tpl_net:
                port_str += ","
            else:
                # Pad to auto_inst_column
                pad_len = auto_inst_column - len(port_str)
                if pad_len > 0:
                    port_str += " " * pad_len
                port_str += f"({tpl_net}),"

            # Add comment if templated or for-star
            if tpl_matched and tpl_ass:
                comment_col = auto_inst_column + (24 if auto_inst_column < 48 else 16)
                comment_pad = comment_col - len(port_str)
                if comment_pad < 1:
                    comment_pad = 1
                tpl_comment = " // Templated"
                if self._config.auto_inst_template_numbers == "lhs":
                    # Use original Emacs-form regex if available
                    lhs = tpl_ass.original_pattern or tpl_ass.port_pattern
                    tpl_comment += f" LHS: {lhs}"
                elif self._config.auto_inst_template_numbers:
                    tpl_comment += f" {tpl_ass.lineno}"
                if tpl_ass.autonohookup:
                    tpl_comment += " AUTONOHOOKUP"
                port_str += " " * comment_pad + tpl_comment
            elif for_star:
                comment_col = auto_inst_column + (24 if auto_inst_column < 48 else 16)
                comment_pad = comment_col - len(port_str)
                if comment_pad < 1:
                    comment_pad = 1
                port_str += " " * comment_pad + " // Implicit .*"

            lines.append(port_str + "\n")

        return lines

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_indent_pt(self) -> int:
        """Find the column of the character after the opening ``(``."""
        with self._buf.save_excursion():
            # Search backward for the opening paren
            text = self._buf.buffer_string()
            pos = self._buf.point()
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

            # Calculate column of (  + 1
            col = 0
            j = open_paren
            while j > 0 and text[j - 1] != "\n":
                j -= 1
            # Count columns from start of line to open_paren
            for k in range(j, open_paren):
                if text[k] == "\t":
                    col = (col + 8) & ~7
                else:
                    col += 1
            return col + 1  # column after the (

    def _read_auto_params(self, pt: int) -> Optional[str]:
        """Read optional regexp from AUTOINST(regexp) or AUTOINSTPARAM(regexp)."""
        text = self._buf.buffer_string()
        # Look backward for the /*AUTO...*/ marker
        marker_start = text.rfind("/*AUTO", 0, pt)
        if marker_start < 0:
            # Try .* marker
            return None
        marker_end = text.find("*/", marker_start)
        if marker_end < 0:
            return None
        marker = text[marker_start:marker_end + 2]

        # Extract regexp from e.g. /*AUTOINST("regexp")*/ or /*AUTOINST(regexp)*/
        m = re.search(r'\(\"(.+?)\"\)', marker)
        if m:
            return m.group(1)
        m = re.search(r'\((.+?)\)', marker)
        if m:
            val = m.group(1)
            if val.startswith('"') and val.endswith('"'):
                return val[1:-1]
            return val
        return None

    @staticmethod
    def _resolve_param_values(
        par_values: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        """Resolve parameter values against each other and evaluate constants.

        E.g. ``[("VEC_W", "8"), ("IDX_W", "$clog2(VEC_W)")]``
        becomes ``[("VEC_W", "8"), ("IDX_W", "3")]``.
        """
        from ..signal import _eval_const_expr

        resolved: dict[str, str] = {}
        for pname, pval in par_values:
            # Substitute already-resolved params into this value
            for rpname, rpval in resolved.items():
                pval = re.sub(r"\b" + re.escape(rpname) + r"\b", rpval, pval)
            # Try to evaluate to a constant
            result = _eval_const_expr(pval)
            if result is not None:
                resolved[pname] = str(result)
            else:
                resolved[pname] = pval
        return list(resolved.items())

    @staticmethod
    def _compute_vl_width(vl_bits: str) -> str:
        """Compute vl-width (the integer width) from a bit range like ``[7:0]``."""
        if not vl_bits:
            return "1"
        m = re.match(r"\[(\d+):(\d+)\]", vl_bits)
        if m:
            hi, lo = int(m.group(1)), int(m.group(2))
            return str(abs(hi - lo) + 1)
        m = re.match(r"\[(\d+)\]", vl_bits)
        if m:
            return str(int(m.group(1)) + 1)
        # Can't compute — return the expression
        return ""

    def _compute_auto_inst_vector(
        self,
        port: Signal,
        vl_bits: str,
        moddecls,
    ) -> str:
        """Determine whether to include vector bits in the connection."""
        if self._config.auto_inst_vector is True:
            return vl_bits
        if self._config.auto_inst_vector == "unsigned":
            if port.signed:
                return ""  # Don't show bits for signed ports
            return vl_bits
        # Check if port exists in current module with same bits
        all_sigs = (
            moddecls.outputs + moddecls.inouts + moddecls.inputs +
            moddecls.vars + moddecls.consts + moddecls.gparams
        )
        for sig in all_sigs:
            if sig.name == port.name:
                if sig.bits == port.bits:
                    return ""
                return vl_bits
        return vl_bits

    def _get_current_moddecls(self):
        """Get ModDecls for the current module being edited."""
        from ..parser.decl_parser import DeclParser
        from ..parser.inst_parser import InstParser
        from ..signal import ModDecls

        iparser = InstParser(self._buf, self._config)
        with self._buf.save_excursion():
            mod_name = iparser.read_module_name()

        if not mod_name:
            return ModDecls()

        # Parse declarations of the current module
        with self._buf.save_excursion():
            # Find the module keyword and parse from there
            text = self._buf.buffer_string()
            # Search backward for module/interface keyword
            pos = self._buf.point()
            mod_re = re.compile(r"\b(?:module|interface|program)\b")
            last_match = None
            for m in mod_re.finditer(text, 0, pos):
                last_match = m
            if last_match:
                # Find the opening ( or ; after module name
                rest = text[last_match.end():]
                paren_m = re.search(r"[;(]", rest)
                if paren_m:
                    parse_pt = last_match.end() + paren_m.end()
                    self._buf.goto_char(parse_pt)
                    parser = DeclParser(self._buf, self._config)
                    return parser.parse()

        return ModDecls()

    def _cleanup_close(self, is_param: bool) -> None:
        """Remove user-provided ) and ; after the insertion point."""
        text = self._buf.buffer_string()
        pos = self._buf.point()

        # Skip whitespace
        while pos < len(text) and text[pos] in " \t\n\r\f":
            pos += 1

        # Remove ) if present
        if pos < len(text) and text[pos] == ")":
            self._buf.goto_char(pos)
            self._buf.delete_char(1)
            text = self._buf.buffer_string()
            pos = self._buf.point()

        # For AUTOINST, also remove ;
        if not is_param:
            while pos < len(text) and text[pos] in " \t\n\r\f":
                pos += 1
            if pos < len(text) and text[pos] == ";":
                self._buf.goto_char(pos)
                self._buf.delete_char(1)

    @staticmethod
    def _filter_regexp(sig_list: list[Signal], regexp: str) -> list[Signal]:
        """Filter signals by regexp, supporting ?! exclusion prefix."""
        if regexp.startswith("?!"):
            # Exclusion
            pat = re.compile(regexp[2:])
            return [s for s in sig_list if not pat.search(s.name)]
        else:
            return signals_matching_regexp(sig_list, regexp)


class AutoInstParam:
    """Expand ``/*AUTOINSTPARAM*/`` markers in a :class:`VerilogBuffer`."""

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._inst = AutoInst(buf, config, db)

    def expand(self) -> None:
        """Expand ``/*AUTOINSTPARAM*/`` at current point."""
        self._inst._expand_common(is_param=True)
