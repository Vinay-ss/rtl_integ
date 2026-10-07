"""Leaf-connectivity equivalence check.

A structural operation (wrap, hoist, unroll) must not change what the leaf
instances are connected to.  The check flattens the hierarchy into classes
of connected *leaf pins* (plus the top-level ports):

* every pin ``.p(expr)`` of a child instance joins the child's port node
  ``(child_path, p)`` with the parent's signal nodes ``(parent_path, name)``
  of every local name in ``expr`` (parameters are ignored);
* ``assign a = b;`` between two plain names joins ``a`` and ``b``;
* classes are compared after renaming the instance paths an operation moved
  (``top.u_a`` -> ``top.u_w.u_a``).

Expressions are joined per name, not per bit: ``{a, b}`` connects the pin to
both ``a`` and ``b``.  That is conservative but catches every rewiring an
operation could get wrong by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..integ.design import Design
from ..integ.model import Instance
from .connect import ConnectError, ModuleIndex

Node = tuple[str, str]
Partition = set[frozenset[Node]]

_NOT_SIGNALS = ("param", "localparam", "genvar", "typedef", "function", "enumval")


class _UF:
    def __init__(self) -> None:
        self.parent: dict[Node, Node] = {}

    def find(self, x: Node) -> Node:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Node, b: Node) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class VerifyResult:
    ok: bool
    missing: list[frozenset[Node]] = field(default_factory=list)   # classes lost
    extra: list[frozenset[Node]] = field(default_factory=list)     # classes gained
    notes: list[str] = field(default_factory=list)

    def describe(self, limit: int = 10) -> str:
        if self.ok:
            return "leaf connectivity unchanged"
        out = ["leaf connectivity changed:"]
        for c in self.missing[:limit]:
            out.append("  - before: " + ", ".join(f"{p}.{n}" for p, n in sorted(c)))
        for c in self.extra[:limit]:
            out.append("  + after:  " + ", ".join(f"{p}.{n}" for p, n in sorted(c)))
        return "\n".join(out + self.notes)


def leaf_partition(design: Design, notes: Optional[list[str]] = None) -> Partition:
    uf = _UF()
    members: set[Node] = set()
    cache: dict[str, Optional[ModuleIndex]] = {}

    def index(inst: Instance) -> Optional[ModuleIndex]:
        name = inst.module_name
        if name not in cache:
            try:
                cache[name] = ModuleIndex(design, inst.module) if inst.module is not None else None
            except ConnectError as exc:
                cache[name] = None
                if notes is not None:
                    notes.append(f"  ({exc})")
        return cache[name]

    for root in design.hierarchy():
        if root.module is not None:
            for p in root.module.ports:
                members.add((root.path, p.name))
                uf.find((root.path, p.name))
        for inst in root.walk():
            if inst.is_blackbox or inst.module is None or not inst.children:
                continue
            ix = index(inst)
            if ix is None:
                continue
            for lhs, rhs in ix.aliases:
                uf.union((inst.path, lhs), (inst.path, rhs))
            for child in inst.children:
                use = ix.insts.get(child.name)
                if use is None:
                    continue
                for pin in use.pins:
                    node = (child.path, pin.port)
                    uf.find(node)
                    for n in pin.names:
                        d = ix.decls.get(n)
                        if d is not None and d.kind in _NOT_SIGNALS:
                            continue
                        uf.union(node, (inst.path, n))
                if not child.children:
                    ports: Iterable[str]
                    if child.module is not None and not child.is_blackbox:
                        ports = [p.name for p in child.module.ports]
                    else:
                        ports = [p.port for p in use.pins]
                    for p in ports:
                        members.add((child.path, p))
    classes: dict[Node, set[Node]] = {}
    for m in members:
        classes.setdefault(uf.find(m), set()).add(m)
    return {frozenset(c) for c in classes.values() if len(c) >= 2}


def rename_path(path: str, renames: dict[str, str]) -> str:
    """Apply the longest matching prefix rename (``a.b`` -> ``a.w.b``)."""
    best = None
    for old in renames:
        if path == old or path.startswith(old + "."):
            if best is None or len(old) > len(best):
                best = old
    if best is None:
        return path
    return renames[best] + path[len(best):]


def compare(before: Partition, after: Partition, renames: Optional[dict[str, str]] = None) -> VerifyResult:
    renames = renames or {}
    mapped = {frozenset((rename_path(p, renames), n) for p, n in c) for c in before}
    missing = sorted(mapped - after, key=lambda c: sorted(c))
    extra = sorted(after - mapped, key=lambda c: sorted(c))
    return VerifyResult(ok=not missing and not extra, missing=missing, extra=extra)


_REPORTED_WARNINGS = ("PortWidthExpand", "PortWidthTruncate", "ImplicitNamedPortNotFound", "UnconnectedNamedPort")


def elab_messages(design: Design) -> list[str]:
    """Elaboration errors (and port-width warnings) of *design*, one line each."""
    try:
        from pyslang import Bag, DiagnosticEngine
        from pyslang.ast import Compilation, CompilationFlags, CompilationOptions
    except ImportError:  # pragma: no cover - text backend
        return []
    opts = CompilationOptions()
    opts.flags = CompilationFlags.IgnoreUnknownModules
    if design.tops_requested:
        opts.topModules = set(design.tops_requested)
    comp = Compilation(Bag([opts]))
    sm = None
    for sf in design.files.values():
        tree = getattr(sf, "tree", None)
        if tree is not None:
            comp.addSyntaxTree(tree)
            sm = sm or tree.sourceManager
    if sm is None:
        return []
    out: list[str] = []
    for d in comp.getAllDiagnostics():
        name = str(d.code).split("(")[-1].rstrip(")")
        if d.isError() or name in _REPORTED_WARNINGS:
            text = DiagnosticEngine.reportAll(sm, [d]).strip().splitlines()
            if text:
                out.append(text[0])
    return out


def new_messages(before: list[str], after: list[str]) -> list[str]:
    """Messages of *after* whose text (without the location) is not in *before*."""
    def strip(m: str) -> str:
        parts = m.split(": ", 1)
        return parts[1] if len(parts) == 2 else m
    seen = {strip(m) for m in before}
    return [m for m in after if strip(m) not in seen]


def verify_designs(before: Design, after: Design, renames: Optional[dict[str, str]] = None) -> VerifyResult:
    notes: list[str] = []
    res = compare(leaf_partition(before, notes), leaf_partition(after, notes), renames)
    res.notes.extend(notes)
    return res
