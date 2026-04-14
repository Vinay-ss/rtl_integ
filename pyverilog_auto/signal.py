"""Signal, ModDecls, SubDecls, Modi — core data structures for the AUTO system.

Ported from ``verilog-sig-new``, ``verilog-decls-new``, ``verilog-modi-new``
(around lines 8745–8863 of ``verilog-mode.el``) and the ``verilog-signals-*``
utility functions (lines ~8900–9085).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .config import VerilogConfig


# ---------------------------------------------------------------------------
# Constant expression evaluation
# ---------------------------------------------------------------------------

def _eval_const_expr(expr: str) -> Optional[int]:
    """Try to evaluate *expr* as a constant integer expression.

    Handles integer literals, +, -, *, /, **, and $clog2().
    Returns None if the expression contains unresolvable identifiers.
    """
    expr = expr.strip()
    if not expr:
        return None

    # Process $clog2 with proper balanced-paren matching
    while "$clog2" in expr:
        idx = expr.find("$clog2")
        # Find the opening (
        paren_start = expr.find("(", idx)
        if paren_start < 0:
            return None
        # Find the matching ) using depth tracking
        depth = 0
        paren_end = -1
        for i in range(paren_start, len(expr)):
            if expr[i] == "(":
                depth += 1
            elif expr[i] == ")":
                depth -= 1
                if depth == 0:
                    paren_end = i
                    break
        if paren_end < 0:
            return None
        inner = expr[paren_start + 1:paren_end]
        val = _eval_const_expr(inner)
        if val is not None and val > 0:
            clog2_val = max(1, math.ceil(math.log2(val)))
            expr = expr[:idx] + str(clog2_val) + expr[paren_end + 1:]
        else:
            return None

    # Check if the expression is purely numeric with operators
    # Allow: digits, +, -, *, /, (, ), spaces
    if re.fullmatch(r"[\d\s+\-*/()]+", expr):
        try:
            result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
            if isinstance(result, (int, float)):
                return int(result)
        except Exception:
            return None
    return None


def simplify_range(range_str: str) -> str:
    """Simplify a Verilog range expression using iterative text rewriting.

    Port of ``verilog-simplify-range-expression`` from ``verilog-mode.el``.
    Handles mixed symbolic/numeric expressions by only collapsing adjacent
    numeric sub-expressions while respecting operator precedence.

    Examples::

        [8+4-1:0]       → [11:0]
        [DW-1+2:0]      → [DW+1:0]
        [FOO*4-1*2:0]   → [FOO*4-2:0]
        [4>>2:0]         → [1:0]
        [(2*3+6*7)]      → [48]
    """
    if not range_str:
        return range_str

    # Short-circuit: if no operators or parens, nothing to simplify
    if not re.search(r"[-+*/<>()]", range_str):
        return range_str

    out = range_str
    last_pass = ""

    # Boundary characters for operator-precedence context.
    # These mirror the elisp regex character classes exactly.
    # Use \[ and \] to avoid FutureWarning about nested sets in Python 3.12+.
    _PREFIX_ALL = r"[\[({:*/<>+\-]"    # prefix for *, /
    _SUFFIX_ALL = r"[\])}:*/<>.+\-]"   # suffix for paren removal + *, /
    _PREFIX_PM = r"[\[({:<>+\-]"       # prefix for +, - (no * or /)
    _SUFFIX_PM = r"[\])}:<>+\-]"       # suffix for +, -
    _PREFIX_SHIFT = r"[\[({:]"          # prefix for >>, <<
    _SUFFIX_SHIFT = r"[\])}:<>]"       # suffix for >>, <<

    while out != last_pass:
        last_pass = out
        inner_last = ""
        while out != inner_last:
            inner_last = out

            # --- Step 1: Strip redundant parentheses around simple terms ---
            # (IDENT_OR_NUM) surrounded by operator/bracket context
            out = re.sub(
                _PREFIX_ALL + r"\((\w+)\)" + _SUFFIX_ALL,
                lambda m: m.group(0)[0] + m.group(1) + m.group(0)[-1],
                out,
            )

            # --- Step 2: Evaluate $clog2(N) where N is a number ---
            def _clog2_repl(m: re.Match) -> str:
                pre, nstr, post = m.group(1), m.group(2), m.group(3)
                n = int(nstr)
                if n > 0:
                    val = max(1, math.ceil(math.log2(n)))
                    return f"{pre}{val}{post}"
                return m.group(0)

            out = re.sub(
                r"(" + _PREFIX_ALL + r")"
                r"\$clog2\s*\((\d+)\)"
                r"(" + _SUFFIX_ALL + r")",
                _clog2_repl,
                out,
            )

            # --- Step 3: Evaluate N*M and N/M (highest precedence) ---
            changed = True
            while changed:
                changed = False
                m = re.search(
                    r"(" + _PREFIX_ALL + r")"
                    r"(\d+)\s*([*/])\s*(\d+)"
                    r"(" + _SUFFIX_ALL + r")",
                    out,
                )
                if m:
                    pre, lhs_s, op, rhs_s, post = (
                        m.group(1), m.group(2), m.group(3),
                        m.group(4), m.group(5),
                    )
                    lhs, rhs = int(lhs_s), int(rhs_s)
                    if op == "/" and (rhs == 0 or lhs % rhs != 0):
                        break  # don't simplify non-even division
                    val = lhs * rhs if op == "*" else lhs // rhs
                    out = out[:m.start()] + pre + str(val) + post + out[m.end():]
                    changed = True

            # --- Step 4: Evaluate N+M and N-M ---
            changed = True
            while changed:
                changed = False
                m = re.search(
                    r"(" + _PREFIX_PM + r")"
                    r"(\d+)\s*([+\-])\s*(\d+)"
                    r"(" + _SUFFIX_PM + r")",
                    out,
                )
                if m:
                    pre = m.group(1)
                    lhs = int(m.group(2))
                    op = m.group(3)
                    rhs = int(m.group(4))
                    post = m.group(5)
                    # If preceded by '-', the lhs is actually negative
                    if pre == "-":
                        lhs = -lhs
                    val = lhs - rhs if op == "-" else lhs + rhs
                    # Choose replacement prefix (mirrors elisp cond).
                    # In the elisp, int-to-string of a negative number
                    # produces "-N", so the prefix is dropped to avoid
                    # double-negative ("--N" → "-N").  For positive
                    # results the sign changes ("-+N" → "+N").
                    if pre == "-" and val < 0:
                        new_pre = ""   # str(val) already has "-"
                    elif pre == "-" and val > 0:
                        new_pre = "+"  # flip sign
                    else:
                        new_pre = pre  # keep prefix as-is
                    out = (out[:m.start()] + new_pre + str(val)
                           + post + out[m.end():])
                    changed = True

            # --- Step 5: Evaluate N>>M, N>>>M, N<<M, N<<<M ---
            changed = True
            while changed:
                changed = False
                m = re.search(
                    r"(" + _PREFIX_SHIFT + r")"
                    r"(\d+)\s*(>{2,3}|<{2,3})\s*(\d+)"
                    r"(" + _SUFFIX_SHIFT + r")",
                    out,
                )
                if m:
                    pre, lhs_s, op, rhs_s, post = (
                        m.group(1), m.group(2), m.group(3),
                        m.group(4), m.group(5),
                    )
                    lhs, rhs = int(lhs_s), int(rhs_s)
                    if op in (">>", ">>>"):
                        val = lhs >> rhs
                    elif op in ("<<", "<<<"):
                        val = lhs << rhs
                    else:
                        break
                    out = out[:m.start()] + pre + str(val) + post + out[m.end():]
                    changed = True

    return out


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """One signal / port / parameter declaration."""

    name: str
    bits: Optional[str] = None          # packed dimensions e.g. "[7:0]"
    comment: Optional[str] = None
    memory: Optional[str] = None        # unpacked dimensions e.g. "[3:0]"
    enum: Optional[str] = None
    signed: Optional[str] = None        # "signed" or None
    type: Optional[str] = None          # "wire", "logic", "reg", etc.
    multidim: Optional[list[str]] = None
    modport: Optional[str] = None

    # -- helpers -----------------------------------------------------------

    def width_expression(self) -> Optional[str]:
        """Return a width expression derived from *bits* (port of
        ``verilog-make-width-expression``)."""
        if not self.bits:
            return None
        # Strip outer brackets
        inner = self.bits.strip()
        m = re.match(r"^\[(.+)\]$", inner)
        if not m:
            return None
        range_exp = m.group(1)
        # [#:#] — numeric range
        m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", range_exp)
        if m:
            return str(abs(int(m.group(1)) - int(m.group(2))) + 1)
        # [PARAM-1:0] — common pattern
        m = re.match(
            r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*-\s*1\s*:\s*0\s*$",
            range_exp,
        )
        if m:
            return m.group(1)
        # [high:low] — arbitrary expression
        m = re.match(r"^(.*)\s*:\s*(.*)$", range_exp)
        if m:
            high, low = m.group(1).strip(), m.group(2).strip()
            if low == "0":
                return f"(1+({high}))"
            return f"(1+({high})-({low}))"
        # No colon — can't determine width (e.g. a define that
        # expands to a range)
        return None

    def tieoff_value(self, config: "VerilogConfig") -> str:
        """Return the tie-off string for this signal (port of
        ``verilog-sig-tieoff``)."""
        from .regex_compat import string_match_fold  # local to avoid circular

        prefix = ""
        if config.active_low_regexp and string_match_fold(
            config.active_low_regexp, self.name, config.case_fold
        ):
            prefix = "~"

        if not config.auto_reset_widths:
            return f"{prefix}0"

        if config.auto_reset_widths == "unbased":
            return f"{prefix}'0"

        width = self.width_expression()
        if width is None:
            if self.bits:
                # Has bits but width can't be computed (e.g. define-based range)
                return f"{prefix}'0/*NOWIDTH*/"
            # No bits field means 1-bit wide
            suffix = "'sh0" if self.signed else "'h0"
            return f"{prefix}1{suffix}"
        if width.isdigit():
            suffix = "'sh0" if self.signed else "'h0"
            return f"{prefix}{width}{suffix}"
        return f"{prefix}{{{width}{{1'b0}}}}"


# ---------------------------------------------------------------------------
# Modport
# ---------------------------------------------------------------------------

@dataclass
class Modport:
    name: str
    signals: list[Signal] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ModDecls — declarations of a module
# ---------------------------------------------------------------------------

@dataclass
class ModDecls:
    """Port / signal declarations of a module (``verilog-decls-new``)."""

    outputs: list[Signal] = field(default_factory=list)
    inouts: list[Signal] = field(default_factory=list)
    inputs: list[Signal] = field(default_factory=list)
    vars: list[Signal] = field(default_factory=list)
    modports: list[Modport] = field(default_factory=list)
    assigns: list[Signal] = field(default_factory=list)
    consts: list[Signal] = field(default_factory=list)
    gparams: list[Signal] = field(default_factory=list)
    interfaces: list[Signal] = field(default_factory=list)

    def append(self, other: "ModDecls") -> "ModDecls":
        """Return a new ``ModDecls`` with lists concatenated (``verilog-decls-append``)."""
        return ModDecls(
            outputs=self.outputs + other.outputs,
            inouts=self.inouts + other.inouts,
            inputs=self.inputs + other.inputs,
            vars=self.vars + other.vars,
            modports=self.modports + other.modports,
            assigns=self.assigns + other.assigns,
            consts=self.consts + other.consts,
            gparams=self.gparams + other.gparams,
            interfaces=self.interfaces + other.interfaces,
        )


# ---------------------------------------------------------------------------
# SubDecls — signals driven/consumed by sub-module instances
# ---------------------------------------------------------------------------

@dataclass
class SubDecls:
    """Signals driven/consumed by sub-module instances (``verilog-subdecls-new``)."""

    outputs: list[Signal] = field(default_factory=list)
    inouts: list[Signal] = field(default_factory=list)
    inputs: list[Signal] = field(default_factory=list)
    interfaces: list[Signal] = field(default_factory=list)
    interfaced: list[Signal] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Modi — a found module definition
# ---------------------------------------------------------------------------

@dataclass
class Modi:
    """A found module definition (``verilog-modi-new``)."""

    name: str            # module name
    filepath: str        # absolute path to file
    point: int           # character offset of ``module`` keyword
    type: str = "module"  # "module", "interface", "program", "package"


# ---------------------------------------------------------------------------
# Signal-set operations
# ---------------------------------------------------------------------------

def signals_not_params(sigs: list[Signal], defines: dict[str, str]) -> list[Signal]:
    """Remove signals that are parameters or numeric constants.

    Port of ``verilog-signals-not-params``: in the elisp, this checks
    if ``vh-<name>`` is bound, which corresponds to the name being
    in the defines dict (populated from ``parameter`` / backtick ``define``).
    """
    return [s for s in sigs if s.name not in defines]


def signals_not_in(a: list[Signal], b: list[Signal]) -> list[Signal]:
    """Return signals in *a* whose name is not in *b* (de-duplicated)."""
    exclude = {s.name for s in b}
    seen: set[str] = set()
    out: list[Signal] = []
    for s in a:
        if s.name not in exclude and s.name not in seen:
            out.append(s)
            seen.add(s.name)
    return out


def signals_not_in_struct(a: list[Signal], b: list[Signal]) -> list[Signal]:
    """Return signals in *a* not in *b*, with struct-aware exclusion.

    Port of ``verilog-signals-not-in-struct``: if ``foo`` is in *b*,
    then ``foo.bar``, ``foo.bar.baz`` etc. in *a* are also excluded.
    """
    import re
    exclude = {s.name for s in b}
    seen: set[str] = set()
    out: list[Signal] = []
    for s in a:
        nm = s.name
        if nm in exclude or nm in seen:
            continue
        # Walk up the struct hierarchy: foo.bar.baz → foo.bar → foo
        addit = True
        parent = nm
        while True:
            m = re.match(r'^([^]].+)\.[^.]+$', parent)
            if not m:
                break
            parent = m.group(1)
            if parent in exclude:
                addit = False
                break
        if addit:
            out.append(s)
            seen.add(nm)
    return out


def signals_in(a: list[Signal], b: list[Signal]) -> list[Signal]:
    """Return signals in *a* whose name is also in *b* (de-duplicated)."""
    include = {s.name for s in b}
    seen: set[str] = set()
    out: list[Signal] = []
    for s in a:
        if s.name in include and s.name not in seen:
            out.append(s)
            seen.add(s.name)
    return out


def signals_with(pred: Callable[[Signal], bool], lst: list[Signal]) -> list[Signal]:
    """Filter signals by predicate."""
    return [s for s in lst if pred(s)]


def signals_without(pred: Callable[[Signal], bool], lst: list[Signal]) -> list[Signal]:
    """Reject signals matching predicate."""
    return [s for s in lst if not pred(s)]


def signals_combine_bus(sigs: list[Signal], simplify: bool = True) -> list[Signal]:
    """Merge adjacent-bit signals into bus signals (``verilog-signals-combine-bus``).

    Duplicate signals are also removed.  For example ``A[2]`` and ``A[1]``
    become ``A[2:1]``.

    When *simplify* is True (the default, matching ``verilog-auto-simplify-expressions``),
    range expressions are simplified before numeric extraction so that parametric
    ranges like ``[224*1-1:128*1]`` can be merged.
    """
    sigs = sorted(sigs, key=lambda s: s.name)

    out: list[Signal] = []
    sv_name: Optional[str] = None
    sv_highbit: Optional[int] = None
    sv_lowbit: Optional[int] = None
    sv_busstring: Optional[str] = None
    sv_comment: Optional[str] = None
    sv_memory: Optional[str] = None
    sv_enum: Optional[str] = None
    sv_signed: Optional[str] = None
    sv_type: Optional[str] = None
    sv_multidim: Optional[list[str]] = None
    sv_modport: Optional[str] = None
    combo = ""
    buswarn = ""

    def _simplify(bus: str) -> str:
        return bus  # simplification is a later-phase concern

    def _flush() -> None:
        nonlocal sv_name
        if sv_name is None:
            return
        bits = sv_busstring
        if bits is None and sv_highbit is not None:
            bits = f"[{sv_highbit}:{sv_lowbit}]"
        out.append(Signal(
            name=sv_name,
            bits=bits,
            comment=(sv_comment or "") + combo + buswarn or None,
            memory=sv_memory,
            enum=sv_enum,
            signed=sv_signed,
            type=sv_type,
            multidim=sv_multidim,
            modport=sv_modport,
        ))
        sv_name = None  # type: ignore[assignment]

    for sig in sigs:
        if sv_name is None:
            sv_name = sig.name
            sv_highbit = None
            sv_lowbit = None
            sv_busstring = None
            sv_comment = sig.comment
            sv_memory = sig.memory
            sv_enum = sig.enum
            sv_signed = sig.signed
            sv_type = sig.type
            sv_multidim = sig.multidim
            sv_modport = sig.modport
            combo = ""
            buswarn = ""

        # Extract bus details
        bus = sig.bits
        if bus:
            bus = _simplify(bus)
        highbit: Optional[int] = None
        lowbit: Optional[int] = None

        if bus:
            m = re.match(r"^\[(\d+):(\d+)\]$", bus)
            if m:
                highbit = int(m.group(1))
                lowbit = int(m.group(2))
            else:
                m = re.match(r"^\[(\d+)\]$", bus)
                if m:
                    highbit = lowbit = int(m.group(1))

        if highbit is not None:
            if sv_highbit is not None:
                sv_highbit = max(highbit, sv_highbit)
                sv_lowbit = min(lowbit, sv_lowbit)  # type: ignore[arg-type]
            else:
                sv_highbit = highbit
                sv_lowbit = lowbit
        elif bus:
            sv_busstring = bus

        # Peek ahead — is the *next* signal the same name?
        # We handle this by checking at the start of the next iteration.
        # But we need to flush when the name changes.  We'll restructure
        # with a simpler approach: process and flush at name boundary.

    # Flush last accumulated signal
    _flush()

    # Re-do with proper boundary detection (matching the elisp peek-ahead)
    out.clear()
    sv_name = None
    sv_highbit = None
    sv_lowbit = None
    sv_busstring = None
    sv_comment = None
    sv_memory = None
    sv_enum = None
    sv_signed = None
    sv_type = None
    sv_multidim = None
    sv_modport = None
    combo = ""
    buswarn = ""

    for idx, sig in enumerate(sigs):
        if sv_name is None:
            sv_name = sig.name
            sv_highbit = None
            sv_lowbit = None
            sv_busstring = None
            sv_comment = sig.comment
            sv_memory = sig.memory
            sv_enum = sig.enum
            sv_signed = sig.signed
            sv_type = sig.type
            sv_multidim = sig.multidim
            sv_modport = sig.modport
            combo = ""
            buswarn = ""

        # Extract bus details — simplify for numeric extraction only.
        # Keep original bits for output (Emacs preserves symbolic forms).
        bus = sig.bits
        bus_simplified = simplify_range(bus) if (bus and simplify) else bus
        highbit = None
        lowbit = None

        if bus_simplified:
            m = re.match(r"^\[(\d+):(\d+)\]$", bus_simplified)
            if m:
                highbit = int(m.group(1))
                lowbit = int(m.group(2))
            else:
                m = re.match(r"^\[(\d+)\]$", bus_simplified)
                if m:
                    highbit = lowbit = int(m.group(1))

        if highbit is not None:
            if sv_highbit is not None:
                sv_highbit = max(highbit, sv_highbit)
                sv_lowbit = min(lowbit, sv_lowbit)  # type: ignore[arg-type]
            else:
                sv_highbit = highbit
                sv_lowbit = lowbit
        elif bus:
            if sv_busstring and sv_busstring != bus:
                buswarn = ", Couldn't Merge"
            sv_busstring = bus

        # Peek at next signal
        next_sig = sigs[idx + 1] if idx + 1 < len(sigs) else None
        if next_sig is not None and next_sig.name == sv_name:
            # Same signal — combine
            if next_sig.comment:
                combo = ", ..."
            sv_memory = sv_memory or next_sig.memory
            sv_enum = sv_enum or next_sig.enum
            sv_signed = sv_signed or next_sig.signed
            sv_type = sv_type or next_sig.type
            sv_multidim = sv_multidim or next_sig.multidim
            sv_modport = sv_modport or next_sig.modport
        else:
            # Different signal or end — flush
            bits = sv_busstring
            if bits is None and sv_highbit is not None:
                bits = f"[{sv_highbit}:{sv_lowbit}]"
            comment_str = (sv_comment or "") + combo + buswarn
            out.append(Signal(
                name=sv_name,
                bits=bits,
                comment=comment_str if comment_str else None,
                memory=sv_memory,
                enum=sv_enum,
                signed=sv_signed,
                type=sv_type,
                multidim=sv_multidim,
                modport=sv_modport,
            ))
            sv_name = None

    return out


def signals_sort_by_name(sigs: list[Signal]) -> list[Signal]:
    """Return alphabetically sorted copy."""
    return sorted(sigs, key=lambda s: s.name)


def signals_matching_regexp(sigs: list[Signal], regexp: str) -> list[Signal]:
    """Return signals whose name matches *regexp*."""
    pat = re.compile(regexp)
    return [s for s in sigs if pat.search(s.name)]


def signals_not_matching_regexp(sigs: list[Signal], regexp: str) -> list[Signal]:
    """Return signals whose name does NOT match *regexp*."""
    pat = re.compile(regexp)
    return [s for s in sigs if not pat.search(s.name)]
