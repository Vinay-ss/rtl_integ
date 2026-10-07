"""Connectivity index of one module (pyslang syntax of the integrated file).

For a module it records every *owner* (top-level item: instantiation,
declaration, assign, always, header port ...), the identifiers each owner
references, the declarations, and for every child instantiation its pins
with the identifiers of each pin expression.  Structural operations ask:

* which local names does a set of instances use, and through which pins?
* is a name also used outside those instances (or is it a port)?
* how is a name declared (type / dims text, AUTO-generated or written)?

Offsets are char offsets into ``SourceFile.text``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..integ.design import Design
from ..integ.model import ModuleDef
from ..integ.sources import SourceFile


class ConnectError(RuntimeError):
    pass


@dataclass
class Owner:
    id: int
    kind: str                 # port | inst | decl | param | assign | always | generate | other
    name: Optional[str]       # instance / declared name when meaningful
    start: int
    end: int


@dataclass
class Pin:
    port: str
    expr: str                       # connection expression text ("" when unconnected)
    names: list[str]                # local identifiers referenced by the expression
    start: int                      # whole ``.port(expr)`` entry
    end: int
    expr_start: Optional[int] = None
    expr_end: Optional[int] = None
    implicit: bool = False          # ``.port`` or covered by ``.*``
    in_fence: bool = False          # inside an AUTOINST fence
    direction: Optional[str] = None # child's port direction (None: interface / unknown)


@dataclass
class InstUse:
    name: str
    module: str
    owner: int
    start: int                      # statement range
    end: int
    pins: list[Pin] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)        # override text
    param_names: dict[str, list[str]] = field(default_factory=dict)
    param_start: Optional[int] = None                           # "#(" ... ")" range
    param_end: Optional[int] = None
    conn_open: int = 0              # "(" of the connection list
    conn_close: int = 0             # ")"
    ordered: bool = False           # positional connections (not supported by the ops)
    shared_statement: bool = False  # more than one instance in the statement
    in_generate: bool = False
    uses_autoinst: bool = False

    def pin(self, port: str) -> Optional[Pin]:
        for p in self.pins:
            if p.port == port:
                return p
        return None


@dataclass
class Decl:
    name: str
    kind: str                       # net | var | port | param | localparam | iface_inst | typedef | genvar
                                    # | function | enumval
    owner: int
    start: int                      # whole statement
    end: int
    decl_start: int                 # this declarator (name .. end of its dims / initializer)
    decl_end: int
    type_text: str = ""             # e.g. "logic", "wire", "logic signed"
    dims: str = ""                  # packed dims text, e.g. "[W-1:0]"
    unpacked: str = ""
    n_declarators: int = 1
    in_fence: bool = False
    direction: Optional[str] = None # ports
    iface_type: Optional[str] = None
    modport: Optional[str] = None
    value: Optional[str] = None     # parameter default (type parameter: default type); typedef: its type;
                                    # enumval: the typedef or variable declaring it
    is_type: bool = False           # ``parameter type``
    refs: list[str] = field(default_factory=list)   # names the declaration itself references

    @property
    def full_type(self) -> str:
        """Type with packed dimensions, e.g. ``logic [7:0]``."""
        return f"{self.type_text} {self.dims}".strip()


def _port_type(info) -> str:
    """Declared type of a port with its signing, e.g. ``logic signed``
    (an implicit type with a signing becomes ``wire``/``logic``)."""
    if info is None:
        return ""
    t = info.type_text or ""
    if info.signed:
        t = f"{t or ('logic' if info.direction == 'output' else 'wire')} {info.signed}"
    return t


def _sx():
    from pyslang.syntax import SyntaxKind, SyntaxNode

    return SyntaxKind, SyntaxNode, None


def package_names(design: Design, pkg: str) -> set[str]:
    """Names a package makes visible to an importer: parameters, types and
    their enum values, variables, functions and tasks."""
    SyntaxKind, SyntaxNode, _ = _sx()
    mod = design.modules.get(pkg)
    if mod is None or mod.kind != "package" or mod.syntax is None:
        return set()
    names: set[str] = set()

    def enums(n) -> None:
        if not isinstance(n, SyntaxNode):
            return
        if n.kind == SyntaxKind.EnumType:
            names.update(d.name.valueText for d in n.members if isinstance(d, SyntaxNode))
        for c in n:
            enums(c)

    for m in mod.syntax.members:
        if not isinstance(m, SyntaxNode):
            continue
        k = m.kind
        if k == SyntaxKind.TypedefDeclaration:
            names.add(m.name.valueText)
            enums(m.type)
        elif k == SyntaxKind.ParameterDeclarationStatement:
            names.update(d.name.valueText for d in m.parameter.declarators if isinstance(d, SyntaxNode))
        elif k in (SyntaxKind.DataDeclaration, SyntaxKind.NetDeclaration):
            names.update(d.name.valueText for d in m.declarators if isinstance(d, SyntaxNode))
            enums(m.type)
        elif k in (SyntaxKind.FunctionDeclaration, SyntaxKind.TaskDeclaration):
            names.add(str(m.prototype.name).strip())
        elif k == SyntaxKind.ClassDeclaration:
            names.add(m.name.valueText)
    return names


class ModuleIndex:
    """Connectivity facts of one module in a (pyslang) Design."""

    def __init__(self, design: Design, module: ModuleDef):
        if module.syntax is None:
            raise ConnectError(f"{module.name}: no pyslang syntax (structural operations need pyslang)")
        self.design = design
        self.module = module
        self.sf: SourceFile = design.files[module.file]
        self.text = self.sf.text
        self.owners: list[Owner] = []
        self.insts: dict[str, InstUse] = {}
        self.decls: dict[str, Decl] = {}
        self.uses: dict[str, list[tuple[int, int]]] = {}    # name -> [(offset, owner id)]
        self.aliases: list[tuple[str, str]] = []             # ``assign a = b;`` between plain names
        self.imports: list[str] = []                          # "pkg::*" / "pkg::name" visible in the module
        self.imported: set[str] = set()                       # names those imports provide
        self.fences = self._fence_spans()
        self._build()

    # -- helpers ----------------------------------------------------------

    def _fence_spans(self) -> list[tuple[int, int]]:
        spans = []
        for ms in self.module.markers.values():
            for m in ms:
                if m.fence_range is not None:
                    spans.append((m.fence_range.start, m.fence_range.end))
        return spans

    def in_fence(self, off: int) -> bool:
        return any(s <= off < e for s, e in self.fences)

    def _off(self, tok) -> int:
        return self.sf.char_offset(tok.location.offset)

    def _node_span(self, node) -> tuple[int, int]:
        r = node.sourceRange
        return self.sf.char_offset(r.start.offset), self.sf.char_offset(r.end.offset)

    def _src(self, node) -> str:
        if node is None:
            return ""
        s, e = self._node_span(node)
        return self.text[s:e]

    def _new_owner(self, kind: str, name: Optional[str], node) -> Owner:
        s, e = self._node_span(node)
        o = Owner(len(self.owners), kind, name, s, e)
        self.owners.append(o)
        return o

    def _idents(self, node) -> list[tuple[str, int]]:
        """Local identifiers referenced in *node* (leftmost part of dotted
        names; package-scoped names and member selects are skipped)."""
        SyntaxKind, SyntaxNode, Token = _sx()
        out: list[tuple[str, int]] = []

        def walk(n) -> None:
            if n is None or not isinstance(n, SyntaxNode):
                return
            k = n.kind
            if k == SyntaxKind.ScopedName:
                if n.separator.kind.name == "DoubleColon":
                    return
                walk(n.left)
                return
            if k == SyntaxKind.MemberAccessExpression:
                walk(n.left)
                return
            if k in (SyntaxKind.IdentifierName, SyntaxKind.IdentifierSelectName):
                tok = n.identifier
                if tok is not None and not tok.isMissing and tok.valueText:
                    out.append((tok.valueText, self._off(tok)))
                if k == SyntaxKind.IdentifierSelectName:
                    for sel in n.selectors:
                        walk(sel)
                return
            if k == SyntaxKind.SystemName:
                return
            for c in n:
                walk(c)

        walk(node)
        return out

    def _use(self, node, owner: Owner, skip: Iterable[int] = ()) -> None:
        skipset = set(skip)
        for name, off in self._idents(node):
            if off in skipset:
                continue
            self.uses.setdefault(name, []).append((off, owner.id))

    # -- build ---------------------------------------------------------------

    def _import(self, node) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        for it in node.items:
            if not isinstance(it, SyntaxNode):
                continue
            pkg, item = it.package.valueText, it.item.valueText
            if f"{pkg}::{item}" not in self.imports:
                self.imports.append(f"{pkg}::{item}")
            self.imported |= package_names(self.design, pkg) if item == "*" else {item}

    def _unit_imports(self) -> list:
        """``import`` declarations of the compilation unit before the module."""
        SyntaxKind, SyntaxNode, Token = _sx()
        decl = self.module.syntax
        root = getattr(decl, "parent", None)
        if root is None or root.kind != SyntaxKind.CompilationUnit:
            return []
        here = decl.sourceRange.start.offset
        return [m for m in root.members if isinstance(m, SyntaxNode) and m.kind == SyntaxKind.PackageImportDeclaration
                and m.sourceRange.start.offset < here]

    def is_imported(self, name: str) -> bool:
        """*name* comes from a package import (and is not declared locally)."""
        return name not in self.decls and name in self.imported

    def _build(self) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        decl = self.module.syntax
        header = decl.header
        for imp in self._unit_imports():
            self._import(imp)
        for imp in getattr(header, "imports", None) or []:
            if isinstance(imp, SyntaxNode):
                self._import(imp)
        hdr_owner = self._new_owner("port", None, header)
        params = getattr(header, "parameters", None)
        if params is not None:
            self._param_list(params, hdr_owner)
        ports = getattr(header, "ports", None)
        if ports is not None:
            if ports.kind == SyntaxKind.AnsiPortList:
                self._ansi_ports(ports)
            else:
                self._use(ports, hdr_owner)
        for m in decl.members:
            if isinstance(m, SyntaxNode):
                self._member(m, in_generate=False)

    def _param_list(self, params, owner: Owner) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        for p in params.declarations:
            if not isinstance(p, SyntaxNode):
                continue
            if p.kind in (SyntaxKind.ParameterDeclaration, SyntaxKind.TypeParameterDeclaration):
                for d in p.declarators:
                    if not isinstance(d, SyntaxNode):
                        continue
                    s, e = self._node_span(d)
                    self._param_decl(p, d, owner, s, e, s, e, 1)
            else:
                self._use(p, owner)

    def _param_decl(self, p, d, owner: Owner, start: int, end: int, ds: int, de: int, n_decl: int) -> None:
        """Record one declarator of a (type) parameter declaration."""
        SyntaxKind, SyntaxNode, Token = _sx()
        kw = p.keyword.valueText if getattr(p, "keyword", None) is not None else "parameter"
        kind = "localparam" if kw == "localparam" else "param"
        name = d.name.valueText
        if p.kind == SyntaxKind.TypeParameterDeclaration:
            init = d.assignment.type if d.assignment is not None else None
            self.decls[name] = Decl(name, kind, owner.id, start, end, ds, de, value=self._src(init).strip() or None,
                                    n_declarators=n_decl, is_type=True, refs=self._names(init))
        else:
            init = d.initializer.expr if d.initializer is not None else None
            self.decls[name] = Decl(name, kind, owner.id, start, end, ds, de, type_text=self._src(p.type).strip(),
                                    value=self._src(init) if init is not None else None, n_declarators=n_decl,
                                    refs=self._names(p.type, init))
        if init is not None:
            self._use(init, owner)

    def _names(self, *nodes) -> list[str]:
        out: list[str] = []
        for node in nodes:
            for n, _off in self._idents(node):
                if n not in out:
                    out.append(n)
        return out

    def _ansi_ports(self, ports) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        for p in ports.ports:
            if not isinstance(p, SyntaxNode):
                continue
            owner = self._new_owner("port", None, p)
            name_tok = p.declarator.name if hasattr(p, "declarator") and p.declarator is not None else None
            name = name_tok.valueText if name_tok is not None else None
            if name:
                owner.name = name
                info = self.module.port(name)
                s, e = self._node_span(p)
                self.decls[name] = Decl(
                    name, "port", owner.id, s, e, s, e,
                    type_text=_port_type(info),
                    dims=(info.packed_dims or "") if info else "",
                    unpacked=(info.unpacked_dims or "") if info else "",
                    in_fence=self.in_fence(s), direction=info.direction if info else None,
                    iface_type=info.iface_type if info else None, modport=info.modport if info else None,
                )
            skip = [self._off(name_tok)] if name_tok is not None else []
            self._use(p, owner, skip=skip)

    def _member(self, m, *, in_generate: bool) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        k = m.kind
        if k == SyntaxKind.HierarchyInstantiation:
            self._instantiation(m, in_generate)
        elif k in (SyntaxKind.DataDeclaration, SyntaxKind.NetDeclaration):
            self._declaration(m, "var" if k == SyntaxKind.DataDeclaration else "net")
        elif k == SyntaxKind.PortDeclaration:
            self._body_port(m)
        elif k == SyntaxKind.PackageImportDeclaration:
            self._import(m)
        elif k == SyntaxKind.ParameterDeclarationStatement:
            owner = self._new_owner("param", None, m)
            p = m.parameter
            ms, me = self._node_span(m)
            n_decl = sum(1 for x in p.declarators if isinstance(x, SyntaxNode))
            for d in p.declarators:
                if isinstance(d, SyntaxNode):
                    s, e = self._node_span(d)
                    self._param_decl(p, d, owner, ms, me, s, e, n_decl)
        elif k == SyntaxKind.TypedefDeclaration:
            owner = self._new_owner("other", None, m)
            ms, me = self._node_span(m)
            name = m.name.valueText
            unpacked = "".join(self._src(x) for x in m.dimensions if isinstance(x, SyntaxNode))
            self.decls.setdefault(name, Decl(name, "typedef", owner.id, ms, me, ms, me,
                                             value=self._src(m.type).strip(), unpacked=unpacked,
                                             in_fence=self.in_fence(ms), refs=self._names(m.type, *m.dimensions)))
            self._enum_values(m.type, name, owner)
            self._use(m, owner)
        elif k == SyntaxKind.FunctionDeclaration:
            owner = self._new_owner("other", None, m)
            ms, me = self._node_span(m)
            name = str(m.prototype.name).strip()
            self.decls.setdefault(name, Decl(name, "function", owner.id, ms, me, ms, me,
                                             in_fence=self.in_fence(ms), refs=self._names(m)))
            self._use(m, owner)
        elif k == SyntaxKind.ContinuousAssign:
            self._use(m, self._new_owner("assign", None, m))
            for a in m.assignments:
                if not isinstance(a, SyntaxNode) or a.kind != SyntaxKind.AssignmentExpression:
                    continue
                if a.left.kind == SyntaxKind.IdentifierName and a.right.kind == SyntaxKind.IdentifierName:
                    self.aliases.append((a.left.identifier.valueText, a.right.identifier.valueText))
        elif k in (SyntaxKind.AlwaysBlock, SyntaxKind.AlwaysCombBlock, SyntaxKind.AlwaysFFBlock,
                   SyntaxKind.AlwaysLatchBlock, SyntaxKind.InitialBlock, SyntaxKind.FinalBlock):
            self._use(m, self._new_owner("always", None, m))
        elif k == SyntaxKind.GenerateRegion:
            for c in m.members:
                if isinstance(c, SyntaxNode):
                    self._member(c, in_generate=in_generate)
        elif k in (SyntaxKind.LoopGenerate, SyntaxKind.IfGenerate, SyntaxKind.CaseGenerate,
                   SyntaxKind.GenerateBlock):
            owner = self._new_owner("generate", None, m)
            self._use(m, owner)
            self._generate_insts(m)
        else:
            self._use(m, self._new_owner("other", None, m))

    def _generate_insts(self, node) -> None:
        """Record instantiations inside generate constructs (for the verifier)."""
        SyntaxKind, SyntaxNode, Token = _sx()

        def walk(n) -> None:
            if not isinstance(n, SyntaxNode):
                return
            if n.kind == SyntaxKind.HierarchyInstantiation:
                self._instantiation(n, True, record_uses=False)
                return
            for c in n:
                walk(c)

        walk(node)

    def _enum_values(self, node, declared_by: str, owner: Owner) -> None:
        """Record the values of every enum type written in *node*."""
        SyntaxKind, SyntaxNode, Token = _sx()
        if not isinstance(node, SyntaxNode):
            return
        if node.kind == SyntaxKind.EnumType:
            for d in node.members:
                if isinstance(d, SyntaxNode):
                    s, e = self._node_span(d)
                    self.decls.setdefault(d.name.valueText, Decl(d.name.valueText, "enumval", owner.id, s, e, s, e,
                                                                 value=declared_by))
        for c in node:
            self._enum_values(c, declared_by, owner)

    def _declaration(self, m, kind: str) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        owner = self._new_owner("decl", None, m)
        ms, me = self._node_span(m)
        dt = m.type
        full = self._src(dt).strip()
        dims = ""
        if dt is not None and hasattr(dt, "dimensions"):
            dims = "".join(self._src(d) for d in dt.dimensions if isinstance(d, SyntaxNode))
        base = full[: -len(dims)].rstrip() if dims and full.endswith(dims) else full
        type_text = (m.netType.valueText + " " + base).strip() if kind == "net" else base
        decls = [d for d in m.declarators if isinstance(d, SyntaxNode)]
        is_iface = False
        if kind == "var" and dt is not None and dt.kind == SyntaxKind.NamedType:
            tname = self._src(dt).strip()
            is_iface = tname in self.design.modules and self.design.modules[tname].kind == "interface"
        skip = []
        for d in decls:
            s, e = self._node_span(d)
            name = d.name.valueText
            skip.append(self._off(d.name))
            unpacked = "".join(self._src(x) for x in d.dimensions if isinstance(x, SyntaxNode))
            if len(decls) == 1:
                owner.name = name
            self.decls.setdefault(name, Decl(
                name, "iface_inst" if is_iface else kind, owner.id, ms, me, s, e,
                type_text=type_text, dims=dims, unpacked=unpacked, n_declarators=len(decls),
                in_fence=self.in_fence(ms),
            ))
        if decls:
            self._enum_values(dt, decls[0].name.valueText, owner)
        self._use(m, owner, skip=skip)

    def _body_port(self, m) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        owner = self._new_owner("port", None, m)
        ms, me = self._node_span(m)
        skip = []
        decls = [d for d in m.declarators if isinstance(d, SyntaxNode)]
        for d in decls:
            name = d.name.valueText
            skip.append(self._off(d.name))
            info = self.module.port(name)
            s, e = self._node_span(d)
            self.decls[name] = Decl(
                name, "port", owner.id, ms, me, s, e,
                type_text=_port_type(info), dims=(info.packed_dims or "") if info else "",
                unpacked=(info.unpacked_dims or "") if info else "",
                n_declarators=len(decls), in_fence=self.in_fence(ms), direction=info.direction if info else None,
            )
        self._use(m, owner, skip=skip)

    def _instantiation(self, m, in_generate: bool, *, record_uses: bool = True) -> None:
        SyntaxKind, SyntaxNode, Token = _sx()
        module = m.type.valueText or m.type.rawText
        insts = [i for i in m.instances if isinstance(i, SyntaxNode)]
        child = self.design.modules.get(module)
        s, e = self._node_span(m)
        params: dict[str, str] = {}
        pnames: dict[str, list[str]] = {}
        p_start = p_end = None
        if m.parameters is not None:
            p_start, p_end = self._node_span(m.parameters)
            for a in m.parameters.parameters:
                if isinstance(a, SyntaxNode) and a.kind == SyntaxKind.NamedParamAssignment:
                    params[a.name.valueText] = self._src(a.expr) if a.expr is not None else ""
                    pnames[a.name.valueText] = [n for n, _ in self._idents(a.expr)] if a.expr is not None else []
        for hi in insts:
            decl = hi.decl
            name = decl.name.valueText if decl is not None else ""
            owner = self._new_owner("inst", name, m)
            use = InstUse(name=name, module=module, owner=owner.id, start=s, end=e, params=dict(params),
                          param_names=dict(pnames), param_start=p_start, param_end=p_end,
                          conn_open=self._off(hi.openParen), conn_close=self._off(hi.closeParen),
                          shared_statement=len(insts) > 1, in_generate=in_generate)
            if record_uses and m.parameters is not None:
                self._use(m.parameters, owner)
            explicit: set[str] = set()
            wildcard = False
            for c in hi.connections:
                if not isinstance(c, SyntaxNode):
                    continue
                ck = c.kind
                cs, ce = self._node_span(c)
                if ck == SyntaxKind.NamedPortConnection:
                    port = c.name.valueText
                    explicit.add(port)
                    if c.openParen is None or c.openParen.isMissing or c.openParen.rawText != "(":
                        pin = Pin(port, port, [port], cs, ce, implicit=True)
                        if record_uses:
                            self.uses.setdefault(port, []).append((self._off(c.name), owner.id))
                    elif c.expr is None:
                        pin = Pin(port, "", [], cs, ce)
                    else:
                        xs, xe = self._node_span(c.expr)
                        ids = self._idents(c.expr)
                        pin = Pin(port, self.text[xs:xe], [n for n, _ in ids], cs, ce, xs, xe)
                        if record_uses:
                            for n, off in ids:
                                self.uses.setdefault(n, []).append((off, owner.id))
                    pin.in_fence = self.in_fence(cs)
                    use.pins.append(pin)
                elif ck == SyntaxKind.WildcardPortConnection:
                    wildcard = True
                elif ck == SyntaxKind.OrderedPortConnection:
                    use.ordered = True
                    if record_uses:
                        self._use(c, owner)
            if wildcard and child is not None:
                use.uses_autoinst = True
                for p in child.ports:
                    if p.name not in explicit:
                        use.pins.append(Pin(p.name, p.name, [p.name], use.conn_open, use.conn_open, implicit=True))
                        if record_uses:
                            self.uses.setdefault(p.name, []).append((use.conn_open, owner.id))
            ref = next((r for r in self.module.refs if r.inst_name == name and r.kind == "inst"), None)
            if ref is not None and ref.marker_range is not None:
                use.uses_autoinst = True
            if child is not None:
                for pin in use.pins:
                    info = child.port(pin.port)
                    if info is not None:
                        pin.direction = info.direction
            if name:
                self.insts.setdefault(name, use)
                if child is not None and child.kind == "interface":
                    self.decls.setdefault(name, Decl(name, "iface_inst", owner.id, s, e, s, e, type_text=module,
                                                     n_declarators=len(insts), in_fence=self.in_fence(s)))

    # -- queries ---------------------------------------------------------------

    def users(self, name: str) -> set[int]:
        return {o for _off, o in self.uses.get(name, [])}

    def inst_owners(self, names: Iterable[str]) -> set[int]:
        return {self.insts[n].owner for n in names if n in self.insts}

    def used_outside(self, name: str, owners: set[int]) -> bool:
        """True when *name* is referenced by an owner not in *owners*
        (declarations of *name* itself do not count, other declarations do,
        e.g. a dimension ``[N-1:0]``)."""
        for _off, o in self.uses.get(name, []):
            if o not in owners:
                own = self.owners[o]
                if own.kind == "decl" and own.name == name:
                    continue
                return True
        return False

    def is_port(self, name: str) -> bool:
        d = self.decls.get(name)
        return d is not None and d.kind == "port"

    def names_of(self, inst: InstUse) -> list[str]:
        seen: list[str] = []
        for p in inst.pins:
            for n in p.names:
                if n not in seen:
                    seen.append(n)
        for ns in inst.param_names.values():
            for n in ns:
                if n not in seen:
                    seen.append(n)
        return seen


def module_index(design: Design, name: str) -> ModuleIndex:
    m = design.modules.get(name)
    if m is None:
        raise ConnectError(f"no module {name}")
    return ModuleIndex(design, m)
