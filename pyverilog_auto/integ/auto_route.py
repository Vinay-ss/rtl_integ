"""In-source route annotations: ``//auto_route PORT :: to|from :: TARGETS``.

The annotation sits in the module that declares *PORT* (usually next to
the port declaration) and applies to **every instance** of that module::

    output axi data_ch;
    //auto_route data_ch :: to :: instE, instF
    //auto_route ctrl_ch :: from :: instE

* ``to``   : this module's port is the source; each target gets a port of
  the same name (or ``target:port`` to name it differently).
* ``from`` : the target's port drives this module's port.

Targets are instance names or dotted path suffixes (``instE``,
``coreB.instE``), full paths, or regex patterns.  Any token containing a
regex metacharacter (``top.*instE``, ``top\\.u_mem\\.u_ctrl[01]``) is a
regular expression full-matched against the whole path from the top, the
same as ``re:REGEX``.  A pattern that matches several instances resolves to
the nearest ones (deepest common ancestor with the annotated instance);
ties fan out.

Keywords resolve relative to each annotated instance: ``$top`` (alias
``$root``) is the root of its tree, ``$parent`` its immediate parent.
Routing to an ancestor exposes the port on that module's boundary.

The other-end port name (``target:port``) may contain placeholders so that
several instances of the annotated module get distinct boundary ports:
``{inst}`` (annotated instance name), ``{parent}`` (its parent's name),
``{path}`` (its path below the root, dots replaced by ``_``) and ``{n}``
(its index among the module's instances in the same tree)::

    //auto_route m_axi :: to :: $top:m_axi_{n}      -> top.m_axi_0, top.m_axi_1

The collected routes are ordinary :class:`RouteSpec` objects with exact
(escaped) paths, so they can be written to ``routes.toml`` for review and
applied with the regular routing flow.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .model import Instance, ModuleDef
from .route import Diagnostic, RouteError, RouteSpec

if TYPE_CHECKING:
    from .design import Design

# targets run to the end of the line; a '*' is allowed (regex wildcards) unless it closes a block comment ('*/')
_AUTO_ROUTE_RE = re.compile(
    r"(?://|/\*)\s*auto_route\s+(?P<port>[A-Za-z_]\w*)\s*::\s*(?P<dir>to|from)\s*::\s*(?P<targets>(?:[^\n*]|\*(?!/))+)",
    re.I,
)


@dataclass
class AutoRouteComment:
    module: str
    port: str
    direction: str                 # to | from
    targets: list[str]
    file: str                      # display path
    line: int
    text: str


@dataclass
class CollectedRoute:
    spec: RouteSpec
    comment: AutoRouteComment
    origin: Instance
    target: Instance


@dataclass
class CollectResult:
    routes: list[CollectedRoute] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)

    @property
    def specs(self) -> list[RouteSpec]:
        return [r.spec for r in self.routes]


# ----------------------------------------------------------------------
# Scanning
# ----------------------------------------------------------------------

def scan_auto_routes(design: "Design") -> list[AutoRouteComment]:
    """Find every ``auto_route`` annotation in the design's modules."""
    out: list[AutoRouteComment] = []
    for mod in design.modules.values():
        sf = design.files[mod.file]
        region = sf.text[mod.keyword_range.start:mod.end_range.end]
        for m in _AUTO_ROUTE_RE.finditer(region):
            targets = [t.strip() for t in m.group("targets").split(",")]
            targets = [t for t in targets if t]
            line, _ = sf.line_col(mod.keyword_range.start + m.start())
            out.append(AutoRouteComment(
                module=mod.name, port=m.group("port"), direction=m.group("dir").lower(),
                targets=targets, file=sf.path, line=line, text=m.group(0).strip(),
            ))
    out.sort(key=lambda c: (c.file, c.line))
    return out


# ----------------------------------------------------------------------
# Resolution
# ----------------------------------------------------------------------

_META_CHARS = set("*+?[](){}|^$\\")


# A trailing ':port' is anything after the last colon that contains no regex punctuation, so
# '(?:a|b).*instE' keeps its group and 'top:err-{n}' still reports its bad port name.
_TRAILING_PORT_RE = re.compile(r"^(.*?)(?::([^:()\[\]|*+?\\]*))?$")


def _split_target(token: str) -> tuple[str, Optional[str]]:
    """``target[:port]`` -> (target, port).

    The port is a trailing ``:name`` where *name* is an identifier that may carry
    ``{inst}``/``{parent}``/``{path}``/``{n}`` placeholders.  Taking the LAST colon
    keeps ``(?:...)`` groups inside regex targets intact; a ``re:`` prefix is kept.
    """
    token = token.strip()
    prefix = ""
    if token.startswith("re:"):
        prefix, token = "re:", token[3:]
    m = _TRAILING_PORT_RE.match(token)
    assert m is not None
    return prefix + m.group(1).strip(), m.group(2)


