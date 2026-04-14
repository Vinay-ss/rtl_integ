"""Minimal Lisp expression evaluator for AUTO_LISP / @"(expr)" templates.

Ported from the ``verilog-read-auto-lisp`` function and the inline
``eval-region`` calls in ``verilog-mode.el`` (line ~9974).

AUTO_LISP blocks appear as::

    /*AUTO_LISP(setq num 1)*/
    /* AUTO_LISP(setq verilog-auto-inst-param-value t) */

They are evaluated in document order before AUTO expansion starts.
The results modify the per-file config and/or set variables that
``@"expr"`` template substitutions can reference.

Supported Lisp subset (covers all forms used in the test suite)::

    (setq name value)          ; variable assignment
    (1+ n)                     ; increment
    (1- n)                     ; decrement
    (+ a b ...)                ; addition
    (- a b)                    ; subtraction
    (* a b ...)                ; multiplication
    (/ a b)                    ; integer division
    (number-to-string n)       ; int to string
    (int-to-string n)          ; alias
    (string-to-number s)       ; string to int
    (concat a b ...)           ; string concatenation
    (substring s from [to])    ; substring extraction
    (upcase s)                 ; uppercase
    (downcase s)               ; lowercase (uc in elisp)
    (if cond then [else])      ; conditional
    (equal a b)                ; equality test
    (not x)                    ; boolean negation
    (or a b ...)               ; boolean or
    (length s)                 ; string length
    (> a b)                    ; numeric comparison
    (eval x)                   ; evaluate variable as expression
    (let (...) body)           ; let binding (simplified)
    (defun name (params) body) ; function definition

Unsupported forms emit a warning and return "0".
"""

from __future__ import annotations

import re
import warnings
from typing import Any, Optional


