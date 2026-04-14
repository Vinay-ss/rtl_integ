"""TemplateParser — parse AUTO_TEMPLATE blocks.

Ported from ``verilog-read-auto-template`` and
``verilog-read-auto-template-middle`` (around lines 10187–10300 of
``verilog-mode.el``).

AUTO_TEMPLATE syntax::

    /* SubModuleName AUTO_TEMPLATE (
        .port_name  (signal_name[]),
        .other_port (other_signal@),
        ); */

Where ``@`` is replaced by the instance number and ``[]`` by the
port's bit range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..signal import Signal


@dataclass
class TemplateEntry:
    """One template mapping: port pattern → signal expression."""

    port_pattern: str       # exact port name or regex (with ^ and $)
    signal_expr: str        # the connection expression (may contain @ or [])
    templateno: int = 0     # which template (1-based counter)
    lineno: int = 0         # line number within template
    autonohookup: bool = False  # has AUTONOHOOKUP annotation
    is_wildcard: bool = False   # is this a regex (wildcard) pattern?
    original_pattern: str = ""  # original Emacs-form regex for LHS comments


@dataclass
class TemplateResult:
    """Result from parsing a template block."""

    regexp: str                           # instance-name matching regexp
    sig_list: list[TemplateEntry] = field(default_factory=list)   # exact port matches
    wild_list: list[TemplateEntry] = field(default_factory=list)  # wildcard port matches


class TemplateParser:
    """Parse AUTO_TEMPLATE blocks for a given module."""

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self._buf = buf
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, module_name: str) -> TemplateResult:
        """Search backward (then forward) from point for an AUTO_TEMPLATE
        for *module_name*.  Return a :class:`TemplateResult`.

        Port of ``verilog-read-auto-template``.
        """
        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Build search pattern:  optional leading /*  module  AUTO_TEMPLATE
        # The elisp searches for:
        #   ^\\s-*/?\\*?\\s-*MODULE\\s+AUTO_TEMPLATE
        pat = re.compile(
            r"^\s*/?\*?\s*" + re.escape(module_name) + r"\s+AUTO_TEMPLATE",
            re.MULTILINE,
        )

        # Search backward from point first
        found_pos = None
        for m in pat.finditer(text, 0, pt):
            found_pos = m.end()  # take the last match before point

        # If not found backward, try forward
        if found_pos is None:
            m = pat.search(text, pt)
            if m:
                found_pos = m.end()

        if found_pos is None:
            return TemplateResult(regexp="")

        return self._parse_middle(text, found_pos)

    def apply(
        self,
        port: "Signal",
        result: TemplateResult,
        tpl_num: str = "0",
        vl_context: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """Return the signal expression for *port*, or ``None`` if no
        template matches.

        *tpl_num* is the string captured from matching the template
        regexp against the instance name (e.g. ``"3"`` from ``inst3``
        or ``"foo"`` from ``u_foo``).

        *vl_context* is a dict of Emacs-Lisp variable bindings used to
        evaluate ``@"expr"`` patterns (e.g. ``vl-cell-name``,
        ``vl-width``, ``vh-*``).

        Searches exact matches first (``sig_list``), then wildcard
        matches (``wild_list``).
        """
        port_name = port.name

        # Check exact matches
        for entry in result.sig_list:
            if entry.port_pattern == port_name:
                return self._substitute(entry.signal_expr, port, tpl_num, result, vl_context)

        # Check wildcard matches
        for entry in result.wild_list:
            m = re.match(entry.port_pattern, port_name)
            if m:
                # Replace \1, \2, etc. in signal_expr with match groups
                expr = entry.signal_expr
                try:
                    expr = m.expand(expr)
                except (re.error, IndexError):
                    # If expand fails, do manual replacement
                    for i in range(len(m.groups()), 0, -1):
                        expr = expr.replace(f"\\{i}", m.group(i) or "")
                return self._substitute(expr, port, tpl_num, result, vl_context)

        return None

    def get_entry(
        self,
        port_name: str,
        result: TemplateResult,
    ) -> Optional[TemplateEntry]:
        """Return the matching :class:`TemplateEntry` for *port_name*,
        or ``None`` if no template matches."""
        for entry in result.sig_list:
            if entry.port_pattern == port_name:
                return entry
        for entry in result.wild_list:
            if re.match(entry.port_pattern, port_name):
                return entry
        return None

    # ------------------------------------------------------------------
    # Internal: parse the template body
    # ------------------------------------------------------------------

    def _parse_middle(self, text: str, pos: int) -> TemplateResult:
        """Port of ``verilog-read-auto-template-middle``.

        *pos* points just after ``AUTO_TEMPLATE`` in the text.
        """
        n = len(text)

        # Default regexp for matching instance names: captures a number
        # Stored in Emacs regex syntax (grouping = \( \))
        tpl_regexp = r"\([0-9]+\)"
        lineno = -1  # -1 to offset for the AUTO_TEMPLATE's newline
        templateno = 0

        # Parse optional "REGEXP" after AUTO_TEMPLATE
        m = re.match(r"\s*\"([^\"]*)\"", text[pos:])
        if m:
            tpl_regexp = m.group(1)
            pos += m.end()

        # Skip subsequent AUTO_TEMPLATE headers (multi-template syntax).
        # Multiple module names can share one template body:
        #   ModA AUTO_TEMPLATE "regexp"
        #   ModB AUTO_TEMPLATE "regexp" (
        #     .port (sig),
        #   );
        _MULTI_TPL_RE = re.compile(
            r"\s*\n\s*/?\*?\s*\w+\s+AUTO_TEMPLATE(?:\s*\"[^\"]*\")?",
        )
        while True:
            mm = _MULTI_TPL_RE.match(text[pos:])
            if mm:
                pos += mm.end()
            else:
                break

        # Find the opening ( of the template port list
        paren_pos = text.find("(", pos)
        if paren_pos < 0:
            return TemplateResult(regexp=tpl_regexp)
        pos = paren_pos + 1

        # Count template number and line numbers if needed
        if self._config.auto_inst_template_numbers:
            # Count how many AUTO_TEMPLATE markers precede this one
            pre_text = text[:pos]
            templateno = pre_text.count("AUTO_TEMPLATE")
            # Count lines from start of file to pos
            lineno = pre_text[:paren_pos].count("\n") - text[:paren_pos].rfind("AUTO_TEMPLATE")
            lineno = -1  # reset; we'll count within the template
            # Count lines from beginning of file to opening paren
            count_from = 0
            count_to = paren_pos
            for tm in re.finditer(r"AUTO_TEMPLATE", text[:pos]):
                templateno_candidate = 0
            templateno = text[:pos].count("AUTO_TEMPLATE")
            # Reset lineno — count from start of file to template opening
            lineno = text[:pos].count("\n")
            # We want lines relative to the file, like the elisp does
            lineno = 0  # will be incremented per-line below

        # Find the matching close paren for the template
        tpl_end = self._find_close_paren(text, paren_pos)
        if tpl_end is None:
            return TemplateResult(regexp=tpl_regexp)
        tpl_end -= 1  # position of the )

        sig_list: list[TemplateEntry] = []
        wild_list: list[TemplateEntry] = []

        # Exact port pattern:  .portname(connection),  or .portname(connection);
        _EXACT_RE = re.compile(
            r"\s*\.([a-zA-Z0-9`_$]+)\s*\((.*)\)\s*(?:,|(?:\)\s*;))"
        )

        # Wildcard port pattern (port name contains regex chars like .* @ [] etc.)
        _WILD_RE = re.compile(
            r"\s*\.((?:[-a-zA-Z0-9`_$+@^.*?|]|[\[\]]|\\[()|\d])+)\s*\((.*)\)\s*(?:,|(?:\)\s*;))"
        )

        while pos < tpl_end:
            if pos >= n:
                break
            ch = text[pos]

            # Whitespace (not newline)
            if ch in " \t\f":
                m = re.match(r"[ \t\f]+", text[pos:])
                if m:
                    pos += m.end()
                continue

            # Newline
            if ch == "\n":
                lineno += 1
                pos += 1
                continue

            # Line comment
            if text[pos:pos + 2] == "//":
                eol = text.find("\n", pos)
                if eol >= 0:
                    lineno += 1
                    pos = eol + 1
                else:
                    pos = n
                continue

            # Block comment
            if text[pos:pos + 2] == "/*":
                close = text.find("*/", pos + 2)
                if close >= 0:
                    pos = close + 2
                else:
                    pos = n
                continue

            # Port mapping line: .portname(connection)
            if ch == ".":
                # Try exact match first
                line_end = text.find("\n", pos)
                if line_end < 0:
                    line_end = n
                line_text = text[pos:line_end]

                em = _EXACT_RE.match(line_text)
                if em:
                    port_name = em.group(1)
                    connection = em.group(2).strip()
                    # Check for AUTONOHOOKUP after the match
                    after = line_text[em.end():]
                    nohookup = "AUTONOHOOKUP" in after or "AUTONOHOOKUP" in line_text[em.start():]
                    # Check rest of line after match for AUTONOHOOKUP
                    rest_after = text[pos + em.end():line_end]
                    nohookup = "AUTONOHOOKUP" in rest_after

                    # Check if port_name contains wildcard chars
                    if re.search(r"[@*?|\\]|\.\*", port_name):
                        # It's actually a wildcard pattern
                        wild_pattern = self._make_wild_pattern(port_name)
                        emacs_pat = self._make_emacs_pattern(port_name)
                        wild_list.append(TemplateEntry(
                            port_pattern=wild_pattern,
                            signal_expr=connection,
                            templateno=templateno,
                            lineno=lineno,
                            autonohookup=nohookup,
                            is_wildcard=True,
                            original_pattern=emacs_pat,
                        ))
                    else:
                        sig_list.append(TemplateEntry(
                            port_pattern=port_name,
                            signal_expr=connection,
                            templateno=templateno,
                            lineno=lineno,
                            autonohookup=nohookup,
                        ))
                    pos += em.end()
                    continue

                # Try wildcard match
                wm = _WILD_RE.match(line_text)
                if wm:
                    port_pat_raw = wm.group(1)
                    connection = wm.group(2).strip()
                    rest_after = text[pos + wm.end():line_end]
                    nohookup = "AUTONOHOOKUP" in rest_after

                    wild_pattern = self._make_wild_pattern(port_pat_raw)
                    emacs_pat = self._make_emacs_pattern(port_pat_raw)
                    wild_list.append(TemplateEntry(
                        port_pattern=wild_pattern,
                        signal_expr=connection,
                        templateno=templateno,
                        lineno=lineno,
                        autonohookup=nohookup,
                        is_wildcard=True,
                        original_pattern=emacs_pat,
                    ))
                    pos += wm.end()
                    continue

                # Skip to end of line on parse failure
                pos = line_end
                continue

            # Unknown character — skip
            pos += 1

        return TemplateResult(
            regexp=tpl_regexp,
            sig_list=sig_list,
            wild_list=wild_list,
        )

    # ------------------------------------------------------------------
    # Wildcard pattern conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _make_wild_pattern(raw: str) -> str:
        """Convert a template wildcard port pattern to a Python regex.

        In the elisp, ``@`` in wild patterns becomes ``([0-9]+)`` — a
        capture group for the instance number embedded in the port name.
        Emacs regex grouping (``\\(`` / ``\\)``) is translated to Python
        syntax.  The result is anchored with ``^...$``.
        """
        from ..regex_compat import translate_emacs_regex

        # Use a placeholder so @ replacement doesn't get mangled
        # by the Emacs-to-Python regex translation.
        _AT_PLACEHOLDER = "\x00AT_GROUP\x00"
        pattern = raw.replace("@", _AT_PLACEHOLDER)
        # Translate Emacs regex syntax to Python
        # (e.g. \(.*\) → (.*), \1 stays as \1)
        pattern = translate_emacs_regex(pattern)
        # Now replace the placeholder with Python regex capture group
        pattern = pattern.replace(_AT_PLACEHOLDER, "([0-9]+)")
        # Anchor
        if not pattern.startswith("^"):
            pattern = "^" + pattern
        if not pattern.endswith("$"):
            pattern = pattern + "$"
        return pattern

    @staticmethod
    def _make_emacs_pattern(raw: str) -> str:
        """Convert a template wildcard port pattern to Emacs regex form.

        This is used for the ``LHS:`` comment — it shows the regex as Emacs
        would display it, NOT the Python-translated form.
        """
        # Replace @ with Emacs-style group for digits
        pattern = raw.replace("@", "\\([0-9]+\\)")
        # Anchor
        if not pattern.startswith("^"):
            pattern = "^" + pattern
        if not pattern.endswith("$"):
            pattern = pattern + "$"
        return pattern

    # ------------------------------------------------------------------
    # Template substitution
    # ------------------------------------------------------------------

    def _substitute(
        self,
        expr: str,
        port: "Signal",
        tpl_num: str,
        result: TemplateResult,
        vl_context: Optional[dict[str, str]] = None,
    ) -> str:
        """Apply ``@`` and ``[]`` substitutions to a template expression.

        Port of the substitution logic inside ``verilog-auto-inst-port``.
        """

        # First handle @"..." lisp expressions before plain @ replacement
        if '@"' in expr and vl_context:
            expr = self._eval_at_exprs(expr, vl_context, tpl_num)
        else:
            # Replace plain @ with the instance number
            expr = expr.replace("@", tpl_num)

        # Use param-substituted bits from vl_context if available,
        # otherwise fall back to port.bits (raw declaration bits).
        eff_bits = port.bits or ""
        if vl_context and vl_context.get("vl-bits"):
            eff_bits = vl_context["vl-bits"]

        # Replace [][] with the full default bits (multidim + bits)
        dflt = ""
        if eff_bits:
            if port.multidim:
                mbits = "".join(port.multidim)
                dflt = f"/*{mbits}{eff_bits}*/"
            else:
                dflt = eff_bits
        expr = expr.replace("[][]", dflt)

        # Replace [] with the port's bit range
        expr = expr.replace("[]", eff_bits)

        return expr

    # ------------------------------------------------------------------
    # @"expr" evaluation (port of verilog-auto-inst-port lisp eval)
    # ------------------------------------------------------------------

    def _eval_at_exprs(
        self,
        expr: str,
        ctx: dict[str, str],
        tpl_num: str,
    ) -> str:
        """Replace all ``@"..."`` patterns in *expr* with evaluated results.

        Also replaces remaining bare ``@`` with *tpl_num*.
        Handles escaped quotes ``\\"`` inside the lisp expression.
        """
        result = []
        i = 0
        while i < len(expr):
            if i < len(expr) - 1 and expr[i] == "@" and expr[i + 1] == '"':
                # Find closing quote, skipping escaped quotes (\\")
                j = i + 2
                while j < len(expr):
                    if expr[j] == '"' and (j < 2 or expr[j - 1:j + 1] != '\\"' or expr[j - 2:j] == '\\\\'):
                        # Check it's not an escaped quote
                        # Count preceding backslashes
                        bs = 0
                        k = j - 1
                        while k >= i + 2 and expr[k] == '\\':
                            bs += 1
                            k -= 1
                        if bs % 2 == 0:
                            break  # Unescaped quote — this is the closing one
                    j += 1
                if j >= len(expr):
                    # No matching closing quote — treat @ as plain substitution
                    result.append(tpl_num)
                    i += 1
                    continue
                lisp_expr = expr[i + 2:j]
                # Unescape: \\" → "
                lisp_expr = lisp_expr.replace('\\"', '"')
                evaluated = self._eval_lisp(lisp_expr, ctx, tpl_num)
                result.append(evaluated)
                i = j + 1
            elif expr[i] == "@":
                result.append(tpl_num)
                i += 1
            else:
                result.append(expr[i])
                i += 1
        return "".join(result)

    def _eval_lisp(
        self,
        expr: str,
        ctx: dict[str, str],
        tpl_num: str,
    ) -> str:
        """Evaluate a simple Emacs Lisp expression in template context.

        Supports:
        - Variable lookup: ``vl-cell-name``, ``vl-width``, ``vh-*``
        - ``(substring str from [to])``
        - ``(concat a b ...)``
        - ``(if cond then [else])``
        - ``(equal a b)``
        - ``(number-to-string n)`` / ``(int-to-string n)``
        - ``(+ a b)``, ``(* a b)``, ``(- a b)``, ``(/ a b)``
        """
        expr = expr.strip()

        # String literal: "text"
        if expr.startswith('"') and expr.endswith('"'):
            return expr[1:-1]

        # Numeric literal
        if expr.isdigit():
            return expr

        # S-expression: (func args...)
        if expr.startswith("("):
            return self._eval_sexp(expr, ctx, tpl_num)

        # Variable lookup
        if expr in ctx:
            return ctx[expr]

        # tpl_num for bare references
        return tpl_num if expr == "@" else expr

    def _eval_sexp(
        self,
        expr: str,
        ctx: dict[str, str],
        tpl_num: str,
    ) -> str:
        """Evaluate an S-expression like ``(func arg1 arg2)``."""
        # Strip outer parens
        inner = expr[1:-1].strip() if expr.endswith(")") else expr[1:].strip()

        # Parse function name and arguments
        func, rest = self._split_first_token(inner)
        func = func.lower()

        if func == "substring":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            if len(args) >= 2:
                s = args[0]
                frm = int(args[1]) if args[1].lstrip("-").isdigit() else 0
                to = int(args[2]) if len(args) > 2 and args[2].lstrip("-").isdigit() else None
                try:
                    return s[frm:to] if to is not None else s[frm:]
                except (IndexError, ValueError):
                    return ""
            return ""

        if func == "concat":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return "".join(args)

        if func in ("equal", "string="):
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            if len(args) >= 2:
                return "t" if args[0] == args[1] else ""
            return ""

        if func == "if":
            parts = self._split_sexp_parts(rest)
            if len(parts) >= 2:
                cond = self._eval_lisp(parts[0], ctx, tpl_num)
                if cond and cond != "" and cond != "nil":
                    return self._eval_lisp(parts[1], ctx, tpl_num)
                elif len(parts) >= 3:
                    return self._eval_lisp(parts[2], ctx, tpl_num)
            return ""

        if func == "not":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            if args and args[0] and args[0] != "" and args[0] != "nil":
                return ""
            return "t"

        if func == "or":
            parts = self._split_sexp_parts(rest)
            for part in parts:
                val = self._eval_lisp(part, ctx, tpl_num)
                if val and val != "" and val != "nil":
                    return val
            return ""

        if func == "length":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return str(len(args[0])) if args else "0"

        if func == ">":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            if len(args) >= 2:
                try:
                    return "t" if int(args[0]) > int(args[1]) else ""
                except ValueError:
                    return ""
            return ""

        if func in ("+", "-", "*", "/"):
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            try:
                nums = [int(a) for a in args]
                if func == "+":
                    return str(sum(nums))
                if func == "-":
                    return str(nums[0] - nums[1]) if len(nums) >= 2 else str(-nums[0])
                if func == "*":
                    r = 1
                    for n in nums:
                        r *= n
                    return str(r)
                if func == "/":
                    return str(nums[0] // nums[1]) if len(nums) >= 2 else "0"
            except (ValueError, IndexError, ZeroDivisionError):
                return ""

        if func in ("number-to-string", "int-to-string"):
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return args[0] if args else ""

        if func == "string-to-number":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return args[0] if args else "0"

        if func == "upcase":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return args[0].upper() if args else ""

        if func == "downcase":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return args[0].lower() if args else ""

        # uc (Emacs abbreviation for upcase)
        if func == "uc":
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            return args[0].upper() if args else ""

        # eval — evaluate the value of a variable as an expression
        # In Emacs, (eval tense) when tense="is" returns "is"
        if func == "eval":
            val = self._eval_lisp(rest.strip(), ctx, tpl_num)
            return val

        # let — let binding
        if func in ("let", "let*"):
            parts = self._split_sexp_parts(rest)
            if len(parts) >= 2:
                bindings = self._split_sexp_parts(
                    parts[0][1:-1] if parts[0].startswith("(") else parts[0]
                )
                local_ctx = dict(ctx)
                for bp in bindings:
                    if bp.startswith("("):
                        inner = bp[1:-1].strip() if bp.endswith(")") else bp[1:]
                        tokens = self._split_sexp_parts(inner)
                        if len(tokens) >= 2:
                            local_ctx[tokens[0]] = self._eval_lisp(tokens[1], local_ctx, tpl_num)
                result = ""
                for part in parts[1:]:
                    result = self._eval_lisp(part, local_ctx, tpl_num)
                return result
            return ""

        # User-defined functions from eval:(defun ...) local variables
        # Check with original case first, then lowercased
        user_funcs = getattr(self._config, "user_functions", {})
        func_orig = self._split_first_token(inner)[0]  # original case
        func_key = func_orig if func_orig in user_funcs else func
        if func_key in user_funcs:
            params, body = user_funcs[func_key]
            args = self._parse_sexp_args(rest, ctx, tpl_num)
            func_ctx = dict(ctx)
            for i, p in enumerate(params):
                func_ctx[p] = args[i] if i < len(args) else ""
            return self._eval_lisp(body, func_ctx, tpl_num)

        # Unknown function — return empty
        return ""

    def _parse_sexp_args(
        self,
        rest: str,
        ctx: dict[str, str],
        tpl_num: str,
    ) -> list[str]:
        """Parse and evaluate all arguments in an S-expression."""
        parts = self._split_sexp_parts(rest)
        return [self._eval_lisp(p, ctx, tpl_num) for p in parts]

    @staticmethod
    def _split_first_token(s: str) -> tuple[str, str]:
        """Split string into first whitespace-delimited token and the rest."""
        s = s.strip()
        for i, ch in enumerate(s):
            if ch in " \t\n\r":
                return s[:i], s[i:].strip()
        return s, ""

    @staticmethod
    def _split_sexp_parts(s: str) -> list[str]:
        """Split a string into top-level S-expression parts.

        Respects parentheses and quoted strings.
        """
        parts: list[str] = []
        current = ""
        depth = 0
        in_str = False
        i = 0
        s = s.strip()
        while i < len(s):
            ch = s[i]
            if ch == "\\" and i + 1 < len(s) and in_str:
                current += ch + s[i + 1]
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
                current += ch
            elif in_str:
                current += ch
            elif ch == "(":
                depth += 1
                current += ch
            elif ch == ")":
                depth -= 1
                current += ch
            elif ch in " \t\n\r" and depth == 0:
                if current.strip():
                    parts.append(current.strip())
                current = ""
            else:
                current += ch
            i += 1
        if current.strip():
            parts.append(current.strip())
        return parts

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_close_paren(text: str, open_pos: int) -> Optional[int]:
        """Find the matching ``)`` for ``(`` at *open_pos*.
        Returns position after the ``)``.
        """
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
