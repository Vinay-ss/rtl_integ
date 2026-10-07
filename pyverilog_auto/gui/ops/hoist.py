"""Hoist an instance out of its wrapper, for every instance of the wrapper.

For instance X inside wrapper module W (instantiated as w1..wk in parents
P1..Pk) the plan rewrites the names X uses:

* a W parameter -> the value bound at wi (or W's default);
* a W port p -> the expression wi connects to p (ports X no longer shares
  with anything in W are removed from W and from every wi);
* a W net also used by X's siblings -> a new W port; each Pi gets a net
  that connects X and wi;
* a W net used only by X -> moves to Pi with X.

X's template text is inserted after each wi statement (renamed when a
parent holds several copies) and deleted from W.  Wrapper headers with
AUTOINPUT/AUTOOUTPUT/AUTOARG regenerate their ports at the next build;
written ANSI port lists are edited.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from ...integ.model import Instance, ModuleDef
from ..build import OutputInfo
from ..connect import ConnectError, InstUse, ModuleIndex
from ..srcmap import CODE, GUARDED, LITERAL, LOOP, SUBST, Statement
from .common import OpError, Plan, Question, capture_values, free_variables, indent_of, literal_safe, unique_name
from .units import drop_empty_guards, header_lines, marker_of, terminator_lines
from .textops import (_conn_list, add_pins, fold_constants, full_select_base, needs_parens, port_list_span,
                      remove_pin, rewrite_param, rewrite_pin, subst_identifiers)

_AUTO_PORT_MARKERS = ("AUTOINPUT", "AUTOOUTPUT", "AUTOINOUT", "AUTOARG")


@dataclass
class _Copy:
    """One instantiation statement of W (module Pi, instance name wi)."""

    parent_mod: ModuleDef
    wi: str
    insts: list[Instance]              # every elaborated instance of that statement
    ix: ModuleIndex
    use: InstUse
    stmt: Statement
    x_name: str = ""
    nets: dict[str, str] = field(default_factory=dict)      # W net -> net name in Pi


def _rename_instance(lines: list[str], old: str, new: str) -> Optional[list[str]]:
    text = "\n".join(lines)
    span = _conn_list(text)
    if span is None:
        return None
    head = text[:span[0]]
    m = None
    for m in re.finditer(r"\b" + re.escape(old) + r"\b", head):
        pass
    if m is None:
        return None
    return (text[:m.start()] + new + text[m.end():]).split("\n")


class HoistPlanner:
    def __init__(self, session, path: str, choices: Optional[dict] = None):
        self.s = session
        self.path = path
        self.choices = dict(choices or {})
        self.proj = session.project
        self.res = session.result
        self.sm = session.srcmap
        self.design = self.res.design
        self.plan = Plan("hoist", f"hoist {path}")
        self._ix: dict[str, ModuleIndex] = {}
        self.dissolve = False

    def index(self, mod: ModuleDef) -> ModuleIndex:
        if mod.name not in self._ix:
            self._ix[mod.name] = ModuleIndex(self.design, mod)
        return self._ix[mod.name]

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

    # ------------------------------------------------------------------

    def _run(self) -> None:
        plan = self.plan
        if self.proj.view_only:
            self._fail("E_VIEW_ONLY", "the project has no templates (view-only)")
        X = self.s.instance(self.path)
        w = X.parent
        if w is None or w.parent is None:
            self._fail("E_HOIST_TOP", f"{self.path} is not inside a wrapper (its parent is the top)")
        W = w.module
        if W is None or w.is_blackbox:
            self._fail("E_HOIST_PARENT", f"{w.path} has no module body")
        ixW = self.index(W)
        uX = ixW.insts.get(X.name)
        if uX is None:
            self._fail("E_HOIST_NOT_FOUND", f"{X.name} not found in {W.name}")
        if uX.ordered:
            self._fail("E_ORDERED", f"{X.path} uses positional connections")
        if uX.shared_statement:
            self._fail("E_SHARED_STMT", f"{X.path} shares its statement with other instances")
        if uX.in_generate:
            self._fail("E_GENERATE", f"{X.path} is inside a generate block")
        stX = self.sm.statement_of(X)
        if stX.cls in (LOOP, CODE):
            key, verb = ("loop", "unroll") if stX.cls == LOOP else ("code", "freeze")
            choice = self.choices.get(key)
            if choice == verb:
                from .unroll import plan_unroll

                unroll = plan_unroll(self.s, X.path)
                choices = {k: v for k, v in self.choices.items() if k != key}
                unroll.then = {"method": "plan_hoist", "params": {"path": X.path, "choices": choices}}
                unroll.note("N_THEN", f"after the {verb}, the hoist is planned again")
                self.plan = unroll
                return
            if choice is None:
                self.plan.question = Question(key, f"{X.path} is {stX.reason or 'produced by template code'}. "
                                                   f"{verb.capitalize()} it into plain text first?", [verb, "cancel"])
                return
            self._fail("E_TPL_" + stX.cls, f"{X.path}: {stX.reason}")
        if stX.cls not in (LITERAL, SUBST, GUARDED):
            self._fail("E_TPL_" + stX.cls, f"{X.path}: {stX.cls.lower()} statement ({stX.reason or 'not movable'})")
        outW: OutputInfo = stX.output

        copies = self._copies(W)
        plan.title = f"hoist {X.name} out of {W.name}" + (f" ({len(copies)} copies)" if len(copies) > 1 else "")

        # -- classify the names X uses ----------------------------------------------
        own = {uX.owner}
        params: list[str] = []
        ports: list[str] = []
        shared: list[str] = []
        local: list[str] = []
        for n in ixW.names_of(uX):
            d = ixW.decls.get(n)
            if d is not None and d.kind in ("param", "localparam"):
                params.append(n)
            elif d is not None and d.kind == "port":
                ports.append(n)
            elif d is not None and d.kind in ("iface_inst", "typedef", "genvar"):
                self._fail("E_HOIST_" + d.kind.upper(), f"{X.name} uses {d.kind} {n} of {W.name}")
            elif ixW.used_outside(n, own):
                shared.append(n)
            else:
                local.append(n)
        removed = [p for p in ports if not ixW.used_outside(p, own)]
        added: dict[str, str] = {}
        for n in shared:
            dirs = {p.direction for p in uX.pins if n in p.names}
            if None in dirs and len(dirs) == 1:
                self._fail("E_HOIST_IFACE", f"{n} connects interface ports; not supported")
            added[n] = "inout" if "inout" in dirs else "input" if "output" in dirs else "output"
            if n in ixW.decls and ixW.decls[n].kind == "port":
                self._fail("E_HOIST_NAME", f"{n} is already a port of {W.name}")

        auto_hdr = W.has_marker(*_AUTO_PORT_MARKERS)
        if not W.ansi and not auto_hdr and (added or removed):
            self._fail("E_HOIST_NONANSI", f"{W.name} has a non-ANSI header without AUTOARG; edit its ports by hand")

        # -- template variables of X ------------------------------------------------
        bodyX = self._x_text(stX, outW, copies)
        if plan.question is not None:
            return

        # -- an empty wrapper can be removed together with its instances -----------------
        dissolve = False
        if self._w_empty(ixW, uX):
            choice = self.choices.get("dissolve")
            if choice is None:
                plan.question = Question("dissolve", f"{W.name} has nothing else inside. Remove the empty "
                                                     "wrapper and its instances as well?", ["remove", "keep"])
                return
            dissolve = choice == "remove"
        edits = plan.edits
        if dissolve:
            others = [m.name for m in self.design.modules_in(outW.integ_path) if m.name != W.name]
            if others:
                self._fail("E_HOIST_DISSOLVE", f"{self.proj.rel(outW.src)} also defines {', '.join(others)}")
            edits.remove_file(outW.src)
            with open(self.proj.path, "r", encoding="utf-8", newline="") as fh:
                manifest = fh.read().replace("\r\n", "\n")
            from ..project import remove_entry_text

            new_manifest = remove_entry_text(manifest, self.proj, outW.src)
            if new_manifest is None:
                self._fail("E_HOIST_DISSOLVE", f"{self.proj.rel(outW.src)} is not listed in the project file")
            edits.set_text(self.proj.path, new_manifest)
            removed, added = [], {}
        self.dissolve = dissolve

        # -- wrapper edits -------------------------------------------------------------
        tW = stX.template
        wlines = edits.lines(tW)
        a, b = stX.tpl_start, stX.tpl_end
        if a >= 2 and not wlines[a - 2].strip() and b < len(wlines) and not wlines[b].strip():
            b += 1
        deleted = [(a, b)]
        hdr_note = None
        if not dissolve:
            edits.delete_lines(tW, a, b)
            if stX.cls == GUARDED:
                drop_empty_guards(plan, outW, stX.guards, deleted, wlines, tW)
            self._delete_w_decls(W, outW, ixW, local + shared, shared, deleted)
            hdr_note = self._edit_w_header(W, outW, ixW, removed, added, deleted, auto_hdr)

        # -- parent edits (one per instantiation statement of W) -------------------------
        per_parent: dict[str, int] = {}
        for c in copies:
            per_parent[c.parent_mod.name] = per_parent.get(c.parent_mod.name, 0) + 1
        for c in copies:
            self._plan_copy(c, X, uX, ixW, bodyX, params, ports, removed, added, shared, local,
                            per_parent[c.parent_mod.name] > 1, auto_hdr)

        plan.focus = copies[0].insts[0].parent.path + "." + copies[0].x_name if copies[0].insts[0].parent else None
        plan.summary_lines.append(f"  copies:   " + ", ".join(f"{c.parent_mod.name}.{c.wi} -> {c.x_name}"
                                                                for c in copies))
        if removed:
            plan.summary_lines.append(f"  {W.name} loses ports: " + ", ".join(removed))
        if added:
            plan.summary_lines.append(f"  {W.name} gains ports: " + ", ".join(f"{n} ({d})" for n, d in added.items()))
        if local:
            plan.summary_lines.append("  moved nets: " + ", ".join(local))
        if hdr_note:
            plan.note("N_AUTO_PORTS", hdr_note)
        if dissolve:
            plan.summary_lines.append(f"  removed:  {W.name} ({self.proj.rel(outW.src)}) and its instances")
        elif self._w_empty(ixW, uX):
            plan.note("N_EMPTY", f"{W.name} has nothing left inside")

    @staticmethod
    def _w_empty(ixW: ModuleIndex, uX: InstUse) -> bool:
        return not any(o.kind in ("inst", "assign", "always", "generate", "other") and o.id != uX.owner
                       for o in ixW.owners)

    # ------------------------------------------------------------------

    def _copies(self, W: ModuleDef) -> list[_Copy]:
        groups: dict[tuple[str, str], list[Instance]] = {}
        for wi in self.design.instances_of(W.name):
            if wi.parent is None or wi.parent.module is None:
                self._fail("E_HOIST_TOP", f"{W.name} is also used as a top-level module")
            groups.setdefault((wi.parent.module_name, wi.name), []).append(wi)
        copies: list[_Copy] = []
        for (pmod, wname), insts in groups.items():
            P = insts[0].parent.module
            ix = self.index(P)
            use = ix.insts.get(wname)
            if use is None:
                self._fail("E_HOIST_NOT_FOUND", f"{wname} not found in {pmod}")
            if use.ordered or use.shared_statement or use.in_generate:
                self._fail("E_HOIST_COPY", f"{pmod}.{wname}: positional, shared or generate instantiation")
            st = self.sm.statement_of(insts[0])
            if st.cls not in (LITERAL, SUBST):
                self._fail("E_TPL_" + st.cls, f"{insts[0].path}: {st.cls.lower()} statement ({st.reason})")
            copies.append(_Copy(P, wname, insts, ix, use, st))
        if not copies:
            self._fail("E_HOIST_NOT_FOUND", f"no instance of {W.name}")
        return copies

    def _x_text(self, stX: Statement, outW: OutputInfo, copies: list[_Copy]) -> list[str]:
        """X's statement text: template text when its variables are available
        with the same values in every parent template, else frozen (asked)."""
        plan = self.plan
        lines = plan.edits.line_range_text(stX.template, stX.tpl_start, stX.tpl_end)
        if outW.kind != "template" or outW.linemap is None:
            return lines
        guards = stX.guards if stX.cls == GUARDED else []
        if guards:
            lm = outW.linemap
            lines = header_lines(lm, outW, guards) + lines + terminator_lines(lm, outW, len(guards))
        variables = free_variables(outW.entry.lang, outW.linemap, stX.tpl_start, stX.tpl_end, guards)
        if not variables:
            return lines
        mode = self.choices.get("subst")
        if mode != "freeze":
            problem = None
            try:
                wvals = capture_values(self.proj, outW, [stX.tpl_start], variables).get(stX.tpl_start, {})
                for c in copies:
                    out = c.stmt.output
                    if out.kind != "template" or out.entry.lang != outW.entry.lang:
                        problem = f"{c.parent_mod.name} is not a {outW.entry.lang} template"
                        break
                    pvals = capture_values(self.proj, out, [c.stmt.tpl_start], variables).get(c.stmt.tpl_start, {})
                    for v in variables:
                        if v not in wvals or not literal_safe(outW.entry.lang, wvals[v]) or pvals.get(v) != wvals[v]:
                            problem = f"{v} is not set to the same value in {c.parent_mod.name}"
                            break
                    if problem:
                        break
            except OpError as exc:
                problem = exc.message
            if problem is None:
                plan.note("N_CARRY", "template variables kept (same values in the parent): " + ", ".join(variables))
                return lines
            if mode is None:
                plan.question = Question("subst", f"{problem}. Freeze the instance text (substitute the generated "
                                                  "values)?", ["freeze", "cancel"])
                return lines
            raise OpError("E_CARRY", problem)
        with open(outW.gen_path, "r", encoding="utf-8") as fh:
            gen_lines = fh.read().replace("\r\n", "\n").split("\n")
        plan.note("N_FROZEN", "template variables substituted (frozen): " + ", ".join(variables))
        return gen_lines[stX.gen.line - 1:stX.gen.end_line]

    # -- wrapper ------------------------------------------------------------------------

    def _decl_span(self, mod: ModuleDef, out: OutputInfo, name: str) -> Optional[tuple[int, int]]:
        gm = self.sm.gen_module(mod.name)
        sym = gm.symbols.get(name) if gm is not None else None
        if sym is None or sym.range is None:
            return None
        gd = self.res.gen_design
        g0 = sym.range.line
        g1 = gd.files[sym.range.file].line_col(max(sym.range.start, sym.range.end - 1))[0]
        st = self.sm.classify_gen_span(out, g0, g1)
        if st.cls not in (LITERAL, SUBST) or st.tpl_start is None:
            return None
        return st.tpl_start, st.tpl_end

    def _delete_w_decls(self, W: ModuleDef, outW: OutputInfo, ixW: ModuleIndex, names: list[str],
                        must: list[str], deleted: list[tuple[int, int]]) -> None:
        for n in names:
            d = ixW.decls.get(n)
            if d is None or d.in_fence:
                continue
            span = self._decl_span(W, outW, n) if d.n_declarators == 1 else None
            if span is None or any(not (span[1] < a or span[0] > b) for a, b in deleted):
                if n in must:
                    self._fail("E_HOIST_DECL", f"cannot remove the declaration of {n} from {W.name}'s template "
                                               "(it becomes a port)")
                self.plan.warn("W_DECL_LEFT", f"declaration of {n} left in {W.name}")
                continue
            self.plan.edits.delete_lines(self.sm.template_of_module(W.name).src, span[0], span[1])
            deleted.append(span)

    def _edit_w_header(self, W: ModuleDef, outW: OutputInfo, ixW: ModuleIndex, removed: list[str],
                       added: dict[str, str], deleted: list[tuple[int, int]], auto_hdr: bool) -> Optional[str]:
        if not removed and not added:
            return None
        gm = self.sm.gen_module(W.name)
        gd = self.res.gen_design
        if gm is None or gd is None:
            self._fail("E_HOIST_HEADER", f"{W.name}: header not found in the generated file")
        g0 = gm.keyword_range.line
        g1 = gd.files[gm.file].line_col(max(gm.header_range.start, gm.header_range.end - 1))[0]
        st = self.sm.classify_gen_span(outW, g0, g1)
        written_removed = [p for p in removed if not (ixW.decls[p].in_fence)]
        if auto_hdr and not written_removed:
            return f"{W.name}'s AUTO ports are regenerated at the next build"
        if st.cls not in (LITERAL, SUBST) or st.tpl_start is None:
            self._fail("E_HOIST_HEADER", f"{W.name}'s header is produced by template code ({st.reason})")
        if any(not (st.tpl_end < a or st.tpl_start > b) for a, b in deleted):
            self._fail("E_HOIST_HEADER", f"{W.name}'s header overlaps another edit")
        tpath = outW.src
        lines = self.plan.edits.line_range_text(tpath, st.tpl_start, st.tpl_end)
        text = "\n".join(lines)
        span = port_list_span(text)
        if span is None:
            self._fail("E_HOIST_HEADER", f"{W.name}: port list not found in the template")
        op, cl = span
        entries = _split_entries(text[op + 1:cl])
        keep = [e for e in entries if _entry_name(e) not in written_removed]
        if not auto_hdr:
            for n, direction in added.items():
                d = ixW.decls.get(n)
                typ = d.full_type if d is not None and d.full_type else "logic"
                if direction == "inout" and typ.startswith("logic"):
                    typ = "wire" + typ[len("logic"):]
                keep.append(_aligned_entry(entries, direction, typ, n + (d.unpacked if d else "")))
        sep = ",\n" + _entry_indent(text, op) if "\n" in text[op:cl] else ", "
        new_inner = sep.join(e.strip() for e in keep if e.strip())
        tail = "\n" + _entry_indent(text, op) if keep and text[op + 1:cl].rstrip(" ").endswith("\n") else ""
        new_text = text[:op + 1] + new_inner + tail + text[cl:]
        self.plan.edits.replace_lines(tpath, st.tpl_start, st.tpl_end, new_text.split("\n"))
        deleted.append((st.tpl_start, st.tpl_end))
        return (f"{W.name}'s AUTO ports are regenerated at the next build" if auto_hdr else None)

    # -- parents ----------------------------------------------------------------------------

    def _w_param_value(self, c: _Copy, ixW: ModuleIndex, name: str, depth: int = 0) -> str:
        if name in c.use.params:
            return c.use.params[name]
        d = ixW.decls.get(name)
        value = (d.value if d is not None else None) or "0"
        if depth > 8:
            return value
        deps = {n: self._w_param_value(c, ixW, n, depth + 1) for n in re.findall(r"[A-Za-z_]\w*", value)
                if n in ixW.decls and ixW.decls[n].kind in ("param", "localparam")}
        return subst_identifiers(value, {k: f"({v})" if needs_parens(v) else v for k, v in deps.items()})

    def _plan_copy(self, c: _Copy, X: Instance, uX: InstUse, ixW: ModuleIndex, bodyX: list[str],
                   params: list[str], ports: list[str], removed: list[str], added: dict[str, str],
                   shared: list[str], local: list[str], several: bool, auto_hdr: bool) -> None:
        plan = self.plan
        ixP = c.ix
        taken = set(ixP.decls) | set(ixP.insts) | set(c.parent_mod.symbols)
        # instance name in the parent
        c.x_name = X.name if (X.name not in taken and not several) else unique_name(f"{c.wi}_{X.name}", taken)
        taken.add(c.x_name)
        mapping: dict[str, str] = {}
        for n in params:
            v = self._w_param_value(c, ixW, n)
            v = fold_constants(v)
            mapping[n] = f"({v})" if needs_parens(v) else v
        decl_lines: list[str] = []
        for n in shared + local:
            if n in shared and auto_hdr and c.use.uses_autoinst:
                if n in taken:
                    self._fail("E_HOIST_NAME_CLASH", f"{c.parent_mod.name} already has a {n}; AUTOINST would "
                                                     f"connect {c.wi}.{n} to it")
                net = n
            else:
                net = n if (n not in taken and not several) else unique_name(f"{c.wi}_{n}", taken)
            taken.add(net)
            c.nets[n] = net
            mapping[n] = net
            d = ixW.decls.get(n)
            if d is not None:
                dims = fold_constants(subst_identifiers(d.dims, mapping))
                unpacked = fold_constants(subst_identifiers(d.unpacked, mapping))
                typ = d.type_text or "logic"
            else:
                typ, dims, unpacked = "logic", self._x_port_dims(X, uX, n), ""
            decl_lines.append(f"{typ} {dims} {net}{unpacked};".replace("  ", " ").replace(" ;", ";"))
        port_expr: dict[str, str] = {}
        for p in ports:
            pin = c.use.pin(p)
            e = pin.expr if pin is not None else ""
            dw = ixW.decls[p].dims
            base = full_select_base(e, dw) if e else None
            if base is None and ixW.decls[p].iface_type and re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*",
                                                                          e.strip()):
                base = e.strip().split(".")[0]      # interface bound with a modport select
            port_expr[p] = base if base is not None else e

        # X's pins and parameters in the parent's names
        body = list(bodyX)
        for pin in uX.pins:
            if not pin.names:
                continue
            m = dict(mapping)
            complex_used = False
            for n in pin.names:
                if n in port_expr:
                    e = port_expr[n]
                    if pin.expr.strip() == n:
                        m[n] = e
                    elif e == "":
                        self._fail("E_HOIST_UNCONNECTED", f"{X.name}.{pin.port} selects {n}, which {c.wi} leaves "
                                                          "unconnected")
                    elif re.fullmatch(r"[A-Za-z_][\w$]*", e):
                        m[n] = e
                    else:
                        complex_used = True
                        m[n] = f"({e})" if needs_parens(e) else e
            if complex_used and pin.expr.strip() not in m:
                self._fail("E_HOIST_COMPLEX_BINDING", f"{X.name}.{pin.port} = {pin.expr}: {c.wi} binds it to an "
                                                      "expression; connect it through a net first")
            new_expr = m[pin.expr.strip()] if pin.expr.strip() in m else fold_constants(subst_identifiers(pin.expr, m))
            if new_expr == pin.expr:
                continue
            new = rewrite_pin(body, pin.port, new_expr)
            if new is None:
                if pin.port == new_expr and uX.uses_autoinst:
                    continue
                new = add_pins(body, [(pin.port, new_expr)])
            if new is None:
                self._fail("E_HOIST_PIN", f"cannot rewrite {X.name}.{pin.port}")
            body = new
        for pname, names in uX.param_names.items():
            if not any(n in mapping for n in names):
                continue
            new = rewrite_param(body, pname, subst_identifiers(uX.params[pname], mapping))
            if new is None:
                self._fail("E_HOIST_PARAM", f"cannot rewrite parameter {pname} of {X.name}")
            body = new
        if c.x_name != X.name:
            new = _rename_instance(body, X.name, c.x_name)
            if new is None:
                self._fail("E_HOIST_NAME", f"cannot rename {X.name} in its statement text")
            body = new

        # the wrapper instance: pins for added ports, removed ports disconnected
        wlines = plan.edits.line_range_text(c.stmt.template, c.stmt.tpl_start, c.stmt.tpl_end)
        new_w = list(wlines)
        # AUTOINST connects a new port to the parent net of the same name by itself
        extra = [(n, c.nets[n]) for n in added if not (c.use.uses_autoinst and c.nets[n] == n)]
        if extra:
            r = add_pins(new_w, extra)
            if r is None:
                self._fail("E_HOIST_PIN", f"cannot add pins to {c.wi}")
            new_w = r
        for p in removed:
            pin = c.use.pin(p)
            if pin is None or pin.in_fence or pin.implicit:
                continue
            r = remove_pin(new_w, p)
            if r is None:
                self._fail("E_HOIST_PIN", f"cannot remove pin {p} from {c.wi}")
            new_w = r

        tlines = plan.edits.lines(c.stmt.template)
        indent = indent_of(tlines[c.stmt.tpl_start - 1])
        decls = [indent + d for d in decl_lines]
        # re-base the text lines on the parent's indentation; template code lines
        # ("// py ...", "// pl ...") stay at column 0 where prepro expects them
        marker = marker_of(self.sm.template_of_module(X.parent.module_name) or c.stmt.output)
        text_lines = [ln for ln in body if ln.strip() and not ln.lstrip().startswith(marker)]
        xind = indent_of(text_lines[0]) if text_lines else indent
        body = [ln if ln.lstrip().startswith(marker) else
                ((indent + ln[len(xind):]) if ln.startswith(xind) else ln) for ln in body]
        if self.dissolve:
            insert = decls + ([""] if decls else []) + body
        else:
            insert = decls + ([""] if decls else []) + new_w + [""] + body
        plan.edits.replace_lines(c.stmt.template, c.stmt.tpl_start, c.stmt.tpl_end, insert)
        for wi in c.insts:
            plan.renames[f"{wi.path}.{X.name}"] = f"{wi.parent.path}.{c.x_name}"

    def _x_port_dims(self, X: Instance, uX: InstUse, n: str) -> str:
        for pin in uX.pins:
            if pin.expr.strip() == n:
                try:
                    return self.design.port_at(X, pin.port).packed_dims_numeric
                except Exception:
                    return ""
        return ""


def _split_entries(inner: str) -> list[str]:
    out, depth, cur = [], 0, []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if inner.startswith("/*", i):
            end = inner.find("*/", i + 2)
            end = len(inner) if end < 0 else end + 2
            cur.append(inner[i:end])
            i = end
            continue
        if inner.startswith("//", i):
            nl = inner.find("\n", i)
            nl = len(inner) if nl < 0 else nl
            cur.append(inner[i:nl])
            i = nl
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


_DIR_RE = re.compile(r"^(input|output|inout)(\s+)(.*?)(\s*)([A-Za-z_][\w$]*(?:\s*\[[^\]]*\])*)$", re.S)


def _aligned_entry(entries: list[str], direction: str, typ: str, name: str) -> str:
    """A new ``direction type name`` entry in the columns of an existing one."""
    for e in entries:
        m = _DIR_RE.match(e.strip())
        if m and m.group(3):
            type_col = len(m.group(1)) + len(m.group(2))
            name_col = type_col + len(m.group(3)) + len(m.group(4))
            head = direction.ljust(type_col - 1) + " " + typ
            return head.ljust(name_col - 1) + " " + name
    return f"{direction} {typ} {name}"


def _entry_name(entry: str) -> Optional[str]:
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", " ", entry, flags=re.S)
    clean = re.sub(r"\[[^\]]*\]", " ", clean).strip()
    names = re.findall(r"[A-Za-z_][\w$]*", clean)
    return names[-1] if names else None


def _entry_indent(text: str, op: int) -> str:
    nl = text.find("\n", op)
    if nl < 0:
        return "   "
    nxt = text[nl + 1:]
    return nxt[: len(nxt) - len(nxt.lstrip(" \t"))] or "   "


def plan_hoist(session, path: str, choices: Optional[dict] = None) -> Plan:
    return HoistPlanner(session, path, choices).run()