class AutoLispEval:
    """Evaluate AUTO_LISP expressions and maintain an environment."""

    def __init__(self) -> None:
        self._env: dict[str, Any] = {}

    @property
    def env(self) -> dict[str, Any]:
        """Read-only access to the evaluator environment."""
        return self._env

    def setq(self, name: str, value: Any) -> None:
        """Set a variable in the evaluator environment."""
        self._env[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        """Get a variable from the evaluator environment."""
        return self._env.get(name, default)

    def eval(self, expr: str) -> str:
        """Evaluate a Lisp expression string. Return the string result."""
        expr = expr.strip()
        if not expr:
            return ""
        try:
            result = self._eval_inner(expr)
            if result is None:
                return ""
            if isinstance(result, bool):
                return "t" if result else ""
            return str(result)
        except Exception as exc:
            warnings.warn(f"AUTO_LISP: error evaluating {expr!r}: {exc}", stacklevel=2)
            return "0"

    def eval_auto_lisp_block(self, text: str) -> None:
        """Find and evaluate all ``AUTO_LISP(...)`` blocks in *text*.

        This modifies the evaluator environment with any ``setq`` calls
        and stores ``defun`` definitions.
        """
        for m in re.finditer(r"\bAUTO_LISP\s*\(", text):
            start = m.end() - 1  # position of the opening (
            end = self._find_matching_paren(text, start)
            if end is None:
                continue
            sexp = text[start:end]
            self._eval_inner(sexp)

    # ------------------------------------------------------------------
    # Core evaluator
    # ------------------------------------------------------------------

    def _eval_inner(self, expr: str) -> Any:
        """Evaluate an expression, returning a Python value."""
        expr = expr.strip()

        # nil
        if expr == "nil":
            return None

        # t
        if expr == "t":
            return True

        # Numeric literal
        if re.fullmatch(r"-?\d+", expr):
            return int(expr)

        # String literal "text"
        if expr.startswith('"') and expr.endswith('"'):
            return self._unescape_elisp_string(expr[1:-1])

        # Quoted symbol: 'foo
        if expr.startswith("'"):
            return expr[1:]

        # S-expression
        if expr.startswith("("):
            return self._eval_sexp(expr)

        # Variable lookup
        if expr in self._env:
            return self._env[expr]

        # Return as string literal
        return expr

    def _eval_sexp(self, expr: str) -> Any:
        """Evaluate an S-expression ``(func args...)``."""
        inner = self._strip_outer_parens(expr)
        if not inner:
            return None

        func, rest = self._split_first_token(inner)
        func_lower = func.lower()

        # setq: variable assignment
        if func_lower == "setq":
            return self._do_setq(rest)

        # defun: function definition
        if func_lower == "defun":
            return self._do_defun(rest)

        # 1+, 1-
        if func == "1+":
            val = self._eval_inner(rest.strip())
            return (int(val) if val is not None else 0) + 1
        if func == "1-":
            val = self._eval_inner(rest.strip())
            return (int(val) if val is not None else 0) - 1

        # Arithmetic: +, -, *, /
        if func in ("+", "-", "*", "/"):
            args = self._eval_args(rest)
            try:
                nums = [int(a) if not isinstance(a, int) else a for a in args]
                if func == "+":
                    return sum(nums)
                if func == "-":
                    return nums[0] - nums[1] if len(nums) >= 2 else -nums[0]
                if func == "*":
                    r = 1
                    for n in nums:
                        r *= n
                    return r
                if func == "/":
                    return nums[0] // nums[1] if len(nums) >= 2 else 0
            except (ValueError, IndexError, ZeroDivisionError):
                return 0

        # Comparison: >
        if func == ">":
            args = self._eval_args(rest)
            try:
                return int(args[0]) > int(args[1])
            except (ValueError, IndexError):
                return None

        # number-to-string / int-to-string
        if func_lower in ("number-to-string", "int-to-string"):
            val = self._eval_inner(rest.strip())
            return str(int(val)) if val is not None else "0"

        # string-to-number
        if func_lower == "string-to-number":
            val = self._eval_inner(rest.strip())
            try:
                return int(val)
            except (ValueError, TypeError):
                return 0

        # concat
        if func_lower == "concat":
            args = self._eval_args(rest)
            return "".join(str(a) if a is not None else "" for a in args)

        # substring
        if func_lower == "substring":
            args = self._eval_args(rest)
            if len(args) >= 2:
                s = str(args[0]) if args[0] is not None else ""
                frm = int(args[1]) if args[1] is not None else 0
                to = int(args[2]) if len(args) > 2 and args[2] is not None else None
                try:
                    return s[frm:to] if to is not None else s[frm:]
                except (IndexError, ValueError):
                    return ""
            return ""

        # upcase / uc
        if func_lower in ("upcase", "uc"):
            val = self._eval_inner(rest.strip())
            return str(val).upper() if val is not None else ""

        # downcase
        if func_lower == "downcase":
            val = self._eval_inner(rest.strip())
            return str(val).lower() if val is not None else ""

        # length
        if func_lower == "length":
            val = self._eval_inner(rest.strip())
            return len(str(val)) if val is not None else 0

        # equal / string=
        if func_lower in ("equal", "string="):
            args = self._eval_args(rest)
            if len(args) >= 2:
                return args[0] == args[1]
            return None

        # not
        if func_lower == "not":
            val = self._eval_inner(rest.strip())
            return val is None or val == "" or val is False

        # or
        if func_lower == "or":
            parts = self._split_sexp_parts(rest)
            for part in parts:
                val = self._eval_inner(part)
                if val is not None and val != "" and val is not False:
                    return val
            return None

        # and
        if func_lower == "and":
            parts = self._split_sexp_parts(rest)
            result = None
            for part in parts:
                result = self._eval_inner(part)
                if result is None or result == "" or result is False:
                    return None
            return result

        # if
        if func_lower == "if":
            parts = self._split_sexp_parts(rest)
            if len(parts) >= 2:
                cond = self._eval_inner(parts[0])
                if cond is not None and cond != "" and cond is not False:
                    return self._eval_inner(parts[1])
                elif len(parts) >= 3:
                    return self._eval_inner(parts[2])
            return None

        # when
        if func_lower == "when":
            parts = self._split_sexp_parts(rest)
            if len(parts) >= 2:
                cond = self._eval_inner(parts[0])
                if cond is not None and cond != "" and cond is not False:
                    result = None
                    for part in parts[1:]:
                        result = self._eval_inner(part)
                    return result
            return None

        # let / let*
        if func_lower in ("let", "let*"):
            return self._do_let(rest)

        # progn / prog1
        if func_lower in ("progn", "prog1"):
            parts = self._split_sexp_parts(rest)
            first = None
            for i, part in enumerate(parts):
                val = self._eval_inner(part)
                if i == 0:
                    first = val
            return first if func_lower == "prog1" else val

        # eval
        if func_lower == "eval":
            val = self._eval_inner(rest.strip())
            if isinstance(val, str) and val in self._env:
                return self._env[val]
            return val

        # insert — in Emacs, inserts text into buffer (side effect).
        # We capture the text in _insert_buffer for AUTOINSERTLISP.
        if func_lower == "insert":
            args = self._eval_args(rest)
            text = "".join(str(a) if a is not None else "" for a in args)
            if hasattr(self, "_insert_buffer") and isinstance(self._insert_buffer, list):
                self._insert_buffer.append(text)
            return None  # insert returns nil in Emacs

        # shell-command-to-string — run a shell command and return output
        if func_lower == "shell-command-to-string":
            args = self._eval_args(rest)
            if args:
                import subprocess
                try:
                    result = subprocess.run(
                        str(args[0]), shell=True, capture_output=True,
                        text=True, timeout=10,
                    )
                    return result.stdout.rstrip("\n")
                except Exception:
                    return ""
            return ""

        # format
        if func_lower == "format":
            args = self._eval_args(rest)
            if args:
                fmt = str(args[0])
                # Simple %s and %d replacement
                result = fmt
                for a in args[1:]:
                    result = result.replace("%s", str(a), 1)
                    result = result.replace("%d", str(int(a) if a is not None else 0), 1)
                return result
            return ""

        # User-defined function
        if func in self._env and isinstance(self._env[func], tuple):
            params, body = self._env[func]
            args = self._eval_args(rest)
            # Save env, bind params, evaluate body, restore
            saved = dict(self._env)
            for i, p in enumerate(params):
                self._env[p] = args[i] if i < len(args) else None
            result = self._eval_inner(body)
            # Restore everything except what setq may have modified
            for k, v in saved.items():
                if k not in [p for p in params]:
                    pass  # keep any setq changes
            # Restore param bindings
            for p in params:
                if p in saved:
                    self._env[p] = saved[p]
                else:
                    self._env.pop(p, None)
            return result

        # Unknown form
        warnings.warn(f"AUTO_LISP: unsupported form: ({func} ...)", stacklevel=3)
        return 0

    # ------------------------------------------------------------------
    # Special forms
    # ------------------------------------------------------------------

    def _do_setq(self, rest: str) -> Any:
        """Handle ``(setq name value [name2 value2 ...])``."""
        parts = self._split_sexp_parts(rest)
        result = None
        i = 0
        while i + 1 < len(parts):
            name = parts[i]
            val = self._eval_inner(parts[i + 1])
            self._env[name] = val
            result = val
            i += 2
        return result

    def _do_defun(self, rest: str) -> Any:
        """Handle ``(defun name (params...) body)``."""
        parts = self._split_sexp_parts(rest)
        if len(parts) >= 3:
            name = parts[0]
            # Parse parameter list
            param_str = self._strip_outer_parens(parts[1])
            params = param_str.split() if param_str else []
            # Body is everything after params
            body = parts[2] if len(parts) == 3 else f"(progn {' '.join(parts[2:])})"
            self._env[name] = (params, body)
        return None

    def _do_let(self, rest: str) -> Any:
        """Handle ``(let ((var val) ...) body...)``."""
        parts = self._split_sexp_parts(rest)
        if len(parts) < 2:
            return None

        # Parse bindings: ((var1 val1) (var2 val2) ...)
        bindings_str = self._strip_outer_parens(parts[0])
        binding_parts = self._split_sexp_parts(bindings_str)

        saved: dict[str, Any] = {}
        bound_names: list[str] = []
        for bp in binding_parts:
            if bp.startswith("("):
                inner = self._strip_outer_parens(bp)
                tokens = self._split_sexp_parts(inner)
                if len(tokens) >= 2:
                    name = tokens[0]
                    val = self._eval_inner(tokens[1])
                    if name in self._env:
                        saved[name] = self._env[name]
                    self._env[name] = val
                    bound_names.append(name)
            else:
                # Just a name with no value = nil
                if bp in self._env:
                    saved[bp] = self._env[bp]
                self._env[bp] = None
                bound_names.append(bp)

        # Evaluate body forms
        result = None
        for part in parts[1:]:
            result = self._eval_inner(part)

        # Restore bindings
        for name in bound_names:
            if name in saved:
                self._env[name] = saved[name]
            else:
                self._env.pop(name, None)

        return result

    # ------------------------------------------------------------------
    # String escaping
    # ------------------------------------------------------------------

    @staticmethod
    def _unescape_elisp_string(s: str) -> str:
        """Unescape an Emacs Lisp string literal's escape sequences.

        Handles: ``\\\\`` → ``\\``, ``\\"`` → ``"``, ``\\n`` → newline,
        ``\\t`` → tab, and ``\\/`` → ``/`` (backslash before non-special
        chars is simply removed in Emacs).
        """
        result = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                if nxt == "\\":
                    result.append("\\")
                elif nxt == '"':
                    result.append('"')
                elif nxt == "n":
                    result.append("\n")
                elif nxt == "t":
                    result.append("\t")
                else:
                    # Backslash before non-special char is ignored
                    result.append(nxt)
                i += 2
            else:
                result.append(s[i])
                i += 1
        return "".join(result)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _eval_args(self, rest: str) -> list[Any]:
        """Split *rest* into parts and evaluate each."""
        parts = self._split_sexp_parts(rest)
        return [self._eval_inner(p) for p in parts]

    @staticmethod
    def _strip_outer_parens(s: str) -> str:
        """Remove matching outer parentheses: ``(foo bar)`` → ``foo bar``."""
        s = s.strip()
        if s.startswith("(") and s.endswith(")"):
            return s[1:-1].strip()
        return s

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
        """Split a string into top-level S-expression parts."""
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

    @staticmethod
    def _find_matching_paren(text: str, pos: int) -> Optional[int]:
        """Find the matching ``)`` for ``(`` at *pos*.
        Returns position after the ``)``, or None.
        """
        depth = 0
        i = pos
        n = len(text)
        in_str = False
        while i < n:
            ch = text[i]
            if ch == "\\" and in_str and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
        return None