def _matches(design: "Design", token: str) -> list[Instance]:
    """Instances addressed by *token*; raises ``ValueError`` for an invalid pattern."""
    insts = design.all_instances()
    if token.startswith("re:"):
        try:
            rx = re.compile(token[3:])
        except re.error as exc:
            raise ValueError(f"invalid regex {token[3:]!r}: {exc}") from None
        return [i for i in insts if rx.fullmatch(i.path)]
    if any(ch in _META_CHARS for ch in token):
        # regex shorthand: full-matched against the whole path from the top (like 're:'),
        # so write 'top.*instE' or 'top\.u_mem\.u_ctrl[01]'
        try:
            rx = re.compile(token)
        except re.error as exc:
            raise ValueError(f"invalid pattern {token!r}: {exc}") from None
        return [i for i in insts if rx.fullmatch(i.path)]
    parts = token.split(".")
    out: list[Instance] = []
    for inst in insts:
        comps = inst.path.split(".")
        if len(comps) >= len(parts) and comps[-len(parts):] == parts:
            out.append(inst)
    return out


def _lca_depth(design: "Design", a: Instance, b: Instance) -> int:
    lca = design.lca(a, b)
    return -1 if lca is None else lca.depth


_KEYWORDS = {"$top", "$root", "$parent"}
_PLACEHOLDER_RE = re.compile(r"\{(inst|parent|path|n)\}")


def _root_of(inst: Instance) -> Instance:
    while inst.parent is not None:
        inst = inst.parent
    return inst


def _keyword_target(origin: Instance, name: str) -> Optional[Instance]:
    if name in ("$top", "$root"):
        return _root_of(origin)
    if name == "$parent":
        return origin.parent
    return None


def _expand_port(template: str, origin: Instance, origins_same_tree: list[Instance]) -> str:
    """Expand ``{inst}``/``{parent}``/``{path}``/``{n}`` in an other-end port name."""
    if "{" not in template:
        return template
    root = _root_of(origin)
    below = origin.path[len(root.path) + 1:] if origin.path.startswith(root.path + ".") else origin.path
    values = {
        "inst": re.sub(r"\W+", "_", origin.name),
        "parent": re.sub(r"\W+", "_", origin.parent.name) if origin.parent else "",
        "path": re.sub(r"\W+", "_", below),
        "n": str(origins_same_tree.index(origin)),
    }
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def resolve_auto_routes(design: "Design", comments: list[AutoRouteComment]) -> CollectResult:
    """Turn annotations into exact-path :class:`RouteSpec` objects."""
    res = CollectResult()
    for c in comments:
        origins = design.instances_of(c.module)
        if not origins:
            res.warnings.append(Diagnostic("W_AUTOROUTE_UNUSED", "warning",
                                           f"{c.text}: module {c.module} is never instantiated", c.file, c.line))
            continue
        # instances of the module grouped per tree, sorted by path (for {n})
        by_root: dict[str, list[Instance]] = {}
        for o in sorted(origins, key=lambda i: i.path):
            by_root.setdefault(_root_of(o).path, []).append(o)
        for token in c.targets:
            name, port_override = _split_target(token)
            is_keyword = name in _KEYWORDS
            try:
                candidates = [] if is_keyword else _matches(design, name)
            except ValueError as exc:
                res.errors.append(Diagnostic("E_AUTOROUTE_TARGET", "error", f"{c.text}: {exc}", c.file, c.line))
                continue
            if not candidates and not is_keyword:
                res.errors.append(Diagnostic("E_AUTOROUTE_TARGET", "error",
                                             f"{c.text}: no instance matches target {name!r}", c.file, c.line))
                continue
            for origin in origins:
                if is_keyword:
                    kt = _keyword_target(origin, name)
                    if kt is None or kt is origin:
                        res.warnings.append(Diagnostic("W_AUTOROUTE_UNREACHABLE", "warning",
                                                       f"{c.text}: {name} does not exist for {origin.path}", c.file, c.line))
                        continue
                    nearest = [kt]
                else:
                    reachable = [(i, _lca_depth(design, origin, i)) for i in candidates if i is not origin]
                    reachable = [(i, d) for i, d in reachable if d >= 0]
                    if not reachable:
                        res.warnings.append(Diagnostic("W_AUTOROUTE_UNREACHABLE", "warning",
                                                       f"{c.text}: no {name!r} reachable from {origin.path}", c.file, c.line))
                        continue
                    best = max(d for _, d in reachable)
                    nearest = [i for i, d in reachable if d == best]
                for target in nearest:
                    other_port = _expand_port(port_override or c.port, origin, by_root[_root_of(origin).path])
                    if not re.fullmatch(r"[A-Za-z_]\w*", other_port):
                        res.errors.append(Diagnostic("E_AUTOROUTE_PORT", "error",
                                                     f"{c.text}: other-end port name {other_port!r} is not an identifier",
                                                     c.file, c.line))
                        continue
                    net = None
                    if c.direction == "to":
                        src = f"{re.escape(origin.path)}:{c.port}"
                        dst = f"{re.escape(target.path)}:{other_port}"
                        label = f"{c.module}.{c.port}->{target.name}"
                        # a customized far-end name (e.g. err_{n}) also names the intermediate nets, so
                        # several instances of the annotated module do not collide inside shared parents
                        if other_port != c.port:
                            net = other_port
                    else:
                        src = f"{re.escape(target.path)}:{other_port}"
                        dst = f"{re.escape(origin.path)}:{c.port}"
                        label = f"{target.name}.{other_port}->{c.module}.{c.port}"
                    res.routes.append(CollectedRoute(RouteSpec(src=src, dst=dst, name=label, net=net), c, origin, target))
    # drop exact duplicates (same src/dst)
    seen: set[tuple[str, str]] = set()
    unique: list[CollectedRoute] = []
    for r in res.routes:
        k = (r.spec.src, r.spec.dst)
        if k in seen:
            continue
        seen.add(k)
        unique.append(r)
    res.routes = unique
    return res


