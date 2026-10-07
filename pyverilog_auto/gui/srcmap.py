"""Map design objects back to their template lines and classify them.

An instance is found in three places:

* **integ** — the integrated file (connectivity, full pin list);
* **gen** — the prepro output before AUTO expansion, located by parent
  module and instance name in the gen Design;
* **template** — the gen lines mapped through the prepro line map.

The template lines of an instantiation statement get a class that decides
which edit path a structural operation can take:

========  ==============================================================
LITERAL   plain text lines, printed once, outside any template block
SUBST     printed once, with substitution tokens and/or template blocks
          wholly inside the statement (e.g. an optional pin)
GUARDED   printed once inside plain ``if`` blocks (no else)
LOOP      printed by a template loop (one statement per iteration)
CODE      printed by template code, crosses block boundaries, or sits in
          an if/else, def, or code block: not movable as text
========  ==============================================================

Plain sources map to themselves and are always LITERAL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..integ.design import Design
from ..integ.model import Instance, ModuleDef, ModuleRef
from ..prepro import Construct, LineMap, Token
from .build import BuildResult, OutputInfo

LITERAL = "LITERAL"
SUBST = "SUBST"
GUARDED = "GUARDED"
LOOP = "LOOP"
CODE = "CODE"
FILE = "FILE"      # view-only project: the file itself, no template behind it

MOVABLE = (LITERAL, SUBST, GUARDED)


@dataclass
class Loc:
    view: str                 # template | gen | integ
    path: str
    line: int                 # 1-based
    end_line: int
    readonly: bool = False

    def to_json(self) -> dict:
        return {"view": self.view, "path": self.path, "line": self.line, "end_line": self.end_line,
                "readonly": self.readonly}


@dataclass
class Statement:
    """Where an instantiation (or module header) lives and how it can move."""

    cls: str
    reason: str = ""
    template: Optional[str] = None            # file to edit
    tpl_start: Optional[int] = None
    tpl_end: Optional[int] = None
    gen: Optional[Loc] = None
    integ: Optional[Loc] = None
    tokens: list[Token] = field(default_factory=list)
    inner: list[Construct] = field(default_factory=list)    # template blocks inside the statement
    guards: list[Construct] = field(default_factory=list)   # enclosing plain ifs (GUARDED)
    loop: Optional[Construct] = None                        # enclosing loop (LOOP)
    emit_count: int = 1
    output: Optional[OutputInfo] = None

    @property
    def movable(self) -> bool:
        return self.cls in MOVABLE

    @property
    def template_loc(self) -> Optional[Loc]:
        if self.template is None or self.tpl_start is None:
            return None
        return Loc("template", self.template, self.tpl_start, self.tpl_end or self.tpl_start)

    def tag(self) -> str:
        if self.cls in (LITERAL, FILE):
            return ""
        if self.cls == LOOP:
            return f"Lx{self.emit_count}"
        return {SUBST: "S", GUARDED: "G", CODE: "C"}.get(self.cls, "?")

    def to_json(self) -> dict:
        return {
            "class": self.cls, "reason": self.reason, "template": self.template,
            "tpl_start": self.tpl_start, "tpl_end": self.tpl_end,
            "gen": self.gen.to_json() if self.gen else None,
            "integ": self.integ.to_json() if self.integ else None,
            "tokens": [t.raw for t in self.tokens],
            "guards": [g.header for g in self.guards],
            "loop": self.loop.header if self.loop else None,
            "emit_count": self.emit_count, "tag": self.tag(),
        }


def _end_line(design: Design, rng) -> int:
    sf = design.files[rng.file]
    end = max(rng.start, rng.end - 1)
    return sf.line_col(end)[0]


class SourceMap:
    def __init__(self, build: BuildResult):
        self.build = build
        self._counts: dict[int, dict[int, int]] = {}

    # -- helpers ----------------------------------------------------------

    @property
    def design(self) -> Design:
        assert self.build.design is not None
        return self.build.design

    def output_of_key(self, key: str) -> Optional[OutputInfo]:
        sf = self.design.files.get(key)
        return self.build.by_integ(sf.path) if sf is not None else None

    def _counts_of(self, lm: LineMap) -> dict[int, int]:
        c = self._counts.get(id(lm))
        if c is None:
            c = self._counts[id(lm)] = lm.emit_counts()
        return c

    def gen_module(self, name: str) -> Optional[ModuleDef]:
        gd = self.build.gen_design
        return gd.modules.get(name) if gd is not None else None

    def gen_ref(self, parent_module: str, ref: ModuleRef) -> Optional[ModuleRef]:
        gm = self.gen_module(parent_module)
        if gm is None:
            return None
        same = [r for r in gm.refs if r.kind in ("inst", "bind") and r.inst_name == ref.inst_name
                and r.module == ref.module]
        if not same:
            return None
        if len(same) == 1:
            return same[0]
        im = self.design.modules.get(parent_module)
        if im is not None:
            peers = [r for r in im.refs if r.kind in ("inst", "bind") and r.inst_name == ref.inst_name
                     and r.module == ref.module]
            for i, r in enumerate(peers):
                if r is ref and i < len(same):
                    return same[i]
        return same[0]

    # -- classification ------------------------------------------------------

    def classify_gen_span(self, out: OutputInfo, gen_start: int, gen_end: int) -> Statement:
        st = Statement(cls=CODE, output=out, gen=Loc("gen", out.gen_path, gen_start, gen_end, True))
        if out.kind == "source":
            st.cls = LITERAL
            st.template = out.src
            st.tpl_start, st.tpl_end = gen_start, gen_end
            return st
        if out.kind != "template" or out.linemap is None:
            st.reason = "not generated from a template of the project"
            return st
        lm = out.linemap
        st.template = out.src
        origins = [lm.origin(n) for n in range(gen_start, gen_end + 1)]
        known = [o for o in origins if o is not None]
        if known:
            st.tpl_start, st.tpl_end = min(known), max(known)
        if len(known) != len(origins):
            st.reason = "some lines are not traceable to the template"
            return st
        if any(lm.tpl(o).kind != "text" for o in origins):
            st.reason = "printed by template code"
            return st
        if origins != sorted(origins):
            st.reason = "template lines are emitted out of order"
            return st
        first, last = origins[0], origins[-1]
        for n in range(first, last + 1):
            if lm.tpl(n).kind in ("begin", "end", "block"):
                st.reason = f"a code block (line {n}) is inside the statement"
                return st
        enclosing = lm.enclosing(first)
        for c in enclosing:
            if not c.contains(last):
                st.reason = f"the statement crosses the end of the template block at line {c.start}"
                return st
        inner = [c for c in lm.constructs if first <= c.start <= last]
        for c in inner:
            if c.end > last:
                st.reason = f"the template block at line {c.start} continues after the statement"
                return st
        counts = self._counts_of(lm)
        st.emit_count = max(counts.get(o, 0) for o in set(origins))
        st.inner = inner
        st.tokens = [t for n in range(first, last + 1) for t in lm.tpl(n).tokens]
        loops = [c for c in enclosing if c.is_loop]
        bad = [c for c in enclosing if not (c.is_loop or c.is_cond)]
        if bad:
            st.reason = f"inside a template '{bad[-1].kind}' block (line {bad[-1].start})"
            return st
        if loops or st.emit_count > 1:
            if not loops:
                st.reason = "printed more than once outside a loop"
                return st
            st.loop = loops[-1]
            st.guards = [c for c in enclosing if c.is_cond]
            st.cls = LOOP
            st.reason = f"printed by the loop at line {st.loop.start}"
            return st
        conds = [c for c in enclosing if c.is_cond]
        if conds:
            if any(c.has_else for c in conds):
                st.reason = "inside an if/else template block (freeze to move)"
                return st
            st.guards = conds
            st.cls = GUARDED
            return st
        st.cls = SUBST if (st.tokens or inner) else LITERAL
        return st

    def statement_of(self, inst: Instance) -> Statement:
        """Class and locations of the statement that instantiates *inst*."""
        d = self.design
        if inst.parent is None or inst.ref is None or inst.parent.module is None:
            st = Statement(cls=CODE, reason="top-level instance (no instantiation statement)")
            m = inst.module
            if m is not None:
                st.integ = Loc("integ", d.files[m.file].path, m.keyword_range.line, _end_line(d, m.end_range), True)
            return st
        parent = inst.parent.module
        ref = inst.ref
        out = self.output_of_key(ref.range.file)
        integ = Loc("integ", d.files[ref.range.file].path, ref.range.line, _end_line(d, ref.range), True)
        if out is None:
            return Statement(cls=CODE, reason="file is not part of the build", integ=integ)
        if out.kind == "file":
            integ.readonly = False
            return Statement(cls=FILE, reason="view-only project (no templates)", integ=integ, output=out)
        if inst.gen_path or ref.in_generate or ref.is_array:
            st = Statement(cls=CODE, reason="generate blocks and instance arrays are not supported yet",
                           integ=integ, output=out)
            self._attach_template(st, out, parent.name, ref)
            return st
        gref = self.gen_ref(parent.name, ref)
        if gref is None:
            st = Statement(cls=CODE, reason="instantiation not found in the generated file", integ=integ, output=out)
            return st
        gd = self.build.gen_design
        assert gd is not None
        st = self.classify_gen_span(out, gref.range.line, _end_line(gd, gref.range))
        st.integ = integ
        return st

    def _attach_template(self, st: Statement, out: OutputInfo, parent: str, ref: ModuleRef) -> None:
        gref = self.gen_ref(parent, ref)
        gd = self.build.gen_design
        if gref is None or gd is None:
            return
        g0, g1 = gref.range.line, _end_line(gd, gref.range)
        st.gen = Loc("gen", out.gen_path, g0, g1, True)
        if out.kind == "source":
            st.template, st.tpl_start, st.tpl_end = out.src, g0, g1
        elif out.linemap is not None:
            known = [o for o in (out.linemap.origin(n) for n in range(g0, g1 + 1)) if o is not None]
            if known:
                st.template, st.tpl_start, st.tpl_end = out.src, min(known), max(known)

    # -- locations -----------------------------------------------------------

    def module_locs(self, name: str) -> dict[str, Loc]:
        """Header location of module *name* in every view."""
        d = self.design
        locs: dict[str, Loc] = {}
        m = d.modules.get(name)
        if m is None:
            return locs
        path = d.files[m.file].path
        locs["integ"] = Loc("integ", path, m.keyword_range.line, _end_line(d, m.end_range), True)
        out = self.build.by_integ(path)
        if out is None:
            return locs
        if out.kind == "file":
            locs["integ"].readonly = False
            return locs
        gm = self.gen_module(name)
        gd = self.build.gen_design
        if gm is None or gd is None:
            return locs
        g0, g1 = gm.keyword_range.line, _end_line(gd, gm.end_range)
        locs["gen"] = Loc("gen", out.gen_path, g0, g1, True)
        if out.kind == "source":
            locs["template"] = Loc("template", out.src, g0, g1)
        elif out.linemap is not None:
            t0 = out.linemap.origin(g0)
            t1 = out.linemap.origin(g1)
            if t0 is not None:
                locs["template"] = Loc("template", out.src, t0, max(t1 or t0, t0))
        return locs

    def instance_locs(self, inst: Instance) -> dict[str, Loc]:
        """Instantiation-statement locations (falls back to the module header for tops)."""
        if inst.parent is None or inst.ref is None:
            return self.module_locs(inst.module_name)
        st = self.statement_of(inst)
        locs: dict[str, Loc] = {}
        if st.integ:
            locs["integ"] = st.integ
        if st.gen:
            locs["gen"] = st.gen
        if st.template_loc:
            locs["template"] = st.template_loc
        return locs

    def template_of_module(self, name: str) -> Optional[OutputInfo]:
        m = self.design.modules.get(name)
        if m is None:
            return None
        return self.build.by_integ(self.design.files[m.file].path)


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))
