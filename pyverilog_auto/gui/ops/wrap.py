"""Wrap sibling instances into a new wrapper module.

For instances S of parent module P the plan

1. finds every local name S uses (pins and parameter overrides);
2. turns each name into a wrapper port (used outside S, or a port or
   interface of P), an internal net (declaration moves into the wrapper), or
   a forwarded parameter;
3. writes a new wrapper template containing S's template text verbatim
   (tokens, blocks and ``/*AUTOINST*/`` survive and expand at the next build);
4. replaces S in P's template by one instance of the wrapper, deletes the
   now-internal declarations, and adds the wrapper to the project manifest.

Template structure: statements guarded by the same ``if`` keep the guard in
P around the wrapper instance; otherwise a guard travels with its statement
into the wrapper.  A statement printed by a loop moves with the whole loop
(choice ``loop=group``) or the loop is unrolled first (``loop=unroll``);
statements printed by template code are frozen first (``code=freeze``).

Port naming ``net`` names each port after the parent net; ``inst_port``
uses ``<inst>_<port>`` and aliases the port to the original net name inside
the wrapper with an ``assign``, so the instance text still moves verbatim.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from ...integ.model import Instance
from ..build import OutputInfo
from ..connect import ConnectError, Decl, InstUse, ModuleIndex
from ..project import Binding, SourceEntry, TemplateEntry, append_template_entry_text
from ..srcmap import CODE, GUARDED, LITERAL, LOOP, SUBST, Statement
from .common import (OpError, Plan, Question, capture_values, free_variables, identifiers, indent_of,
                     literal_safe, unique_name)
from .textops import rewrite_pin
from .units import (Unit, construct_span, drop_empty_guards, first_printed, gen_text, header_lines, marker_of,
                    output_range, terminator_lines)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_DIR_ORDER = {"input": 0, "inout": 1, "output": 2, "": 3}


@dataclass
class WPort:
    name: str            # port name in the wrapper
    net: str             # name in the parent (connection expression)
    direction: str       # input | output | inout | "" (interface)
    type_text: str       # "logic", "wire", "" ... or the interface type
    dims: str = ""
    unpacked: str = ""
    iface: bool = False
    alias: bool = False  # inst_port naming: assign between port and net inside the wrapper

    def decl(self) -> tuple[str, str]:
        if self.iface:
            return "", self.type_text
        return self.direction, f"{self.type_text} {self.dims}".strip()


def _fmt_ports(ports: list[WPort]) -> list[str]:
    """``direction type name`` columns; interface ports leave the direction blank."""
    cols = [p.decl() for p in ports]
    w0 = max((len(d) for d, _t in cols), default=0)
    w1 = max((len(t) for _d, t in cols), default=0)
    out = []
    for p, (d, t) in zip(ports, cols):
        if p.iface:
            head = t.ljust(w0 + (1 if w0 else 0) + w1)
        else:
            head = " ".join(x for x in (d.ljust(w0) if w0 else "", t.ljust(w1) if w1 else "") if x)
        out.append(f"{head} {p.name}{p.unpacked}".rstrip())
    return out


def _wrapper_text(module: str, origin: str, params: list[tuple[str, str]], ports: list[WPort],
                  internal: list[str], aliases: list[str], bodies: list[list[str]], indent: str,
                  imports: list[str] = ()) -> str:
    lines = [f"// {module}: wrapper created by rtl-integ-gui from {origin}", f"module {module}"]
    if imports:
        lines.append("  import " + ", ".join(imports) + ";")
    if params:
        plines = [f"parameter {n} = {v}" for n, v in params]
        lines.append("  #(" + (",\n    ".join(plines)) + ")")
    plist = _fmt_ports(ports)
    if plist:
        lines.append("  (" + (",\n   ".join(plist)) + ");")
    else:
        lines.append("  ();")
    lines.append("")
    if internal:
        lines.extend(indent + s for s in internal)
        lines.append("")
    if aliases:
        lines.extend(indent + s for s in aliases)
        lines.append("")
    for i, body in enumerate(bodies):
        if i:
            lines.append("")
        lines.extend(body)
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _instance_lines(module: str, inst: str, ports: list[WPort], params: list[str], indent: str,
                    autoinst: bool) -> list[str]:
    head = module
    if params:
        head += " #(" + ", ".join(f".{p}({p})" for p in params) + ")"
    if autoinst:
        return [f"{indent}{head} {inst} (/*AUTOINST*/);"]
    if not ports:
        return [f"{indent}{head} {inst} ();"]
    w = max(len(p.name) for p in ports)
    pins = [f".{p.name.ljust(w)} ({p.net})" for p in ports]
    out = [f"{indent}{head} {inst}"]
    for i, pin in enumerate(pins):
        lead = "  (" if i == 0 else "   "
        tail = ");" if i == len(pins) - 1 else ","
        out.append(f"{indent}{lead}{pin}{tail}")
    return out


class WrapPlanner:
    def __init__(self, session, paths: list[str], module: str, instance: str, choices: Optional[dict] = None,
                 port_naming: Optional[str] = None):
        self.s = session
        self.paths = list(paths)
        self.module = module
        self.instance = instance
        self.choices = dict(choices or {})
        self.proj = session.project
        self.res = session.result
        self.sm = session.srcmap
        self.design = self.res.design
        self.port_naming = port_naming or self.choices.get("port_naming") or self.proj.wrap.port_naming
        names = ", ".join(p.rsplit(".", 1)[-1] for p in self.paths)
        self.plan = Plan("wrap", f"wrap {names} into {module} {instance}")
        self.rewrites: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------

    def run(self) -> Plan:
        try:
            self._run()
        except OpError as exc:
            self.plan.error(exc.code, exc.message)
        except ConnectError as exc:
            self.plan.error("E_CONNECT", str(exc))
        return self.plan

    def _fail(self, code: str, message: str):
        raise OpError(code, message)

    def _resolve(self) -> tuple[Instance, list[Instance]]:
        if self.proj.view_only:
            self._fail("E_VIEW_ONLY", "the project has no templates (view-only)")
        if not self.paths:
            self._fail("E_WRAP_EMPTY", "no instances selected")
        if len(set(self.paths)) != len(self.paths):
            self._fail("E_WRAP_DUP", "an instance is selected twice")
        insts = [self.s.instance(p) for p in self.paths]
        parents = {i.parent.path if i.parent is not None else None for i in insts}
        if None in parents:
            self._fail("E_WRAP_TOP", "a top-level instance cannot be wrapped")
        if len(parents) != 1:
            self._fail("E_WRAP_NOT_SIBLINGS", "the instances have different parents: " + ", ".join(sorted(parents)))
        parent = insts[0].parent
        assert parent is not None
        if parent.module is None or parent.is_blackbox:
            self._fail("E_WRAP_PARENT", f"{parent.path} has no module body")
        for name, what in ((self.module, "module"), (self.instance, "instance")):
            if not _IDENT_RE.match(name or ""):
                self._fail("E_WRAP_NAME", f"{what} name {name!r} is not a plain identifier")
        if self.module in self.design.modules:
            self._fail("E_WRAP_MODULE_EXISTS", f"module {self.module} already exists")
        return parent, insts

    def _then(self, path: str, op: str) -> None:
        """Replace the plan by an unroll/freeze of *path*'s construct, then wrap again."""
        from .unroll import plan_unroll

        unroll = plan_unroll(self.s, path)
        choices = {k: v for k, v in self.choices.items() if k not in ("loop", "code")}
        unroll.then = {"method": "plan_wrap", "params": {"paths": self.paths, "module": self.module,
                                                          "instance": self.instance, "choices": choices,
                                                          "port_naming": self.port_naming}}
        unroll.note("N_THEN", f"after the {op}, the wrap is planned again")
        self.plan = unroll

    def _run(self) -> None:
        parent, insts = self._resolve()
        P = parent.module
        assert P is not None
        ix = ModuleIndex(self.design, P)
        if self.instance in ix.insts or self.instance in ix.decls or self.instance in P.symbols:
            self._fail("E_WRAP_INST_EXISTS", f"{P.name} already has a {self.instance}")
        uses, stmts = self._collect(insts, ix)

        # template structure first: loops / code may change what is selected
        for k, st in enumerate(stmts):
            if st.cls == CODE:
                choice = self.choices.get("code")
                if choice == "freeze":
                    return self._then(insts[k].path, "freeze")
                if choice is None:
                    self.plan.question = Question(
                        "code", f"{insts[k].path} is {st.reason or 'produced by template code'}. Freeze that "
                                "template code into plain text first?", ["freeze", "cancel"])
                    return
                self._fail("E_TPL_CODE", f"{insts[k].path}: {st.reason}")
        loops = [k for k, st in enumerate(stmts) if st.cls == LOOP]
        if loops:
            k = loops[0]
            choice = self.choices.get("loop")
            if choice == "unroll":
                return self._then(insts[k].path, "unroll")
            if choice is None:
                n = stmts[k].emit_count
                self.plan.question = Question(
                    "loop", f"{insts[k].path} is printed by the template loop at line {stmts[k].loop.start} "
                            f"({n} instance(s)). Wrap the whole loop, or unroll the loop first?",
                    ["group", "unroll", "cancel"])
                return
            if choice != "group":
                self._fail("E_TPL_LOOP", f"{insts[k].path}: {stmts[k].reason}")
            insts, uses, stmts = self._add_loop_members(parent, ix, insts, uses, stmts)

        out: OutputInfo = stmts[0].output
        template = stmts[0].template
        if any(st.template != template for st in stmts):
            self._fail("E_WRAP_FILES", "the instances come from different files")
        order = sorted(range(len(insts)), key=lambda k: stmts[k].tpl_start)
        insts = [insts[k] for k in order]
        uses = [uses[k] for k in order]
        stmts = [stmts[k] for k in order]
        units, inside_guard = self._units(out, stmts)

        ports, internal, params = self._boundary(ix, uses)
        self._plan_edits(parent, P, ix, insts, uses, stmts, units, inside_guard, out, template, params, ports,
                         internal)

    # -- selection ----------------------------------------------------------------------

    def _collect(self, insts: list[Instance], ix: ModuleIndex) -> tuple[list[InstUse], list[Statement]]:
        uses: list[InstUse] = []
        stmts: list[Statement] = []
        for inst in insts:
            use = ix.insts.get(inst.name)
            if use is None:
                self._fail("E_WRAP_NOT_FOUND", f"{inst.path}: instantiation not found in {ix.module.name}")
            if use.ordered:
                self._fail("E_ORDERED", f"{inst.path} uses positional connections")
            if use.shared_statement:
                self._fail("E_SHARED_STMT", f"{inst.path} shares its statement with other instances")
            if use.in_generate:
                self._fail("E_GENERATE", f"{inst.path} is inside a generate block")
            uses.append(use)
            stmts.append(self.sm.statement_of(inst))
        return uses, stmts

    def _add_loop_members(self, parent: Instance, ix: ModuleIndex, insts, uses, stmts):
        loop_starts = {st.loop.start for st in stmts if st.cls == LOOP}
        have = {i.path for i in insts}
        for child in parent.children:
            if child.path in have:
                continue
            st = self.sm.statement_of(child)
            if st.cls == LOOP and st.loop is not None and st.loop.start in loop_starts and \
                    st.template == stmts[0].template:
                use = ix.insts.get(child.name)
                if use is None:
                    continue
                insts.append(child)
                uses.append(use)
                stmts.append(st)
                self.plan.note("N_LOOP_MEMBER", f"{child.path} is printed by the same loop and is wrapped too")
        return insts, uses, stmts

    def _units(self, out: OutputInfo, stmts: list[Statement]) -> tuple[list[Unit], bool]:
        edits = self.plan.edits
        lm = out.linemap
        chains = [tuple(g.start for g in st.guards) if st.cls == GUARDED else () for st in stmts]
        inside_guard = all(c and c == chains[0] for c in chains) and not any(st.cls == LOOP for st in stmts)
        units: list[Unit] = []
        done: set[int] = set()
        for k, st in enumerate(stmts):
            if k in done:
                continue
            if st.cls == LOOP:
                assert lm is not None and st.loop is not None
                if any(c.is_cond for c in lm.enclosing(st.loop.start)):
                    self._fail("E_WRAP_LOOP_GUARD", f"the loop at line {st.loop.start} is inside a template if; "
                                                    "unroll it first")
                first, last = construct_span(lm, st.loop, marker_of(out))
                members = [j for j, s2 in enumerate(stmts) if s2.cls == LOOP and s2.loop.start == st.loop.start]
                done.update(members)
                self._check_loop_output(out, first, last, [stmts[j] for j in members])
                lines = edits.line_range_text(out.src, first, last) + terminator_lines(lm, out, 0 if
                                                                                         lm.language == "perl" else 1)
                rng = output_range(lm, first, last)
                cap = first_printed(lm, first, last) or st.tpl_start
                units.append(Unit(first, last, lines, members, cap, first, last,
                                  frozen_lines=gen_text(out, rng[0], rng[1]) if rng else None))
                continue
            done.add(k)
            body = edits.line_range_text(st.template, st.tpl_start, st.tpl_end)
            frozen = gen_text(out, st.gen.line, st.gen.end_line) if st.gen is not None else None
            if st.cls == GUARDED and not inside_guard:
                assert lm is not None
                lines = header_lines(lm, out, st.guards) + body + terminator_lines(lm, out, len(st.guards))
                units.append(Unit(st.tpl_start, st.tpl_end, lines, [k], st.tpl_start, st.tpl_start, st.tpl_end,
                                  headers=list(st.guards), frozen_lines=frozen, guards_left=list(st.guards)))
            else:
                units.append(Unit(st.tpl_start, st.tpl_end, body, [k], st.tpl_start, st.tpl_start, st.tpl_end,
                                  frozen_lines=frozen))
        units.sort(key=lambda u: u.p_first)
        for a, b in zip(units, units[1:]):
            if b.p_first <= a.p_last:
                self._fail("E_WRAP_OVERLAP", "two selected instances share template lines")
        return units, inside_guard

    def _check_loop_output(self, out: OutputInfo, first: int, last: int, members: list[Statement]) -> None:
        lm = out.linemap
        rng = output_range(lm, first, last)
        if rng is None:
            self._fail("E_WRAP_LOOP_RANGE", f"the output of the loop at line {first} is not contiguous")
        covered = set()
        for st in members:
            if st.gen is not None:
                covered.update(range(st.gen.line, st.gen.end_line + 1))
        for n, line in zip(range(rng[0], rng[1] + 1), gen_text(out, rng[0], rng[1])):
            s = line.strip()
            if n not in covered and s and not s.startswith("//"):
                self._fail("E_WRAP_LOOP_CONTENT", f"the loop at line {first} also prints {s!r}; unroll it first")

    # -- boundary -------------------------------------------------------------------------

    def _boundary(self, ix: ModuleIndex, uses: list[InstUse]):
        owners = ix.inst_owners(u.name for u in uses)

        def external_name(n: str) -> bool:
            d = ix.decls.get(n)
            if ix.is_imported(n) or (d is not None and d.kind in ("param", "localparam", "typedef", "genvar")):
                return False
            return (d is not None and d.kind in ("port", "iface_inst")) or ix.used_outside(n, owners)

        # Outputs that drive only part of an external net (or a concatenation) get
        # their own wrapper port; the pin inside the wrapper is rewritten to it.
        taken: set[str] = set()
        expr_ports: list[WPort] = []
        for u in uses:
            for p in u.pins:
                if p.direction not in ("output", "inout") or not p.names:
                    continue
                if not self._is_partial(p, ix) or not any(external_name(n) for n in p.names):
                    continue
                if p.in_fence:
                    self._fail("E_WRAP_PARTIAL_AUTO", f"{u.name}.{p.port} drives part of {p.expr} through an "
                                                      "AUTOINST connection; write that pin explicitly first")
                width = self._port_width(u, p.port)
                pname = unique_name(f"{u.name}_{p.port}", taken | set(ix.decls) | set(ix.insts))
                taken.add(pname)
                expr_ports.append(WPort(pname, p.expr, p.direction, "logic" if p.direction == "output" else "wire",
                                        width))
                self.rewrites[(u.name, p.port)] = pname

        names: list[str] = []
        for u in uses:
            for p in u.pins:
                if (u.name, p.port) in self.rewrites:
                    continue
                for n in p.names:
                    if n not in names:
                        names.append(n)
            for ns in u.param_names.values():
                for n in ns:
                    if n not in names:
                        names.append(n)

        params: list[str] = []

        def add_param(n: str) -> None:
            if n in params:
                return
            d = ix.decls.get(n)
            if d is None or d.kind not in ("param", "localparam"):
                return
            for dep in identifiers(d.value or ""):
                add_param(dep)
            params.append(n)

        ports: list[WPort] = list(expr_ports)
        internal: list[tuple[str, Optional[Decl], str]] = []
        for n in names:
            if ix.is_imported(n):
                continue        # a package's type, parameter or enum value: the wrapper imports it too
            d = ix.decls.get(n)
            if d is not None and d.kind in ("param", "localparam"):
                add_param(n)
                continue
            if d is not None and d.kind in ("typedef", "genvar"):
                self._fail("E_WRAP_TYPE", f"{n} is a {d.kind}; wrapping it is not supported")
            pins = [(u, p) for u in uses for p in u.pins if n in p.names]
            dirs = {p.direction for _u, p in pins}
            is_iface = (d is not None and d.kind == "iface_inst") or (d is not None and d.iface_type is not None) \
                or any(self._child_port_is_iface(u, p.port) for u, p in pins)
            external = (d is not None and d.kind in ("port", "iface_inst")) or ix.used_outside(n, owners)
            type_text, dims, unpacked = self._type_of(n, d, pins, uses)
            for t in identifiers(dims) + identifiers(unpacked):
                add_param(t)
            if not external:
                if is_iface:
                    self._fail("E_WRAP_IFACE", f"interface {n} is used only by the wrapped instances; "
                                               "move its instantiation first")
                internal.append((n, d, f"{f'{type_text} {dims}'.strip()} {n}{unpacked};"))
                continue
            if is_iface:
                itype = (d.type_text if d is not None and d.kind == "iface_inst" else (d.iface_type if d else None))
                if not itype:
                    itype = self._iface_type_from_child(pins)
                if d is not None and d.modport:
                    itype = f"{itype}.{d.modport}"
                ports.append(WPort(n, n, "", itype or "interface", iface=True))
                taken.add(n)
                continue
            direction = "inout" if "inout" in dirs else "output" if "output" in dirs else "input"
            pname = n
            alias = False
            if self.port_naming == "inst_port" and direction in ("input", "output"):
                u0, p0 = pins[0]
                pname = f"{u0.name}_{p0.port}"
                alias = pname != n
            k = 1
            base = pname
            while pname in taken or (alias and pname in names):
                pname = f"{base}_{k}"
                k += 1
            taken.add(pname)
            ports.append(WPort(pname, n, direction, type_text, dims, unpacked, alias=alias))
        ports.sort(key=lambda p: (_DIR_ORDER.get(p.direction, 3), ))
        return ports, internal, params

    # -- helpers ------------------------------------------------------------------------

    _SIMPLE_SEL = re.compile(r"^\s*([A-Za-z_][\w$]*)\s*(\[[^\[\]]*\])?\s*$")

    def _is_partial(self, pin, ix: ModuleIndex) -> bool:
        """True unless the expression is a plain name or a select of its whole range."""
        m = self._SIMPLE_SEL.match(pin.expr)
        if m is None:
            return True
        sel = m.group(2)
        if not sel:
            return False
        d = ix.decls.get(m.group(1))
        dims = d.dims if d is not None else ""
        return re.sub(r"\s+", "", sel) != re.sub(r"\s+", "", dims)

    def _inst_of_use(self, use: InstUse) -> Optional[Instance]:
        parent = self.s.instance(self.paths[0]).parent
        for c in parent.children if parent is not None else []:
            if c.name == use.name:
                return c
        return None

    def _port_width(self, use: InstUse, port: str) -> str:
        inst = self._inst_of_use(use)
        if inst is not None:
            try:
                return self.design.port_at(inst, port).packed_dims_numeric
            except Exception:
                pass
        m = self.design.modules.get(use.module)
        info = m.port(port) if m is not None else None
        return info.packed_dims if info is not None else ""

    def _child_port_is_iface(self, use: InstUse, port: str) -> bool:
        m = self.design.modules.get(use.module)
        info = m.port(port) if m is not None else None
        return bool(info is not None and info.is_interface)

    def _iface_type_from_child(self, pins) -> Optional[str]:
        for u, p in pins:
            m = self.design.modules.get(u.module)
            info = m.port(p.port) if m is not None else None
            if info is not None and info.iface_type:
                return info.iface_type
        return None

    def _type_of(self, n: str, d: Optional[Decl], pins, uses) -> tuple[str, str, str]:
        if d is not None and d.kind in ("port", "var", "net"):
            t = d.type_text or ""
            if d.kind == "port" and not t:
                t = "wire" if d.direction != "output" else "logic"
            return t or "logic", d.dims, d.unpacked
        # implicit net (no declaration): elaborated width of the first child port it connects to
        for u, p in pins:
            inst = self._inst_of_use(u)
            if inst is None:
                continue
            try:
                ep = self.design.port_at(inst, p.port)
            except Exception:
                continue
            if ep.kind == "iface":
                return ep.iface_type or "", "", ""
            if p.expr.strip() == n:
                return "logic", ep.packed_dims_numeric, ""
        return "logic", "", ""

    def _carry_or_freeze(self, out: OutputInfo, units: list[Unit]) -> tuple[list[list[str]], Optional[Binding]]:
        """Destination text of each unit, and the binding for carried variables."""
        plan = self.plan
        bodies = [list(u.lines) for u in units]
        if out.kind != "template" or out.linemap is None:
            return bodies, None
        lm = out.linemap
        lang = out.entry.lang
        variables: list[str] = []
        for u in units:
            for v in free_variables(lang, lm, u.var_first, u.var_last, u.headers):
                if v not in variables:
                    variables.append(v)
        if not variables:
            return bodies, None
        mode = self.choices.get("subst")
        if mode == "freeze":
            frozen = []
            for u in units:
                if u.frozen_lines is None:
                    raise OpError("E_FREEZE", f"no generated text for template lines {u.p_first}-{u.p_last}")
                frozen.append(list(u.frozen_lines))
            plan.note("N_FROZEN", "template variables were substituted (frozen) in the wrapper: " + ", ".join(variables))
            return frozen, None
        problem = None
        try:
            values = capture_values(self.proj, out, [u.capture_line for u in units], variables)
        except OpError as exc:
            values, problem = {}, exc.message
        first = values.get(units[0].capture_line, {})
        if problem is None:
            for v in variables:
                val = first.get(v)
                if val is None or not literal_safe(lang, val):
                    problem = f"the value of {v} cannot be carried"
                    break
                for u in units[1:]:
                    if values.get(u.capture_line, {}).get(v) != val:
                        problem = f"{v} has different values at the wrapped statements"
                        break
                if problem:
                    break
        if problem is not None:
            if mode is None:
                plan.question = Question(
                    "subst", f"{problem}. Freeze the template text (substitute the generated values)?",
                    ["freeze", "cancel"])
                return bodies, None
            raise OpError("E_CARRY", problem)
        plan.note("N_CARRY", "template variables carried into the wrapper: "
                  + ", ".join(f"{v} = {first[v]}" for v in variables))
        return bodies, Binding(src=out.src, line=units[0].capture_line, vars=variables)

    # -- edits -------------------------------------------------------------------------

    def _plan_edits(self, parent: Instance, P, ix: ModuleIndex, insts: list[Instance], uses: list[InstUse],
                    stmts: list[Statement], units: list[Unit], inside_guard: bool, out: OutputInfo,
                    template: str, params: list[str], ports: list[WPort], internal) -> None:
        plan = self.plan
        edits = plan.edits
        proj = self.proj
        bodies, binding = self._carry_or_freeze(out, units)
        if plan.question is not None:
            return
        for ui, unit in enumerate(units):
            for k in unit.stmts:
                u = uses[k]
                for (iname, port), pname in self.rewrites.items():
                    if iname != u.name:
                        continue
                    if len(unit.stmts) > 1:
                        raise OpError("E_WRAP_LOOP_PARTIAL", f"{u.name}.{port} drives part of a net from inside a "
                                                             "template loop; unroll the loop first")
                    new = rewrite_pin(bodies[ui], port, pname)
                    if new is None:
                        raise OpError("E_WRAP_PARTIAL_AUTO", f"pin {u.name}.{port} is not written in the template text")
                    bodies[ui] = new
        tlines = edits.lines(template)
        indent = indent_of(tlines[stmts[0].tpl_start - 1]) or "   "

        # wrapper file and manifest entry
        ext = os.path.splitext(template)[1] or ".sv"
        wdir = proj.wrap.wrapper_dir or os.path.dirname(template)
        wpath = os.path.join(wdir, self.module + ext)
        if os.path.exists(wpath):
            raise OpError("E_WRAP_FILE_EXISTS", f"{proj.rel(wpath)} already exists")
        out_ext = ".v" if out.out.endswith(".v") else ".sv"
        wout = os.path.join(os.path.dirname(out.out), self.module + out_ext)
        if proj.entry_for_output(wout) is not None:
            raise OpError("E_WRAP_FILE_EXISTS", f"output {wout} already exists")
        if out.kind == "template":
            t_entry = out.entry
            entry = TemplateEntry(src=wpath, out=wout, lang=t_entry.lang, replace=list(t_entry.replace),
                                  args=list(t_entry.args), bind=binding, created_by_gui=True,
                                  syntax=t_entry.syntax)
        else:
            entry = SourceEntry(path=wpath, out=wout, created_by_gui=True)

        param_vals = []
        for p in params:
            d = ix.decls[p]
            param_vals.append((p, d.value if d.value is not None else "0"))
        decl_lines = [text for _n, _d, text in internal]
        alias_lines = []
        for p in ports:
            if p.alias:
                decl_lines.append(f"{f'{p.type_text} {p.dims}'.strip()} {p.net}{p.unpacked};")
                if p.direction == "input":
                    alias_lines.append(f"assign {p.net} = {p.name};")
                else:
                    alias_lines.append(f"assign {p.name} = {p.net};")
        origin = f"{P.name} ({proj.rel(template)})"
        wtext = _wrapper_text(self.module, origin, param_vals, ports, decl_lines, alias_lines, bodies, indent,
                              ix.imports)
        edits.add_file(wpath, wtext)
        with open(proj.path, "r", encoding="utf-8", newline="") as fh:
            manifest = fh.read()
        edits.set_text(proj.path, append_template_entry_text(manifest.replace("\r\n", "\n"), proj, entry))

        # parent template: the wrapper instance replaces the first unit (or the
        # last, when a name it connects is declared after the first), the others go
        autoinst = self.port_naming == "net" and any(u.uses_autoinst for u in uses) and \
            all(not p.alias for p in ports)
        at = self._instance_position(ix, uses, units, ports, params)
        inst_indent = indent_of(tlines[units[at].p_first - 1]) or indent
        inst_lines = _instance_lines(self.module, self.instance, ports, params, inst_indent, autoinst)
        edits.replace_lines(template, units[at].p_first, units[at].p_last, inst_lines)
        deleted: list[tuple[int, int]] = [(units[at].p_first, units[at].p_last)]
        for unit in units[:at] + units[at + 1:]:
            a, b = unit.p_first, unit.p_last
            if a >= 2 and not tlines[a - 2].strip() and b < len(tlines) and not tlines[b].strip():
                b += 1  # drop one of the two blank lines around the removed statement
            edits.delete_lines(template, a, b)
            deleted.append((a, b))
        self._drop_empty_guards(out, units, deleted, tlines, template)

        # declarations that became internal
        self._delete_internal_decls(P, out, internal, deleted, template)

        # paths before -> after for every instance of P
        for pi in self.design.instances_of(P.name) or [parent]:
            for i in insts:
                plan.renames[f"{pi.path}.{i.name}"] = f"{pi.path}.{self.instance}.{i.name}"
        plan.focus = f"{parent.path}.{self.instance}"
        fmt = lambda p: f"{p.name}" + (f"<-{p.net}" if p.name != p.net else "")  # noqa: E731
        plan.summary_lines += [
            "  inputs:   " + (", ".join(fmt(p) for p in ports if p.direction == "input") or "-"),
            "  outputs:  " + (", ".join(fmt(p) for p in ports if p.direction == "output") or "-"),
        ]
        if any(p.direction == "inout" for p in ports):
            plan.summary_lines.append("  inouts:   " + ", ".join(fmt(p) for p in ports if p.direction == "inout"))
        if any(p.iface for p in ports):
            plan.summary_lines.append("  ifaces:   " + ", ".join(p.name for p in ports if p.iface))
        plan.summary_lines.append("  internal: " + (", ".join(n for n, _d, _t in internal) or "-"))
        if params:
            plan.summary_lines.append("  params:   " + ", ".join(params))
        if inside_guard:
            plan.summary_lines.append(f"  the wrapper instance stays inside the template if at line "
                                      f"{stmts[0].guards[-1].start}")
        plan.summary_lines.append(f"  new file: {proj.rel(wpath)}")

    @staticmethod
    def _instance_position(ix: ModuleIndex, uses: list[InstUse], units: list[Unit], ports: list[WPort],
                           params: list[str]) -> int:
        """Index of the unit the wrapper instance replaces: the first one,
        unless a net or parameter it connects is declared after the first
        wrapped instance (SystemVerilog needs declarations before use); the
        last unit comes after every declaration its instances use."""
        first = min(uses[k].start for k in units[0].stmts)
        for name in [p.net for p in ports] + list(params):
            d = ix.decls.get(name)
            if d is not None and d.kind != "port" and d.start > first:
                return len(units) - 1
        return 0

    def _drop_empty_guards(self, out: OutputInfo, units: list[Unit], deleted: list[tuple[int, int]],
                           tlines: list[str], template: str) -> None:
        seen: set[int] = set()
        for unit in units:
            drop_empty_guards(self.plan, out, unit.guards_left, deleted, tlines, template, seen)

    def _delete_internal_decls(self, P, out: OutputInfo, internal, deleted: list[tuple[int, int]],
                               template: str) -> None:
        plan = self.plan
        gm = self.sm.gen_module(P.name)
        for n, d, _text in internal:
            if d is None or d.in_fence:
                continue
            if d.n_declarators != 1:
                plan.warn("W_DECL_SHARED", f"declaration of {n} is shared with other names; left in {P.name}")
                continue
            sym = gm.symbols.get(n) if gm is not None else None
            if sym is None or sym.range is None:
                plan.warn("W_DECL_NOT_FOUND", f"declaration of {n} not found in the generated file; left in {P.name}")
                continue
            gd = self.res.gen_design
            g0 = sym.range.line
            g1 = gd.files[sym.range.file].line_col(max(sym.range.start, sym.range.end - 1))[0]
            dst = self.sm.classify_gen_span(out, g0, g1)
            if dst.cls not in (LITERAL, SUBST) or dst.tpl_start is None:
                plan.warn("W_DECL_TEMPLATED", f"declaration of {n} is produced by template code; left in {P.name}")
                continue
            if any(not (dst.tpl_end < a or dst.tpl_start > b) for a, b in deleted):
                continue
            plan.edits.delete_lines(template, dst.tpl_start, dst.tpl_end)
            deleted.append((dst.tpl_start, dst.tpl_end))


def plan_wrap(session, paths: list[str], module: str, instance: str, choices: Optional[dict] = None,
              port_naming: Optional[str] = None) -> Plan:
    return WrapPlanner(session, paths, module, instance, choices, port_naming).run()
