"""Parse ``// Local Variables:`` blocks in Verilog files.

Emacs writes per-file settings like::

    // Local Variables:
    // verilog-library-directories:("." "../lib")
    // verilog-auto-inst-sort:t
    // End:

This module parses those blocks and applies the values on top of a
``VerilogConfig``.
"""

from __future__ import annotations

import re
from dataclasses import fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .buffer import VerilogBuffer
    from .config import VerilogConfig


# ---------------------------------------------------------------------------
# Emacs-Lisp value parsing helpers
# ---------------------------------------------------------------------------

def _parse_elisp_value(raw: str) -> object:
    """Convert a raw Emacs Lisp value string to a Python object.

    Handles:
    - ``t`` → ``True``
    - ``nil`` → ``None`` (treated as ``False`` when applied to booleans)
    - ``"string"`` → ``str``
    - ``("a" "b")`` → ``list[str]``
    - ``'symbol`` → ``str`` (the symbol name)
    - integer literals
    """
    s = raw.strip()

    if s == "t":
        return True
    if s == "nil":
        return None

    # Quoted symbol: 'foo → "foo"
    if s.startswith("'"):
        return s[1:]

    # Integer
    if re.fullmatch(r"-?\d+", s):
        return int(s)

    # Double-quoted string
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]

    # List of strings: ("a" "b" "c")
    m = re.fullmatch(r'\((.+)\)', s)
    if m:
        inner = m.group(1)
        return re.findall(r'"([^"]*)"', inner)

    # Fallback — return as-is string
    return s


# ---------------------------------------------------------------------------
# Config field name mapping:  verilog-auto-inst-sort → auto_inst_sort
# ---------------------------------------------------------------------------

_PREFIX = "verilog-"


def _elisp_name_to_field(name: str) -> str:
    """Convert ``verilog-auto-inst-sort`` → ``auto_inst_sort``."""
    if name.startswith(_PREFIX):
        name = name[len(_PREFIX):]
    return name.replace("-", "_")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_local_vars(buf: "VerilogBuffer") -> dict[str, str]:
    """Scan *buf* for a ``// Local Variables:`` block.

    Returns ``{var_name: raw_value}`` where *var_name* still uses the
    original Emacs-Lisp hyphenated form (e.g. ``verilog-auto-inst-sort``).

    Handles both ``//``-style and ``/* */``-style comment blocks.
    Also handles multi-line ``eval:`` values by reading continuation
    lines until parentheses are balanced.
    """
    text = buf.buffer_string()
    result: dict[str, str] = {}

    # Try // style first, then /* */ style
    m = re.search(
        r"//\s*Local\s+Variables\s*:\s*\n(.*?)//\s*End\s*:",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        # Try block comment style: /* ... Local Variables: ... End: ... */
        m = re.search(
            r"Local\s+Variables\s*:\s*\n(.*?)End\s*:",
            text,
            re.DOTALL | re.IGNORECASE,
        )
    if not m:
        return result

    block = m.group(1)
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Strip leading comment markers
        if line.startswith("//"):
            line = line[2:].strip()
        elif line.startswith("*"):
            line = line[1:].strip()

        # Parse "var:value"
        vm = re.match(r"([\w-]+)\s*:\s*(.*)", line)
        if vm:
            var_name = vm.group(1).strip()
            var_value = vm.group(2).strip()

            # For eval: with multi-line Lisp forms, read continuation
            # lines until parens are balanced
            if var_name == "eval" and var_value == "":
                # Read continuation lines
                eval_lines = []
                i += 1
                while i < len(lines):
                    cline = lines[i].strip()
                    if cline.startswith("//"):
                        cline = cline[2:].strip()
                    elif cline.startswith("*"):
                        cline = cline[1:].strip()
                    # Stop if we hit another var:value or End:
                    if re.match(r"[\w-]+\s*:", cline) and not cline.startswith("("):
                        break
                    eval_lines.append(cline)
                    # Check if parens are balanced
                    joined = " ".join(eval_lines)
                    if _parens_balanced(joined):
                        i += 1
                        break
                    i += 1
                var_value = " ".join(eval_lines)
            elif var_name == "eval" and var_value.startswith("("):
                # eval on one line but may need continuation
                while not _parens_balanced(var_value) and i + 1 < len(lines):
                    i += 1
                    cline = lines[i].strip()
                    if cline.startswith("//"):
                        cline = cline[2:].strip()
                    elif cline.startswith("*"):
                        cline = cline[1:].strip()
                    var_value += " " + cline

            result[var_name] = var_value
        i += 1

    return result


def _parens_balanced(s: str) -> bool:
    """Check if parentheses are balanced in *s*."""
    depth = 0
    in_str = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and in_str and i + 1 < len(s):
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    return depth == 0 and s.count("(") > 0


def _parse_eval_defun(value: str, cfg_dict: dict) -> None:
    """Parse ``(defun name (params...) body)`` and store in user_functions."""
    m = re.match(
        r"\(defun\s+(\S+)\s+\(([^)]*)\)\s+(.+)\)\s*$",
        value,
        re.DOTALL,
    )
    if not m:
        return
    func_name = m.group(1).strip()
    params = [p.strip() for p in m.group(2).split() if p.strip()]
    body = m.group(3).strip()
    if "user_functions" not in cfg_dict:
        cfg_dict["user_functions"] = {}
    cfg_dict["user_functions"][func_name] = (params, body)


def apply_local_vars(
    config: "VerilogConfig", local_vars: dict[str, str]
) -> "VerilogConfig":
    """Return a **new** ``VerilogConfig`` with *local_vars* applied on top.

    Unknown variable names are silently ignored.
    """
    from .config import VerilogConfig  # avoid circular at module level

    # Build a set of valid field names for fast lookup
    field_map = {f.name: f for f in fields(config)}

    # Copy current config into a dict
    cfg_dict = {f.name: getattr(config, f.name) for f in fields(config)}

    for var_name, raw_value in local_vars.items():
        # Handle eval:(defun name (params) body) — user-defined functions
        if var_name == "eval" and raw_value.strip().startswith("(defun"):
            _parse_eval_defun(raw_value.strip(), cfg_dict)
            continue

        # Handle eval:(verilog-read-defines) — force reading all defines
        if var_name == "eval" and "verilog-read-defines" in raw_value:
            cfg_dict["auto_read_includes"] = True
            continue

        py_field = _elisp_name_to_field(var_name)
        if py_field not in field_map:
            continue  # skip unknown

        f = field_map[py_field]
        parsed = _parse_elisp_value(raw_value)

        # Type coercion based on the field's annotation
        if f.type == "bool":
            cfg_dict[py_field] = bool(parsed) if parsed is not None else False
        elif f.type == "int":
            cfg_dict[py_field] = int(parsed) if parsed is not None else 0
        elif f.type in ("Optional[str]", "str"):
            if parsed is None:
                cfg_dict[py_field] = None
            else:
                cfg_dict[py_field] = str(parsed)
        elif "list[str]" in str(f.type):
            if isinstance(parsed, list):
                cfg_dict[py_field] = parsed
            elif isinstance(parsed, str):
                cfg_dict[py_field] = [parsed]
            else:
                cfg_dict[py_field] = []
        elif f.type == "object":
            # Preserve the parsed value as-is (supports mixed types
            # like auto_inst_vector which can be True, None, or "unsigned")
            if parsed is None:
                cfg_dict[py_field] = None
            else:
                cfg_dict[py_field] = parsed
        else:
            cfg_dict[py_field] = parsed

    return VerilogConfig(**cfg_dict)