def collect_auto_routes(design: "Design", *, strict: bool = True) -> CollectResult:
    """Scan and resolve; raise :class:`RouteError` on unresolved targets when *strict*."""
    res = resolve_auto_routes(design, scan_auto_routes(design))
    if strict and res.errors:
        raise RouteError(res.errors)
    return res


# ----------------------------------------------------------------------
# routes.toml writer
# ----------------------------------------------------------------------

def _toml_literal(s: str) -> str:
    if "'" not in s and "\n" not in s:
        return f"'{s}'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def routes_to_toml(specs: list[RouteSpec], origins: Optional[list[str]] = None, header: Optional[str] = None) -> str:
    lines: list[str] = []
    lines.append("# Generated by pyverilog-auto route --collect; edit freely, then apply with --routes.")
    if header:
        lines.append(f"# {header}")
    lines.append("")
    for i, s in enumerate(specs):
        if origins and i < len(origins) and origins[i]:
            lines.append(f"# {origins[i]}")
        lines.append("[[route]]")
        if s.name:
            lines.append(f"name = {_toml_literal(s.name)}")
        lines.append(f"src  = {_toml_literal(s.src)}")
        lines.append(f"dst  = {_toml_literal(s.dst)}")
        if s.net:
            lines.append(f"net = {_toml_literal(s.net)}")
        if s.dst_port:
            lines.append(f"dst_port = {_toml_literal(s.dst_port)}")
        if s.dst_modport:
            lines.append(f"dst_modport = {_toml_literal(s.dst_modport)}")
        if s.iface_conn:
            items = ", ".join(f"{k} = {_toml_literal(v)}" for k, v in s.iface_conn.items())
            lines.append(f"iface_conn = {{ {items} }}")
        if s.iface_params:
            items = ", ".join(f"{k} = {_toml_literal(v)}" for k, v in s.iface_params.items())
            lines.append(f"iface_params = {{ {items} }}")
        if s.modport_policy != "carry":
            lines.append(f"modport_policy = {_toml_literal(s.modport_policy)}")
        if not s.check_types:
            lines.append("check_types = false")
        if not s.comment:
            lines.append("comment = false")
        lines.append("")
    return "\n".join(lines)


def write_routes_file(path: str, result: CollectResult, *, base_dir: Optional[str] = None) -> str:
    """Write *result* as TOML to *path*; returns the text written."""
    origins = []
    for r in result.routes:
        f = r.comment.file
        if base_dir:
            try:
                f = os.path.relpath(f, base_dir)
            except ValueError:
                pass
        origins.append(f"{f}:{r.comment.line}  {r.comment.text}  [{r.origin.path} -> {r.target.path}]"
                       if r.comment.direction == "to" else
                       f"{f}:{r.comment.line}  {r.comment.text}  [{r.target.path} -> {r.origin.path}]")
    text = routes_to_toml(result.specs, origins)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text
