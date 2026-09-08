"""RoutePlanner — validate route specs against a Design and plan text edits.

Policy ("AUTO-native where markers exist, explicit edits otherwise"):

* a pin on an ``/*AUTOINST*/`` instantiation is left to AUTOINST when the
  child port name equals the net name at the parent (no rename), the port is
  visible to AUTOINST, and the width is not parameter dependent;
* an intermediate module's port is left to ``/*AUTOINPUT*/`` /
  ``/*AUTOOUTPUT*/`` / ``/*AUTOINOUT*/`` when its pin is AUTO-native and the
  marker exists; interface ports are always written explicitly;
* the net at the lowest common ancestor is left to ``/*AUTOWIRE*/`` /
  ``/*AUTOLOGIC*/`` when every pin there is AUTO-native; interface instances
  are always written explicitly;
* within one module every pin of a net is either AUTO-native or explicit
  (explicit pins are invisible to AUTOOUTPUT and would leave spurious ports).

Everything is validated before anything is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .edits import (
    EditSet, TextEdit, column_of, fence_spans_of, insert_list_entries, insert_lines_after,
    insert_lines_before, line_end, line_indent, line_start,
)
from .model import ElaboratedPort, Instance, ModuleDef, ModuleRef, PortInfo, SrcRange
from .route import Diagnostic, Endpoint, RouteError, RoutePair, RouteSpec, expand_backrefs, is_identifier, pair_routes

if TYPE_CHECKING:
    from .design import Design

_PORT_LIST_MARKERS = ("AUTOINPUT", "AUTOOUTPUT", "AUTOINOUT", "AUTOINOUTMODULE", "AUTOINOUTCOMP",
                      "AUTOINOUTIN", "AUTOINOUTPARAM", "AUTOINOUTMODPORT")


# ----------------------------------------------------------------------
# Plan data
# ----------------------------------------------------------------------

@dataclass
class PairPlan:
    pair: RoutePair
    src_inst: Instance
    dst_inst: Instance
    lca: Instance
    mode: str                      # lca | dst_is_ancestor | src_is_ancestor
    src_chain: list[Instance]      # lca .. src (inclusive)
    dst_chain: list[Instance]      # lca .. dst (inclusive)
    src_ep: ElaboratedPort
    dst_pinfo: Optional[PortInfo]
    dst_direction: Optional[str]   # direction the dst port has / will get
    dst_modport: Optional[str]


@dataclass
class RouteNet:
    spec: RouteSpec
    identity: Endpoint
    kind: str                      # signal | struct | iface
    src_ep: ElaboratedPort
    pairs: list[PairPlan]
    base_name: str = ""
    groups: tuple = ()


@dataclass
class Step:
    """One hop: *child* instance inside *parent*; the child's port
    ``child_port`` must reach ``parent_name`` in the parent module."""

    net: RouteNet
    parent: Instance
    child: Instance
    child_port: str
    parent_name: str
    side: str                      # src | dst
    direction: Optional[str]       # direction of the port created on the parent (intermediate)
    parent_is_host: bool           # parent hosts the net (lca) or the boundary port (ancestor modes)
    pin_auto: bool = False
    port_auto: bool = False


@dataclass
class AutoNativeItem:
    module: str
    net: str
    what: str                      # port | pin | net
    via: str


@dataclass
class RouteResult:
    spec: RouteSpec
    pairs: list[RoutePair] = field(default_factory=list)
    nets: list[RouteNet] = field(default_factory=list)
    edits: list[TextEdit] = field(default_factory=list)
    auto_native: list[AutoNativeItem] = field(default_factory=list)
    created_ports: list[tuple[str, str]] = field(default_factory=list)
    created_nets: list[tuple[str, str]] = field(default_factory=list)
    created_instances: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    infos: list[Diagnostic] = field(default_factory=list)


@dataclass
class RoutePlan:
    results: list[RouteResult]
    edits: EditSet
    warnings: list[Diagnostic]
    errors: list[Diagnostic]

    @property
    def all_edits(self) -> list[TextEdit]:
        return list(self.edits.edits)


@dataclass
class Report:
    results: list[RouteResult] = field(default_factory=list)
    edits: list[TextEdit] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    diff: Optional[str] = None
    dry_run: bool = False
    expanded: bool = False
    residual: list[TextEdit] = field(default_factory=list)
    expand_report: object = None

    def summary(self) -> str:
        n_routes = sum(len(r.pairs) for r in self.results)
        mode = " (dry run)" if self.dry_run else ""
        s = (f"{len(self.results)} spec(s), {n_routes} route(s), {len(self.edits)} edit(s) in "
             f"{len(self.files_changed)} file(s), {len(self.warnings)} warning(s){mode}")
        if self.expanded:
            s += f"; expanded; residual edits: {len(self.residual)}"
        return s


# ----------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------

class RoutePlanner:
    def __init__(self, design: "Design", specs, *, strict: bool = False) -> None:
        self.design = design
        self.specs = [RouteSpec.coerce(s, index=i + 1) for i, s in enumerate(specs)]
        self.strict = strict
        self.errors: list[Diagnostic] = []
        self.warnings: list[Diagnostic] = []
        self._created_ports: dict[str, dict[str, PortInfo]] = {}     # module -> name -> planned port
        self._created_nets: dict[str, set[str]] = {}                  # module -> names
        self._planned_pins: dict[tuple[str, str], tuple[str, str]] = {}   # (module, inst, port) -> (net, route)
        self._edit_keys: dict[tuple, TextEdit] = {}
        self._fences: dict[str, list[tuple[int, int]]] = {}
        self._visits: dict[str, dict[int, set[str]]] = {}
        # (file key, list start, anchor start) -> batch of list entries
        self._lists: dict[tuple, dict] = {}

    # ------------------------------------------------------------------

    def plan(self) -> RoutePlan:
        design = self.design
        if design.backend != "slang":
            raise RouteError([Diagnostic("E_BACKEND", "error", "routing needs the pyslang backend (pip install pyverilog-auto[integ])")])
        paths = [i.path for i in design.all_instances()]
        for key, sf in design.files.items():
            spans: list[tuple[int, int]] = []
            for mod in design.modules.values():
                if mod.file == key:
                    spans.extend(fence_spans_of(mod))
            self._fences[key] = sorted(spans)

        results: list[RouteResult] = []
        nets_all: list[RouteNet] = []
        for spec in self.specs:
            res = RouteResult(spec=spec)
            results.append(res)
            try:
                pairs = pair_routes(spec, paths)
            except RouteError as exc:
                self.errors.extend(exc.diagnostics)
                continue
            res.pairs = pairs
            plans = [pp for pp in (self._plan_pair(p, res) for p in pairs) if pp is not None]
            nets = self._group_identities(spec, plans, res)
            res.nets = nets
            nets_all.extend(nets)
        if self.errors:
            raise RouteError(self.errors)

        self._assign_names(nets_all)
        if self.errors:
            raise RouteError(self.errors)

        self._collect_visits(nets_all)
        steps: list[Step] = []
        for net in nets_all:
            steps.extend(self._steps_for(net))
        self._decide_strategy(steps)
        edits = EditSet(design)
        for net in nets_all:
            res = next(r for r in results if r.spec is net.spec)
            self._emit(net, [s for s in steps if s.net is net], res, edits)
        self._check_unrouted_and_implicit(nets_all, steps, results, edits)
        self._flush_lists(edits)
        if self.errors:
            raise RouteError(self.errors)
        try:
            edits.check(self._fences)
        except Exception as exc:
            raise RouteError([Diagnostic("E_EDIT", "error", str(exc))]) from exc
        if self.strict:
            hard = [w for w in self.warnings if w.code in ("W_UNROUTED",)]
            if hard:
                raise RouteError([Diagnostic("E_UNROUTED", "error", w.message, w.file, w.line, route=w.route) for w in hard])
        return RoutePlan(results=results, edits=edits, warnings=self.warnings, errors=[])

    # ------------------------------------------------------------------
    # Phase A: per pair
    # ------------------------------------------------------------------

    def _err(self, code: str, msg: str, *, route: Optional[str] = None, inst: Optional[Instance] = None,
             rng: Optional[SrcRange] = None) -> None:
        file = line = None
        if rng is not None:
            file, line = self.design.files[rng.file].path, rng.line
        elif inst is not None and inst.file:
            file = self.design.files[inst.file].path
        self.errors.append(Diagnostic(code, "error", msg, file, line, route=route))

    def _warn(self, code: str, msg: str, *, route: Optional[str] = None, inst: Optional[Instance] = None) -> None:
        file = self.design.files[inst.file].path if inst is not None and inst.file else None
        self.warnings.append(Diagnostic(code, "warning", msg, file, None, route=route))

    def _plan_pair(self, pair: RoutePair, res: RouteResult) -> Optional[PairPlan]:
        design = self.design
        label = pair.label
        src_inst = design.instance(pair.src.path)
        dst_inst = design.instance(pair.dst.path)
        assert src_inst is not None and dst_inst is not None
        if src_inst.is_blackbox:
            self._err("E_BLACKBOX", f"src instance {src_inst.path} is a black box ({src_inst.module_name})", route=label)
            return None
        if dst_inst.is_blackbox:
            self._err("E_BLACKBOX", f"dst instance {dst_inst.path} is a black box ({dst_inst.module_name})", route=label)
            return None
        try:
            src_ep = design.port_at(src_inst, pair.src.port)
        except LookupError:
            names = [p.name for p in src_inst.module.ports] if src_inst.module else []
            close = [n for n in names if pair.src.port.lower() in n.lower() or n.lower() in pair.src.port.lower()]
            hint = f"; closest: {', '.join(close[:5])}" if close else f"; ports: {', '.join(names[:12])}"
            self._err("E_SRC_PORT_MISSING", f"{src_inst.path} ({src_inst.module_name}) has no port {pair.src.port!r}{hint}", route=label, inst=src_inst)
            return None
        if src_ep.direction == "ref" or (src_ep.kind == "iface" and not src_ep.iface_type):
            self._err("E_UNSUPPORTED_PORT", f"port {pair.src.port} of {src_inst.module_name} is a ref/generic interface port", route=label, inst=src_inst)
            return None
        lca = design.lca(src_inst, dst_inst)
        if lca is None:
            self._err("E_DIFFERENT_TOPS", f"{src_inst.path} and {dst_inst.path} are under different top modules", route=label)
            return None
        if lca is src_inst:
            mode = "src_is_ancestor"
        elif lca is dst_inst:
            mode = "dst_is_ancestor"
        else:
            mode = "lca"
        src_chain = design.chain(lca, src_inst)
        dst_chain = design.chain(lca, dst_inst)
        for inst in src_chain + dst_chain:
            if inst.is_blackbox:
                self._err("E_BLACKBOX", f"{inst.path} on the route is a black box ({inst.module_name})", route=label)
                return None
            if inst.module is not None and inst.module.kind == "interface" and inst not in (src_inst, dst_inst):
                self._err("E_ROUTE_THROUGH_INTERFACE", f"{inst.path} is an interface instance; cannot route through it", route=label)
                return None
        # dst port: exists or to be created
        dst_mod = dst_inst.module
        assert dst_mod is not None
        dst_pinfo = dst_mod.port(pair.dst.port)
        if dst_pinfo is not None and dst_pinfo.in_auto_fence:
            dst_pinfo = None   # transient (regenerated) declaration
        if mode == "lca":
            expected_dir = {"input": "output", "output": "input", "inout": "inout"}.get(src_ep.direction or "", None)
        else:
            expected_dir = src_ep.direction
        dst_modport: Optional[str] = pair.spec.dst_modport
        if dst_pinfo is not None:
            if src_ep.kind == "iface":
                if not dst_pinfo.is_interface or dst_pinfo.iface_type != src_ep.iface_type:
                    self._err("E_IFACE_TYPE_MISMATCH", f"{dst_inst.path}.{pair.dst.port} is {dst_pinfo.iface_type or dst_pinfo.type_text}, expected interface {src_ep.iface_type}", route=label, inst=dst_inst)
                    return None
                dst_modport = dst_pinfo.modport
            else:
                if dst_pinfo.is_interface:
                    self._err("E_TYPE_MISMATCH", f"{dst_inst.path}.{pair.dst.port} is an interface port, expected a {src_ep.kind}", route=label, inst=dst_inst)
                    return None
                if dst_pinfo.direction != expected_dir:
                    self._err("E_DIRECTION_MISMATCH", f"{dst_inst.path}.{pair.dst.port} is {dst_pinfo.direction}, expected {expected_dir} for src {pair.src} ({src_ep.direction})", route=label, inst=dst_inst)
                    return None
                if not self._types_compatible(src_ep, dst_pinfo):
                    d = Diagnostic("E_TYPE_MISMATCH" if pair.spec.check_types else "W_TYPE_MISMATCH",
                                   "error" if pair.spec.check_types else "warning",
                                   f"{dst_inst.path}.{pair.dst.port} type '{dst_pinfo.type_text or ''} {dst_pinfo.packed_dims}' differs from src '{src_ep.symbolic_type_text or ''} {src_ep.symbolic_packed_dims}'",
                                   self.design.files[dst_inst.file].path if dst_inst.file else None, None, route=label)
                    if pair.spec.check_types:
                        self.errors.append(d)
                        return None
                    self.warnings.append(d)
            dst_direction = dst_pinfo.direction
        else:
            dst_direction = None if src_ep.kind == "iface" else expected_dir
        return PairPlan(pair, src_inst, dst_inst, lca, mode, src_chain, dst_chain, src_ep, dst_pinfo, dst_direction, dst_modport)

    @staticmethod
    def _norm_dims(s: Optional[str]) -> str:
        return re.sub(r"\s+", "", s or "")

    def _types_compatible(self, ep: ElaboratedPort, p: PortInfo) -> bool:
        if ep.kind == "struct":
            want = ep.struct_qualified_name or ""
            return (p.type_text or "").split()[-1] == want or (p.type_text or "").endswith(want.split("::")[-1])
        # A plain signal cannot land on a user-typed (struct/typedef) port.
        from ..parser.decl_parser import _TYPE_KEYWORDS

        if any(tok not in _TYPE_KEYWORDS for tok in (p.type_text or "").split()):
            return False
        a = self._norm_dims(ep.symbolic_packed_dims)
        b = self._norm_dims(p.packed_dims)
        if a == b and self._norm_dims(ep.unpacked_dims) == self._norm_dims(p.unpacked_dims):
            return True
        # symbolic texts differ: compare evaluated width when possible
        if ep.bit_width is not None and not b and ep.bit_width == 1:
            return True
        return False

    # ------------------------------------------------------------------
    # Phase B: identities
    # ------------------------------------------------------------------

    def _group_identities(self, spec: RouteSpec, plans: list[PairPlan], res: RouteResult) -> list[RouteNet]:
        label = spec.label()
        nets: dict[Endpoint, RouteNet] = {}
        for pp in plans:
            ep = pp.src_ep
            if ep.kind == "iface" or ep.direction in ("output", "inout"):
                ident = pp.pair.src
            else:
                ident = pp.pair.dst
            net = nets.get(ident)
            if net is None:
                net = RouteNet(spec=spec, identity=ident, kind=ep.kind, src_ep=ep, pairs=[])
                nets[ident] = net
            net.pairs.append(pp)
        # multi-driver checks
        by_dst: dict[Endpoint, set[Endpoint]] = {}
        by_src: dict[Endpoint, set[Endpoint]] = {}
        for net in nets.values():
            for pp in net.pairs:
                by_dst.setdefault(pp.pair.dst, set()).add(net.identity)
                by_src.setdefault(pp.pair.src, set()).add(net.identity)
        for ep_, idents in by_dst.items():
            if len(idents) > 1:
                kind = next(iter(nets.values())).kind
                code = "E_IFACE_MANY_TO_ONE" if kind == "iface" else "E_MULTI_DRIVER"
                self._err(code, f"{ep_} would be driven by {len(idents)} sources: {', '.join(str(i) for i in sorted(idents, key=str))}", route=label)
        for ep_, idents in by_src.items():
            if len(idents) > 1:
                self._err("E_MULTI_DRIVER", f"input {ep_} would be driven by {len(idents)} destinations: {', '.join(str(i) for i in sorted(idents, key=str))}", route=label)
        for net in nets.values():
            if net.kind == "iface" and len({pp.pair.dst for pp in net.pairs}) > 1:
                self._warn("W_IFACE_FANOUT", f"interface {net.identity} fans out to {len(net.pairs)} destinations", route=label)
            if net.kind == "struct" and net.src_ep.struct_is_local:
                self._err("E_LOCAL_TYPEDEF", f"{net.identity}: struct type {net.src_ep.struct_qualified_name} is a module-local typedef; move it to a package", route=label)
            if net.src_ep.param_dependent:
                self._warn("W_PARAM_WIDTH", f"{net.identity}: width {net.src_ep.symbolic_packed_dims} depends on a parameter; ports are written explicitly with the elaborated width {net.src_ep.packed_dims_numeric}", route=label)
        return list(nets.values())

    # ------------------------------------------------------------------
    # Phase C: names
    # ------------------------------------------------------------------

    def _assign_names(self, nets: list[RouteNet]) -> None:
        by_spec: dict[int, list[RouteNet]] = {}
        for net in nets:
            by_spec.setdefault(id(net.spec), []).append(net)
        for group in by_spec.values():
            spec = group[0].spec
            label = spec.label()
            for net in group:
                pp = net.pairs[0]
                m = pp.pair.src_match
                net.groups = tuple(g for g in m.groups() if g is not None)
                src_port = pp.pair.src.port
                if spec.net:
                    base = expand_backrefs(spec.net, m, regex_escape=False, route=label)
                elif len(group) == 1:
                    base = src_port
                else:
                    if not net.groups:
                        self._err("E_NET_NAME_NEEDED", f"{len(group)} distinct nets need distinct names: add a capture group to src or set 'net'", route=label)
                        base = src_port
                    else:
                        base = src_port + "_" + "_".join(re.sub(r"\W+", "_", g) for g in net.groups)
                if not is_identifier(base):
                    self._err("E_BAD_NET_NAME", f"net name {base!r} is not an identifier", route=label)
                net.base_name = base
            seen: dict[str, RouteNet] = {}
            for net in group:
                other = seen.get(net.base_name)
                if other is not None and other is not net:
                    self._err("E_NET_NAME_COLLISION", f"nets {other.identity} and {net.identity} both map to {net.base_name!r}; add \\1 to 'net'", route=label)
                seen[net.base_name] = net

    def _collect_visits(self, nets: list[RouteNet]) -> None:
        """Which modules are visited by which nets through which instances
        (intermediates and hosts); decides where port names must be shared."""
        self._visits = {}
        for net in nets:
            for pp in net.pairs:
                for chain in (pp.src_chain, pp.dst_chain):
                    for inst in chain[:-1]:          # everything but the endpoint
                        self._visits.setdefault(inst.module_name, {}).setdefault(id(net), set()).add(inst.path)

    def _steps_for(self, net: RouteNet) -> list[Step]:
        """Bottom-up names along every chain of *net* (module-consistent)."""
        steps: list[Step] = []
        for pp in net.pairs:
            src_dir = net.src_ep.direction
            for side, chain, ep_port, direction in (
                ("src", pp.src_chain, pp.pair.src.port, src_dir),
                ("dst", pp.dst_chain, pp.pair.dst.port, pp.dst_direction),
            ):
                if len(chain) < 2:
                    continue          # this endpoint is the host itself (ancestor mode)
                child_name = ep_port
                # walk from the endpoint up to the LCA
                for k in range(len(chain) - 1, 0, -1):
                    child = chain[k]
                    parent = chain[k - 1]
                    is_host = (k - 1 == 0)
                    if is_host:
                        if pp.mode == "lca":
                            parent_name = net.base_name if not self._module_shared(parent.module_name) else child_name
                        else:
                            parent_name = pp.pair.dst.port if pp.mode == "dst_is_ancestor" else pp.pair.src.port
                    else:
                        parent_name = child_name if self._module_shared(parent.module_name) else net.base_name
                    steps.append(Step(net=net, parent=parent, child=child, child_port=child_name, parent_name=parent_name,
                                      side=side, direction=direction, parent_is_host=is_host))
                    child_name = parent_name
        return steps

    def _module_shared(self, module_name: str) -> bool:
        """True when several nets pass through *module_name* via distinct
        instances: the module then needs ONE port name for all of them."""
        d = self._visits.get(module_name, {})
        if len(d) < 2:
            return False
        insts = set()
        for s in d.values():
            insts |= s
        return len(insts) >= 2

    # ------------------------------------------------------------------
    # Phase D: strategy
    # ------------------------------------------------------------------

    def _struct_ok(self, mod: ModuleDef, net: RouteNet) -> bool:
        if net.kind != "struct":
            return True
        cfg = self.design.effective_config(mod.file)
        if not cfg.typedef_regexp:
            return False
        qn = net.src_ep.struct_qualified_name or ""
        try:
            return bool(re.search(cfg.typedef_regexp, qn)) or bool(re.search(cfg.typedef_regexp, qn.split("::")[-1]))
        except re.error:
            return False

    def _decide_strategy(self, steps: list[Step]) -> None:
        for s in steps:
            net = s.net
            child_mod = s.child.module
            parent_mod = s.parent.module
            ref = s.child.ref
            pin_auto = bool(ref is not None and ref.uses_autoinst and s.child_port == s.parent_name)
            if net.kind == "iface" and child_mod is not None and not child_mod.ansi:
                pin_auto = False
            if net.src_ep.param_dependent:
                pin_auto = False
            if net.kind == "struct" and parent_mod is not None and not self._struct_ok(parent_mod, net):
                pin_auto = False
            s.pin_auto = pin_auto
            if s.parent_is_host or parent_mod is None:
                s.port_auto = False
                continue
            if net.kind == "iface":
                s.port_auto = False
                continue
            marker = {"output": "AUTOOUTPUT", "input": "AUTOINPUT", "inout": "AUTOINOUT"}.get(s.direction or "", None)
            s.port_auto = bool(pin_auto and marker and parent_mod.has_marker(marker))
        # all-or-nothing per (parent module, net name)
        explicit: set[tuple[str, str]] = set()
        for s in steps:
            if not s.pin_auto:
                explicit.add((s.parent.module_name, s.parent_name))
        for s in steps:
            if (s.parent.module_name, s.parent_name) in explicit:
                s.pin_auto = False
                s.port_auto = False

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def _sf(self, inst: Instance):
        assert inst.module is not None
        return self.design.files[inst.module.file]

    def _comment(self, net: RouteNet) -> Optional[str]:
        return f"// routed: {net.spec.label()}" if net.spec.comment else None

    def _uses_logic(self, mod: ModuleDef) -> bool:
        sf = self.design.files[mod.file]
        if sf.path.lower().endswith(".sv"):
            return True
        return any((p.type_text or "").find("logic") >= 0 for p in mod.ports)

    def _port_decl_text(self, net: RouteNet, mod: ModuleDef, name: str, direction: Optional[str], side: str,
                        ansi: bool, modport: Optional[str]) -> str:
        ep = net.src_ep
        if net.kind == "iface":
            mp = modport if net.spec.modport_policy == "carry" else None
            return f"{ep.iface_type}.{mp} {name}" if mp else f"{ep.iface_type} {name}"
        if net.kind == "struct":
            t = ep.struct_qualified_name or ep.symbolic_type_text or "logic"
            return f"{direction} {t} {name}" if ansi else f"{direction} {t} {name};"
        parts = [direction or "input"]
        if self._uses_logic(mod):
            parts.append("logic")
        sym = ep.symbolic_type_text or ""
        if "signed" in sym.split() or "signed" in (ep.type_text_numeric or ""):
            pass
        dims = ep.packed_dims_numeric if ep.param_dependent else ep.symbolic_packed_dims
        src_pinfo = None
        for pp in net.pairs:
            if pp.src_inst.module is not None:
                src_pinfo = pp.src_inst.module.port(net.identity.port if side == "src" else pp.pair.src.port) or src_pinfo
        if src_pinfo is not None and src_pinfo.signed:
            parts.append(src_pinfo.signed)
        if dims:
            parts.append(dims)
        parts.append(name)
        text = " ".join(parts)
        return text if ansi else text + ";"

    def _net_decl_text(self, net: RouteNet, mod: ModuleDef, name: str) -> str:
        ep = net.src_ep
        if net.kind == "struct":
            return f"{ep.struct_qualified_name or ep.symbolic_type_text} {name};"
        kw = "logic" if self._uses_logic(mod) else "wire"
        parts = [kw]
        src_pinfo = None
        for pp in net.pairs:
            if pp.src_inst.module is not None:
                src_pinfo = pp.src_inst.module.port(pp.pair.src.port) or src_pinfo
        if src_pinfo is not None and src_pinfo.signed:
            parts.append(src_pinfo.signed)
        dims = ep.packed_dims_numeric if ep.param_dependent else ep.symbolic_packed_dims
        if dims:
            parts.append(dims)
        parts.append(name)
        return " ".join(parts) + ";"

    def _iface_inst_text(self, net: RouteNet, host: Instance, name: str, res: RouteResult) -> str:
        ep = net.src_ep
        design = self.design
        iface_mod = design.modules.get(ep.iface_type or "")
        params = ""
        if net.spec.iface_params:
            params = " #(" + ", ".join(f".{k}({v})" for k, v in net.spec.iface_params.items()) + ")"
        conns: list[str] = []
        host_mod = host.module
        if iface_mod is not None:
            for p in iface_mod.ports:
                if p.is_interface:
                    continue
                if p.name in net.spec.iface_conn:
                    conns.append(f".{p.name} ({net.spec.iface_conn[p.name]})")
                elif host_mod is not None and (host_mod.port(p.name) is not None or p.name in host_mod.symbols):
                    conns.append(f".{p.name} ({p.name})")
                else:
                    conns.append(f".{p.name} ()")
                    self._warn("W_IFACE_PORT_UNCONNECTED", f"interface instance {name} in {host.module_name}: port {p.name} left unconnected (set iface_conn)", route=net.spec.label(), inst=host)
        return f"{ep.iface_type}{params} {name} ({', '.join(conns)});"

    def _host_insert_point(self, mod: ModuleDef) -> tuple[int, str]:
        """(char offset of a line, 'after'|'before') for a body declaration in *mod*."""
        sf = self.design.files[mod.file]
        for kind in ("AUTOWIRE", "AUTOLOGIC"):
            for mi in mod.markers.get(kind, []):
                if mi.fence_range is not None:
                    return mi.fence_range.end - 1, "after"
                return mi.range.end - 1, "after"
        insts = [r for r in mod.refs if r.kind == "inst"]
        if insts:
            first = min(insts, key=lambda r: r.range.start)
            return first.range.start, "before"
        return mod.end_range.start, "before"

    def _body_port_insert_point(self, mod: ModuleDef) -> tuple[int, str]:
        sf = self.design.files[mod.file]
        decls = [p.decl_range for p in mod.ports if p.decl_range is not None and not p.in_auto_fence]
        if decls:
            last = max(decls, key=lambda r: r.end)
            return last.end - 1, "after"
        return mod.header_end - 1, "after"

    def _emit(self, net: RouteNet, steps: list[Step], res: RouteResult, edits: EditSet) -> None:
        design = self.design
        label = net.spec.label()
        comment = self._comment(net)
        chain_paths: set[str] = set()
        for pp in net.pairs:
            chain_paths |= {i.path for i in pp.src_chain} | {i.path for i in pp.dst_chain}

        # 1. dst ports to create
        for pp in net.pairs:
            if pp.dst_pinfo is None:
                dm = pp.dst_inst.module
                assert dm is not None
                modport = pp.dst_modport if net.kind == "iface" else None
                self._create_port(net, dm, pp.pair.dst.port, pp.dst_direction, "dst", modport, res, edits, pp.dst_inst)

        # 2. intermediate ports and pins
        for s in steps:
            pm = s.parent.module
            cm = s.child.module
            assert pm is not None and cm is not None
            # port on the parent (intermediate only)
            if not s.parent_is_host:
                if s.port_auto:
                    via = {"output": "AUTOOUTPUT", "input": "AUTOINPUT", "inout": "AUTOINOUT"}[s.direction or "input"]
                    self._note_auto(res, AutoNativeItem(pm.name, s.parent_name, "port", via))
                else:
                    modport = None
                    if net.kind == "iface":
                        if net.spec.modport_policy == "carry":
                            modport = net.src_ep.modport if s.side == "src" else next(
                                (pp.dst_modport for pp in net.pairs if s.child.path.startswith(pp.dst_chain[0].path)), None)
                    self._create_port(net, pm, s.parent_name, s.direction, s.side, modport, res, edits, s.parent)
            # pin in the parent for the child
            if s.pin_auto:
                self._note_auto(res, AutoNativeItem(pm.name, s.parent_name, "pin", "AUTOINST"))
            else:
                self._create_pin(net, s, res, edits)

        # 3. net / interface instance at the host (lca mode only)
        for pp in net.pairs:
            if pp.mode != "lca":
                continue
            host = pp.lca
            hm = host.module
            assert hm is not None
            host_steps = [s for s in steps if s.parent is host]
            name = host_steps[0].parent_name if host_steps else net.base_name
            key = ("net", hm.name, name)
            if key in self._edit_keys or name in self._created_nets.get(hm.name, set()):
                continue
            if net.kind != "iface" and all(s.pin_auto for s in host_steps) and hm.has_marker("AUTOWIRE", "AUTOLOGIC") and self._struct_ok(hm, net):
                self._note_auto(res, AutoNativeItem(hm.name, name, "net", "AUTOWIRE"))
                self._created_nets.setdefault(hm.name, set()).add(name)
                continue
            existing = hm.symbols.get(name)
            if existing is not None and not existing.in_auto_fence:
                if net.kind == "iface" and existing.kind in ("instance", "iface_inst") and (existing.type_text or "") == net.src_ep.iface_type:
                    res.infos.append(Diagnostic("I_EXISTS", "info", f"{hm.name}: interface instance {name} already exists", route=label))
                    self._created_nets.setdefault(hm.name, set()).add(name)
                    continue
                if net.kind != "iface" and existing.kind in ("net", "var"):
                    res.infos.append(Diagnostic("I_EXISTS", "info", f"{hm.name}: net {name} already exists", route=label))
                    self._created_nets.setdefault(hm.name, set()).add(name)
                    continue
                self._err("E_NAME_COLLISION", f"{hm.name}: {name} already exists as a {existing.kind}", route=label, rng=existing.range)
                continue
            sf = design.files[hm.file]
            if net.kind == "iface":
                text = self._iface_inst_text(net, host, name, res)
                res.created_instances.append((hm.name, text))
            else:
                text = self._net_decl_text(net, hm, name)
                res.created_nets.append((hm.name, text))
            line = text + (f"   {comment}" if comment else "")
            at, where = self._host_insert_point(hm)
            indent = line_indent(sf.text, at) if where == "after" else line_indent(sf.text, at)
            if where == "after":
                e = insert_lines_after(sf, at, [line], indent=indent or "   ", kind="iface_inst" if net.kind == "iface" else "net",
                                       module=hm.name, route=label, description=f"{hm.name}: {text}", key=key)
            else:
                e = insert_lines_before(sf, at, [line], indent=indent or "   ", kind="iface_inst" if net.kind == "iface" else "net",
                                        module=hm.name, route=label, description=f"{hm.name}: {text}", key=key)
            self._add_edit(edits, e, res)
            self._created_nets.setdefault(hm.name, set()).add(name)

    def _queue_list_entry(self, sf, list_range: SrcRange, anchor: Optional[SrcRange], entry: str,
                          comment: Optional[str], res: RouteResult, *, pad_col: Optional[int] = None,
                          followers: Optional[bool] = None, child_ports: Optional[set] = None,
                          explicit: Optional[set] = None, **meta) -> None:
        """Queue one list entry; entries of one list/anchor are emitted together."""
        key = meta.get("key")
        if key and key in self._edit_keys:
            return
        lkey = (sf.key, list_range.start, anchor.start if anchor is not None else -1)
        batch = self._lists.setdefault(lkey, {
            "sf": sf, "list_range": list_range, "anchor": anchor, "entries": [], "metas": [],
            "pad_col": pad_col, "followers": False, "child_ports": set(), "explicit": set(), "results": [],
        })
        batch["entries"].append((entry, comment))
        batch["metas"].append(meta)
        batch["results"].append(res)
        if pad_col is not None:
            batch["pad_col"] = pad_col
        if followers:
            batch["followers"] = True
        if child_ports is not None:
            batch["child_ports"] |= child_ports
        if explicit is not None:
            batch["explicit"] |= explicit
        if key:
            self._edit_keys[key] = TextEdit(sf.key, list_range.start, list_range.start, "", **{k: v for k, v in meta.items() if k != "key"}, key=key)

    def _flush_lists(self, edits: EditSet) -> None:
        for batch in self._lists.values():
            followers = batch["followers"]
            if batch["anchor"] is not None and batch["child_ports"]:
                planned = {e.split(" ")[0].lstrip(".") for e, _ in batch["entries"]}
                followers = followers or bool(batch["child_ports"] - batch["explicit"] - planned)
            meta = dict(batch["metas"][0])
            meta.pop("key", None)
            meta["description"] = "; ".join(m.get("description", "") for m in batch["metas"])
            new_edits = insert_list_entries(batch["sf"], batch["list_range"], batch["entries"],
                                            anchor=batch["anchor"], followers_nonempty=followers,
                                            pad_col=batch["pad_col"], **meta)
            for e in new_edits:
                edits.add(e)
                for res in batch["results"]:
                    if e not in res.edits:
                        res.edits.append(e)

    @staticmethod
    def _note_auto(res: RouteResult, item: AutoNativeItem) -> None:
        if not any(a.module == item.module and a.net == item.net and a.what == item.what for a in res.auto_native):
            res.auto_native.append(item)

    def _add_edit(self, edits: EditSet, e: TextEdit, res: RouteResult) -> None:
        if e.key and e.key in self._edit_keys:
            return
        if e.key:
            self._edit_keys[e.key] = e
        edits.add(e)
        res.edits.append(e)

    def _create_port(self, net: RouteNet, mod: ModuleDef, name: str, direction: Optional[str], side: str,
                     modport: Optional[str], res: RouteResult, edits: EditSet, inst: Instance) -> None:
        label = net.spec.label()
        design = self.design
        planned = self._created_ports.setdefault(mod.name, {})
        if name in planned:
            return
        existing = mod.port(name)
        if existing is not None and not existing.in_auto_fence:
            ok = (existing.is_interface and net.kind == "iface" and existing.iface_type == net.src_ep.iface_type) or \
                 (not existing.is_interface and net.kind != "iface" and existing.direction == direction)
            if ok:
                res.infos.append(Diagnostic("I_EXISTS", "info", f"{mod.name}: port {name} already exists", route=label))
                planned[name] = existing
                return
            self._err("E_PORT_CONFLICT", f"{mod.name} already has a port {name} ({existing.direction or existing.iface_type}) incompatible with the route", route=label, rng=existing.range)
            return
        sym = mod.symbols.get(name)
        if sym is not None and not sym.in_auto_fence and sym.kind != "port":
            self._err("E_NAME_COLLISION", f"{mod.name}: {name} already exists as a {sym.kind}", route=label, rng=sym.range)
            return
        if net.kind == "struct" and mod.has_marker("AUTOARG") and not self._struct_ok(mod, net):
            self._err("E_TYPEDEF_REGEXP", f"{mod.name} uses AUTOARG but its typedef regexp does not match {net.src_ep.struct_qualified_name}; add '// verilog-typedef-regexp: \"_t$\"' to its Local Variables", route=label, inst=inst)
            return
        sf = design.files[mod.file]
        comment = self._comment(net)
        if mod.ansi and mod.port_list_range is not None:
            decl = self._port_decl_text(net, mod, name, direction, side, True, modport)
            anchor = None
            for kind in _PORT_LIST_MARKERS:
                for mi in mod.markers.get(kind, []):
                    if mod.port_list_range.contains(mi.range.start):
                        if anchor is None or mi.range.start < anchor.start:
                            anchor = mi.range
            self._queue_list_entry(sf, mod.port_list_range, anchor, decl, comment, res, followers=True,
                                   kind="ansi_port", module=mod.name, route=label,
                                   description=f"{mod.name}: port {decl}", key=("port", mod.name, name))
        else:
            if net.kind == "iface":
                self._err("E_NONANSI_IFACE",
                          f"{mod.name} has a non-ANSI (AUTOARG-style) port list; an interface port {name} needs an ANSI "
                          f"header: convert '{mod.name}' to 'module {mod.name} (input ..., /*AUTOINPUT*/ /*AUTOOUTPUT*/);' style "
                          f"or route the interface to an ANSI ancestor", route=label, inst=inst)
                return
            if mod.port_list_range is None:
                self._err("E_UNEDITABLE", f"{mod.name} has no port list; cannot add port {name}", route=label, inst=inst)
                return
            decl = self._port_decl_text(net, mod, name, direction, side, False, None)
            at, where = self._body_port_insert_point(mod)
            line = decl + (f"   {comment}" if comment else "")
            e = insert_lines_after(sf, at, [line], kind="body_port", module=mod.name, route=label,
                                   description=f"{mod.name}: {decl}", key=("port", mod.name, name))
            self._add_edit(edits, e, res)
            if not mod.has_marker("AUTOARG"):
                self._queue_list_entry(sf, mod.port_list_range, None, name, None, res, kind="arglist_name",
                                       module=mod.name, route=label, description=f"{mod.name}: header name {name}",
                                       key=("argname", mod.name, name))
        planned[name] = PortInfo(name=name, direction=direction, is_interface=(net.kind == "iface"),
                                 iface_type=net.src_ep.iface_type, modport=modport, ansi=mod.ansi)
        res.created_ports.append((mod.name, decl))

    def _create_pin(self, net: RouteNet, s: Step, res: RouteResult, edits: EditSet) -> None:
        label = net.spec.label()
        design = self.design
        ref = s.child.ref
        pm = s.parent.module
        cm = s.child.module
        assert pm is not None and cm is not None
        if ref is None or ref.conn_range is None:
            self._err("E_UNEDITABLE", f"{s.child.path}: instantiation site not found in {pm.name}", route=label, inst=s.parent)
            return
        if ref.connections_style == "ordered":
            self._err("E_ORDERED_CONNECTIONS", f"{s.child.path} uses positional connections; cannot add pin .{s.child_port}", route=label, rng=ref.range)
            return
        key = ("pin", pm.name, ref.inst_name or "", s.child_port)
        prev = self._planned_pins.get((pm.name, ref.inst_name or "", s.child_port))
        if prev is not None:
            if prev[0] != s.parent_name:
                self._err("E_PIN_CONFLICT", f"{s.child.path}.{s.child_port} is routed to both {prev[0]} and {s.parent_name}", route=label, rng=ref.range)
            return
        self._planned_pins[(pm.name, ref.inst_name or "", s.child_port)] = (s.parent_name, label)
        existing = next((p for p in ref.explicit_pins if p.port == s.child_port), None)
        if existing is not None:
            root = re.match(r"\s*([A-Za-z_]\w*)", existing.expr_text or "")
            if existing.expr_text == "" or root is None:
                if existing.expr_text == "":
                    # `.p ()` placeholder (e.g. from an earlier unrouted pass): fill it in
                    text = f".{s.child_port} ({s.parent_name})"
                    sf = design.files[pm.file]
                    e = TextEdit(sf.key, existing.range.start, existing.range.end, text, kind="pin", module=pm.name,
                                 route=label, description=f"{pm.name}.{ref.inst_name}: {text}", key=key)
                    self._add_edit(edits, e, res)
                    return
            elif root.group(1) == s.parent_name:
                res.infos.append(Diagnostic("I_EXISTS", "info", f"{s.child.path}.{s.child_port} already connected to {s.parent_name}", route=label))
                return
            self._err("E_PIN_CONFLICT", f"{s.child.path}.{s.child_port} is already connected to {existing.expr_text!r}, not {s.parent_name}", route=label, rng=existing.range)
            return
        sf = design.files[pm.file]
        text = sf.text
        indent_col = column_of(text, ref.conn_range.start) + 1
        auto_col = max(40, 16 + 8 * ((indent_col + 7) // 8))
        entry = f".{s.child_port}"
        pad = auto_col - indent_col
        anchor = ref.marker_range if ref.marker_range is not None else ref.dotstar_range
        followers = False
        child_ports: set = set()
        if anchor is not None:
            child_ports = {p.name for p in cm.ports if not p.in_auto_fence} | set(self._created_ports.get(cm.name, {}))
            explicit = {p.port for p in ref.explicit_pins} | {s.child_port}
            followers = bool(child_ports - explicit)
        self._queue_list_entry(sf, ref.conn_range, anchor, f"{entry} ({s.parent_name})", self._comment(net), res,
                               pad_col=pad, followers=followers, child_ports=child_ports if anchor is not None else None,
                               explicit={p.port for p in ref.explicit_pins}, kind="pin", module=pm.name, route=label,
                               description=f"{pm.name}.{ref.inst_name}: .{s.child_port} ({s.parent_name})", key=key)

    # ------------------------------------------------------------------
    # Unrouted instances and implicit connections
    # ------------------------------------------------------------------

    def _check_unrouted_and_implicit(self, nets: list[RouteNet], steps: list[Step], results: list[RouteResult], edits: EditSet) -> None:
        design = self.design
        res_of = {id(net): next(r for r in results if r.spec is net.spec) for net in nets}
        # (module, port) -> routed instance paths (all nets), and routed pin keys
        routed_paths: dict[tuple[str, str], set[str]] = {}
        routed_pins: set[tuple[str, str, str]] = set()          # (parent module, child inst base, child port)
        owner: dict[tuple[str, str], RouteNet] = {}
        endpoint_paths: dict[tuple[str, str], set[str]] = {}
        for s in steps:
            if not s.parent_is_host:
                routed_paths.setdefault((s.parent.module_name, s.parent_name), set()).add(s.parent.path)
                owner.setdefault((s.parent.module_name, s.parent_name), s.net)
            routed_pins.add((s.parent.module_name, s.child.name.split("[")[0], s.child_port))
        for net in nets:
            for pp in net.pairs:
                if pp.dst_pinfo is None:
                    k = (pp.dst_inst.module_name, pp.pair.dst.port)
                    endpoint_paths.setdefault(k, set()).add(pp.dst_inst.path)
                    owner.setdefault(k, net)
        for (mod_name, port_name), net in owner.items():
            res = res_of[id(net)]
            label = net.spec.label()
            ok_paths = routed_paths.get((mod_name, port_name), set()) | endpoint_paths.get((mod_name, port_name), set())
            is_boundary = (mod_name, port_name) in endpoint_paths and (mod_name, port_name) not in routed_paths
            for inst in design.instances_of(mod_name):
                if inst.path in ok_paths and not is_boundary:
                    continue
                parent = inst.parent
                if parent is None or parent.module is None:
                    continue
                inst_base = inst.name.split("[")[0]
                ref = inst.ref
                if is_boundary:
                    code, msg = "W_BOUNDARY", f"{inst.path}: boundary port {port_name} is left unconnected in {parent.module_name}"
                else:
                    code, msg = "W_UNROUTED", f"{inst.path} ({mod_name}) gets the new port {port_name} but is not routed (left unconnected)"
                if ref is not None and ref.uses_autoinst:
                    if (parent.module_name, inst_base, port_name) in routed_pins:
                        # the same instantiation text serves a routed instance; the
                        # disconnect must happen further up (handled at that level)
                        continue
                    if not any(p.port == port_name for p in ref.explicit_pins) and \
                            (parent.module_name, inst_base, port_name) not in self._planned_pins:
                        sf = design.files[parent.module.file]
                        indent_col = column_of(sf.text, ref.conn_range.start) + 1
                        pad = max(40, 16 + 8 * ((indent_col + 7) // 8)) - indent_col
                        cm = inst.module
                        child_ports = {p.name for p in cm.ports if not p.in_auto_fence} | set(self._created_ports.get(cm.name, {})) if cm else set()
                        self._queue_list_entry(sf, ref.conn_range, ref.marker_range or ref.dotstar_range, f".{port_name} ()",
                                               "// routed: unconnected here", res, pad_col=pad, followers=True,
                                               child_ports=child_ports, explicit={p.port for p in ref.explicit_pins},
                                               kind="pin_unconnected", module=parent.module_name, route=label,
                                               description=f"{parent.module_name}.{ref.inst_name}: .{port_name} ()",
                                               key=("pin", parent.module_name, ref.inst_name or "", port_name))
                        self._planned_pins[(parent.module_name, inst_base, port_name)] = ("", label)
                self._warn(code, msg, route=label, inst=inst)
        # implicit connections: other AUTOINST children of a module hosting the net
        # whose module has a port named like the net and no explicit pin for it
        seen: set[tuple] = set()
        for s in steps:
            pm = s.parent.module
            if pm is None:
                continue
            for ref in pm.refs:
                if ref.kind != "inst" or not ref.uses_autoinst or ref.resolved is None:
                    continue
                inst_base = ref.inst_name or ""
                if (pm.name, inst_base, s.parent_name) in routed_pins or (pm.name, inst_base, s.parent_name) in self._planned_pins:
                    continue
                if ref.resolved.port(s.parent_name) is not None and not any(p.port == s.parent_name for p in ref.explicit_pins):
                    k = (pm.name, inst_base, s.parent_name)
                    if k in seen:
                        continue
                    seen.add(k)
                    self._err("E_IMPLICIT_CONNECTION", f"{pm.name}: instance {inst_base} of {ref.module} has a port {s.parent_name} that AUTOINST would connect to the routed net; connect it explicitly first", route=s.net.spec.label(), rng=ref.range)


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def apply_routes(design: "Design", specs, *, dry_run: bool = False, strict: bool = False,
                 then_expand: bool = False, log=None) -> Report:
    planner = RoutePlanner(design, specs, strict=strict)
    plan = planner.plan()
    report = Report(results=plan.results, edits=plan.all_edits, warnings=list(plan.warnings), dry_run=dry_run)
    rendered = plan.edits.render()
    report.diff = plan.edits.diff(rendered)
    report.files_changed = plan.edits.apply(dry_run=dry_run)
    if then_expand:
        report.expand_report = design.expand_all(dry_run=dry_run, log=log)
        report.expanded = True
        try:
            re_plan = RoutePlanner(design, planner.specs, strict=False).plan()
            report.residual = re_plan.all_edits
            if report.residual:
                report.warnings.append(Diagnostic("W_RESIDUAL", "warning",
                                                  f"{len(report.residual)} edit(s) still pending after expansion (typedef regexp / markers?)"))
        except RouteError as exc:
            report.warnings.append(Diagnostic("W_RESIDUAL", "warning", "re-plan after expansion failed: " + "; ".join(str(d) for d in exc.diagnostics)))
    return report
