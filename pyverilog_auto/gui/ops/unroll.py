"""Unroll / freeze: replace a template construct by the text it generated.

For an instance printed by a template loop, inside an if/else, or by code,
the innermost construct responsible is replaced by its output lines (the
generated text before AUTO expansion).  The statements become plain text that
wrap and hoist can move.  A trial prepro run of the new template guards
against code after the construct that still needs its variables.
"""

from __future__ import annotations

from typing import Optional

from ...prepro import Construct, PreproError, preprocess_text
from ..srcmap import CODE, LOOP
from .common import OpError, Plan
from .units import construct_span, gen_text, marker_of, output_range


def target_construct(st, lm) -> Optional[Construct]:
    """The construct to freeze for statement *st*."""
    if st.cls == LOOP and st.loop is not None:
        return st.loop
    if st.tpl_start is None:
        return None
    enc = lm.enclosing(st.tpl_start)
    for c in reversed(enc):
        if c.is_loop or c.kind in ("codeblock", "else", "other", "with", "try") or (c.is_cond and c.has_else):
            return c
    # printed by code: the line that printed it is inside the construct
    return enc[-1] if enc else None


def plan_unroll(session, path: str, choices: Optional[dict] = None) -> Plan:
    plan = Plan("unroll", f"unroll the template construct printing {path}")
    try:
        _plan(session, path, plan)
    except OpError as exc:
        plan.error(exc.code, exc.message)
    return plan


def _plan(session, path: str, plan: Plan) -> None:
    sm = session.srcmap
    inst = session.instance(path)
    st = sm.statement_of(inst)
    if st.cls not in (LOOP, CODE):
        raise OpError("E_UNROLL_NONE", f"{path} is {st.cls.lower()} text; nothing to unroll")
    out = st.output
    if out is None or out.kind != "template" or out.linemap is None:
        raise OpError("E_UNROLL_NONE", f"{path} is not generated from a template")
    lm = out.linemap
    if st.cls == CODE and st.gen is not None:
        # a statement printed by code: look at the construct of the printing line
        origins = [lm.origin(n) for n in range(st.gen.line, st.gen.end_line + 1)]
        known = [o for o in origins if o is not None]
        if not known:
            raise OpError("E_UNROLL_UNKNOWN", f"{path}: lines not traceable to the template")
        enc = lm.enclosing(known[0])
        c = None
        for x in reversed(enc):
            if x.kind != "def":
                c = x
                break
        if any(x.kind == "def" for x in enc):
            raise OpError("E_UNROLL_DEF", f"{path} is printed inside a template function; edit the template by hand")
    else:
        c = target_construct(st, lm)
    if c is None:
        raise OpError("E_UNROLL_NONE", f"{path}: no template construct to unroll")
    for outer in lm.enclosing(c.start):
        if outer.is_loop or outer.kind == "def":
            raise OpError("E_UNROLL_NESTED", f"the construct at line {c.start} sits inside the {outer.kind} at "
                                             f"line {outer.start}; unroll that one first")
    first, last = construct_span(lm, c, marker_of(out))
    rng = output_range(lm, first, last)
    if rng is None:
        raise OpError("E_UNROLL_RANGE", f"the output of lines {first}-{last} is not contiguous")
    new_lines = gen_text(out, rng[0], rng[1])
    edits = plan.edits
    tlines = edits.lines(out.src)
    trial = tlines[:first - 1] + new_lines + tlines[last:]
    try:
        preprocess_text("\n".join(trial) + "\n", out.entry.options(out.extra_defines), template_path=out.src,
                        cwd=None, perl=session.project.perl, perl_lib=session.project.perl_lib or None)
    except PreproError as exc:
        raise OpError("E_UNROLL_TRIAL", f"the template no longer runs after unrolling: {exc}") from None
    edits.replace_lines(out.src, first, last, new_lines)
    plan.title = f"unroll the {c.kind} at {session.project.rel(out.src)}:{c.start}"
    plan.summary_lines.append(f"  lines {first}-{last} become {len(new_lines)} generated line(s)")
    plan.focus = path
