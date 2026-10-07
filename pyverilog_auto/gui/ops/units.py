"""Template-structure helpers: guard headers, loop spans, output ranges.

A *unit* is the piece of template text an operation moves: one statement,
a statement together with the ``if`` headers that guard it, or a whole loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...prepro import Construct, LineMap
from ..build import OutputInfo
from ..project import TemplateEntry


@dataclass
class Unit:
    p_first: int                      # template lines removed from the source module
    p_last: int
    lines: list[str]                  # text placed at the destination
    stmts: list[int]                  # indexes of the statements it covers
    capture_line: int                 # a printed line where its variables have their values
    var_first: int                    # template span whose variables are carried
    var_last: int
    headers: list[Construct] = field(default_factory=list)   # guard headers carried with it
    frozen_lines: Optional[list[str]] = None                 # generated text (freeze)
    guards_left: list[Construct] = field(default_factory=list)  # guards that may become empty in the source


def marker_of(out: OutputInfo) -> str:
    entry = out.entry
    if isinstance(entry, TemplateEntry):
        return entry.options().line_id()
    return "// py"


def code_of_line(text: str, marker: str) -> str:
    i = text.find(marker)
    return text[i + len(marker):].strip() if i >= 0 else text.strip()


def header_lines(lm: LineMap, out: OutputInfo, guards: list[Construct]) -> list[str]:
    """Guard headers re-indented for the top level of another template."""
    marker = marker_of(out)
    lines = []
    for level, g in enumerate(guards):
        t = lm.tpl(g.start)
        code = t.code.strip() if t.code is not None else code_of_line(t.text, marker)
        pad = "  " * level if lm.language == "python" else ""
        lines.append(f"{marker} {pad}{code}")
    return lines


def terminator_lines(lm: LineMap, out: OutputInfo, depth: int) -> list[str]:
    marker = marker_of(out)
    if depth == 0:
        return []
    if lm.language == "python":
        return [marker]                       # "// py" alone resets the print indentation
    return [f"{marker} }}"] * depth


def construct_span(lm: LineMap, c: Construct, marker: str = "// pl") -> tuple[int, int]:
    """Template lines of construct *c* including its closing line (Perl brace,
    else/elsif branches)."""
    first, last = c.start, c.end
    if lm.language == "perl":
        chain = c
        while chain.has_else:
            nxt = next((x for x in lm.constructs if x.start in (chain.end + 1, chain.end)
                        and x.kind in ("else", "if") and x is not chain and x.parent == chain.parent), None)
            if nxt is None:
                break
            last = nxt.end
            chain = nxt
        n = last + 1
        if n <= len(lm.lines):
            t = lm.tpl(n)
            if t.code is not None:
                code = t.code.strip()
            else:
                code = code_of_line(t.text, marker) if t.kind == "code" else t.text.strip()
            is_code = t.kind in ("code", "block") or t.code is not None
            if is_code and code.startswith("}") and not code.lstrip("}").strip():
                last = n
    return first, last


def output_range(lm: LineMap, first: int, last: int) -> Optional[tuple[int, int]]:
    """Contiguous output lines produced by template lines first..last, or None
    when the output interleaves with other template lines."""
    outs = [i for i, o in enumerate(lm.out, 1) if o.tpl is not None and first <= o.tpl <= last]
    if not outs:
        return None
    g0, g1 = outs[0], outs[-1]
    for i in range(g0, g1 + 1):
        o = lm.out[i - 1]
        if o.tpl is None or not (first <= o.tpl <= last):
            return None
    return g0, g1


def first_printed(lm: LineMap, first: int, last: int) -> Optional[int]:
    for n in range(first, last + 1):
        if lm.tpl(n).kind == "text" and lm.emit_count(n) > 0:
            return n
    return None


def body_left(lm_lines: list[str], g: Construct, deleted: list[tuple[int, int]]) -> list[str]:
    """Text lines of guard *g* that survive the deletions (template order)."""
    out = []
    for n in range(g.start + 1, g.end + 1):
        if any(a <= n <= b for a, b in deleted):
            continue
        out.append(lm_lines[n - 1])
    return out


def drop_empty_guards(plan, out: OutputInfo, guards: list[Construct], deleted: list[tuple[int, int]],
                      tlines: list[str], template: str, seen: Optional[set[int]] = None) -> None:
    """Delete the headers (and Perl closing braces) of *guards*, innermost
    first, whose bodies are left with nothing but blank or marker lines."""
    lm = out.linemap
    if lm is None:
        return
    seen = seen if seen is not None else set()
    marker = marker_of(out)
    for g in reversed(guards):
        if g.start in seen:
            continue
        seen.add(g.start)
        left = body_left(tlines, g, deleted)
        if any(ln.strip() and not ln.strip().startswith(marker) for ln in left):
            break
        _first, last = construct_span(lm, g, marker)
        if any(a <= g.start <= b for a, b in deleted):
            break
        plan.edits.delete_lines(template, g.start, g.start)
        deleted.append((g.start, g.start))
        if lm.language == "perl" and last > g.end and not any(a <= last <= b for a, b in deleted):
            plan.edits.delete_lines(template, last, last)
            deleted.append((last, last))
        plan.note("N_GUARD_REMOVED", f"the template if at line {g.start} was left empty and removed")


def gen_text(out: OutputInfo, g0: int, g1: int) -> list[str]:
    with open(out.gen_path, "r", encoding="utf-8") as fh:
        lines = fh.read().replace("\r\n", "\n").split("\n")
    return lines[g0 - 1:g1]
