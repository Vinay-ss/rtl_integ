"""In-source route annotations: ``//auto_route [INSTPATH:]PORT :: to|from :: TARGETS``.

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

Wrapper-level annotations name a child's port on the left side
(``LEFT := PORT | INSTPATH:PORT | re:REGEX:PORT``)::

    module core_b ...
    //auto_route instE:e_busy :: to :: instF:f_hold
    //auto_route u_sub.u_leaf:x :: from :: instF:y

An annotation in wrapper W with ``LEFT = instC:p`` behaves exactly like
``//auto_route p :: DIR :: TARGETS`` written in instC's module, restricted to
the instC instances below each instance of W: targets, keywords and
placeholders all resolve relative to that child.  A plain INSTPATH is a
dotted suffix of the path below W (``instC``, ``u_sub.u_leaf``); with ``re:``
or regex metacharacters it is full-matched against the whole path from the
top and intersected with W's subtree.  Several matches give several origins.
Wrapper-level routes get ``create_dst = false`` (a mistyped target port is an
error instead of a new port on a leaf), except when the far end is an
ancestor of the source (``$top:x``, ``$parent:x``), whose boundary port is
created as usual.  :meth:`Design.auto_route` resolves the same thing from
Python without any comment in the sources.

A comment that starts with ``auto_route`` and contains ``::`` but does not
parse is reported as ``E_AUTOROUTE_SYNTAX``.

The collected routes are ordinary :class:`RouteSpec` objects with exact
(escaped) paths, so they can be written to ``routes.toml`` for review and
applied with the regular routing flow.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, Union

from .model import Instance, ModuleDef
from .route import Diagnostic, RouteError, RouteSpec

if TYPE_CHECKING:
    from .design import Design

# targets run to the end of the line; a '*' is allowed (regex wildcards) unless it closes a block comment ('*/')
_AUTO_ROUTE_RE = re.compile(
    r"(?://|/\*)\s*auto_route\s+(?P<left>(?:[^\s*]|\*(?!/))+?)\s*::\s*(?P<dir>to|from)\s*::\s*"
    r"(?P<targets>(?:[^\n*]|\*(?!/))+)",
    re.I,
)
# a comment that starts with the keyword; it is an annotation attempt (and a syntax error when
# _AUTO_ROUTE_RE does not match) only if its text also contains '::', so prose is left alone
_AUTO_ROUTE_START_RE = re.compile(r"(?://|/\*)\s*auto_route\b(?P<body>(?:[^\n*]|\*(?!/))*)", re.I)
_SYNTAX_HINT = "expected 'auto_route [INSTPATH:|re:REGEX:]PORT :: to|from :: TARGETS'"
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_DOTTED_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


@dataclass
class AutoRouteComment:
    module: str
    port: str
    direction: str                 # to | from
    targets: list[str]
    file: str                      # display path
    line: int
    text: str
    # wrapper-level LEFT: the child instance path/pattern ('instC', 'u_sub.u_leaf', 're:top.*instE');
    # None for a leaf-style annotation on the module's own port
    src_inst: Optional[str] = None
    # None: mode default (True for leaf-style, False for wrapper-level); set by Design.auto_route
    create_dst: Optional[bool] = None
    # set when the annotation does not parse; resolving reports it as E_AUTOROUTE_SYNTAX
    error: Optional[str] = None


@dataclass
class CollectedRoute:
    spec: RouteSpec
    comment: AutoRouteComment
    origin: Instance
    target: Instance
    wrapper: Optional[Instance] = None     # the annotated (wrapper) instance for INSTPATH:PORT annotations


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

def _parse_left(left: str) -> tuple[Optional[str], str]:
    """``PORT`` | ``INSTPATH:PORT`` | ``re:REGEX:PORT`` -> (src_inst, port); ``ValueError`` if malformed.

    The port is the text after the LAST colon, so ``(?:...)`` groups inside a
    regex stay intact.  *src_inst* keeps a ``re:`` prefix; a plain INSTPATH
    must be a dotted identifier path (anything with regex metacharacters is a
    pattern).
    """
    left = left.strip()
    if _IDENT_RE.fullmatch(left):
        return None, left
    body = left[3:] if left.startswith("re:") else left
    inst, sep, port = body.rpartition(":")
    if not sep or not inst:
        raise ValueError(f"left side {left!r} must be PORT, INSTPATH:PORT or re:REGEX:PORT")
    if not _IDENT_RE.fullmatch(port):
        raise ValueError(f"port {port!r} in {left!r} is not an identifier")
    if left.startswith("re:"):
        return "re:" + inst, port
    if not _DOTTED_RE.fullmatch(inst) and not any(ch in _META_CHARS for ch in inst):
        raise ValueError(f"instance path {inst!r} in {left!r} is not a dotted path or a regex")
    return inst, port


def _split_targets(targets: Union[str, Sequence[str]]) -> list[str]:
    tokens = targets.split(",") if isinstance(targets, str) else list(targets)
    return [t.strip() for t in tokens if t and t.strip()]


def parse_auto_route(module: str, left: str, direction: str, targets: Union[str, Sequence[str]], *,
                     file: str, line: int, text: Optional[str] = None,
                     create_dst: Optional[bool] = None) -> AutoRouteComment:
    """Build an :class:`AutoRouteComment`; a malformed one carries ``error`` instead of raising."""
    tokens = _split_targets(targets)
    direction = direction.strip().lower()
    if text is None:
        text = f"auto_route {left.strip()} :: {direction} :: {', '.join(tokens)}"
    c = AutoRouteComment(module=module, port="", direction=direction, targets=tokens, file=file, line=line,
                         text=text, create_dst=create_dst)
    try:
        c.src_inst, c.port = _parse_left(left)
    except ValueError as exc:
        c.error = f"{exc}; {_SYNTAX_HINT}"
        return c
    if direction not in ("to", "from"):
        c.error = f"direction {direction!r} must be 'to' or 'from'; {_SYNTAX_HINT}"
    elif not tokens:
        c.error = f"no targets; {_SYNTAX_HINT}"
    return c


def scan_auto_routes(design: "Design") -> list[AutoRouteComment]:
    """Find every ``auto_route`` annotation in the design's modules.

    Comments that start with ``auto_route`` and contain ``::`` but do not
    parse are returned with ``error`` set (reported as ``E_AUTOROUTE_SYNTAX``).
    """
    out: list[AutoRouteComment] = []
    for mod in design.modules.values():
        sf = design.files[mod.file]
        region = sf.text[mod.keyword_range.start:mod.end_range.end]
        for s in _AUTO_ROUTE_START_RE.finditer(region):
            m = _AUTO_ROUTE_RE.match(region, s.start())
            if m is None and "::" not in s.group("body"):
                continue                   # prose that happens to start with 'auto_route'
            line, _ = sf.line_col(mod.keyword_range.start + s.start())
            if m is None:
                out.append(AutoRouteComment(module=mod.name, port="", direction="", targets=[], file=sf.path,
                                            line=line, text=s.group(0).strip(), error=_SYNTAX_HINT))
                continue
            out.append(parse_auto_route(mod.name, m.group("left"), m.group("dir"), m.group("targets"),
                                        file=sf.path, line=line, text=m.group(0).strip()))
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


def _rel_path(inst: Instance, ancestor: Instance) -> str:
    return inst.path[len(ancestor.path) + 1:]


def _left_origins(src_inst: str, hosts: list[Instance]) -> list[tuple[Instance, Instance]]:
    """``(origin, host)`` for the LEFT matches below each annotated instance (*host*).

    A plain dotted path is a suffix of the path below the host; ``re:`` or a
    pattern with metacharacters is full-matched against the whole path from the
    top (no implicit tail anchoring) and intersected with the host's subtree.
    Raises ``ValueError`` for an invalid pattern.
    """
    rx = None
    if src_inst.startswith("re:") or any(ch in _META_CHARS for ch in src_inst):
        pattern = src_inst[3:] if src_inst.startswith("re:") else src_inst
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid pattern {pattern!r}: {exc}") from None
    parts = src_inst.split(".")
    out: list[tuple[Instance, Instance]] = []
    for host in hosts:
        for inst in host.walk():
            if inst is host:
                continue
            if rx is not None:
                ok = rx.fullmatch(inst.path) is not None
            else:
                comps = _rel_path(inst, host).split(".")
                ok = len(comps) >= len(parts) and comps[-len(parts):] == parts
            if ok:
                out.append((inst, host))
    return out


def _is_ancestor(a: Instance, b: Instance) -> bool:
    return any(x is a for x in b.ancestors())


def resolve_auto_routes(design: "Design", comments: list[AutoRouteComment]) -> CollectResult:
    """Turn annotations into exact-path :class:`RouteSpec` objects."""
    res = CollectResult()
    for c in comments:
        if c.error:
            res.errors.append(Diagnostic("E_AUTOROUTE_SYNTAX", "error", f"{c.text}: {c.error}", c.file, c.line))
            continue
        hosts = design.instances_of(c.module)
        if not hosts:
            res.warnings.append(Diagnostic("W_AUTOROUTE_UNUSED", "warning",
                                           f"{c.text}: module {c.module} is never instantiated", c.file, c.line))
            continue
        # origins: the annotated instances themselves (leaf-style) or the LEFT matches below each of
        # them (wrapper-level); everything after this point is the same for both
        wrapper_of: dict[int, Instance] = {}
        if c.src_inst is None:
            origins = hosts
        else:
            try:
                pairs = _left_origins(c.src_inst, hosts)
            except ValueError as exc:
                res.errors.append(Diagnostic("E_AUTOROUTE_SRC", "error", f"{c.text}: {exc}", c.file, c.line))
                continue
            if not pairs:
                is_rx = c.src_inst.startswith("re:") or any(ch in _META_CHARS for ch in c.src_inst)
                hint = " (patterns are full-matched against the whole path from the top)" if is_rx else ""
                res.errors.append(Diagnostic("E_AUTOROUTE_SRC", "error",
                                             f"{c.text}: no instance below {c.module} matches {c.src_inst!r}{hint}",
                                             c.file, c.line))
                continue
            origins = [o for o, _ in pairs]
            wrapper_of = {id(o): h for o, h in pairs}
        # origins grouped per tree, sorted by path (for {n})
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
                    wrapper = wrapper_of.get(id(origin))
                    here = f"{_rel_path(origin, wrapper)}.{c.port}" if wrapper is not None else f"{c.module}.{c.port}"
                    if c.direction == "to":
                        src_i, dst_i = origin, target
                        src = f"{re.escape(origin.path)}:{c.port}"
                        dst = f"{re.escape(target.path)}:{other_port}"
                        label = f"{here}->{target.name}" + (f".{other_port}" if wrapper is not None else "")
                        # a customized far-end name (e.g. err_{n}) also names the intermediate nets, so
                        # several instances of the annotated module do not collide inside shared parents
                        if other_port != c.port:
                            net = other_port
                    else:
                        src_i, dst_i = target, origin
                        src = f"{re.escape(target.path)}:{other_port}"
                        dst = f"{re.escape(origin.path)}:{c.port}"
                        label = f"{target.name}.{other_port}->{here}"
                    if c.create_dst is not None:
                        create_dst = c.create_dst
                    else:
                        # wrapper-level: a missing port on the far end is an error, not a new port on a
                        # leaf; a boundary port on an ancestor ($top:x, $parent:x) is still created
                        create_dst = wrapper is None or _is_ancestor(dst_i, src_i)
                    spec = RouteSpec(src=src, dst=dst, name=label, net=net, create_dst=create_dst)
                    res.routes.append(CollectedRoute(spec, c, origin, target, wrapper))
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


def auto_route(design: "Design", module: str, left: str, direction: str, targets: Union[str, Sequence[str]], *,
               create_dst: Optional[bool] = None) -> CollectResult:
    """Resolve one annotation given as arguments (no comment needed); see :meth:`Design.auto_route`.

    Diagnostics carry the module's file and line.  Errors are returned in the
    result, not raised.
    """
    mod = design.modules[module]
    sf = design.files[mod.file]
    line, _ = sf.line_col(mod.keyword_range.start)
    c = parse_auto_route(module, left, direction, targets, file=sf.path, line=line, create_dst=create_dst)
    return resolve_auto_routes(design, [c])


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
        if not s.create_dst:
            lines.append("create_dst = false")
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
        to = r.comment.direction == "to"
        if r.wrapper is not None:
            # [W-path: left -> target] ('<-' for 'from')
            left = f"{_rel_path(r.origin, r.wrapper)}:{r.comment.port}"
            where = f"[{r.wrapper.path}: {left} {'->' if to else '<-'} {r.target.path}]"
        else:
            where = f"[{r.origin.path} -> {r.target.path}]" if to else f"[{r.target.path} -> {r.origin.path}]"
        origins.append(f"{f}:{r.comment.line}  {r.comment.text}  {where}")
    text = routes_to_toml(result.specs, origins)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text
