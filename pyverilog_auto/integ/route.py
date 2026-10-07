"""Route specifications, endpoint grammar, backreference pairing (pure Python).

A route names both ends as regexes over hierarchical instance paths::

    src = r"top\\.u_cluster\\.u_core(\\d+)\\.u_dma:m_axi"
    dst = r"top\\.u_mem\\.u_ctrl\\1:s_axi"

* ``src`` = ``PATH_REGEX:port`` — the port must exist on the source module.
* ``dst`` = ``PATH_TEMPLATE[:port_template]`` — may use ``\\1`` / ``\\g<name>``
  backreferences into the source match; the expanded template is itself a
  regex matched against every instance path.  Without backreferences every
  source match connects to every destination match (fan-out).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Union

_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
_PORT_TPL_RE = re.compile(r"^(?:[A-Za-z_]\w*|\\\d+|\\g<\w+>)+$")
_SPEC_KEYS = {
    "src", "dst", "name", "net", "dst_port", "dst_modport", "iface_conn", "iface_params",
    "modport_policy", "check_types", "comment", "create_dst",
}
_TRUE_WORDS = {"true", "yes", "on", "1", "t"}
_FALSE_WORDS = {"false", "no", "off", "0", "nil"}


def _spec_bool(d: Mapping[str, object], key: str, default: bool, index: int) -> bool:
    """A boolean spec key: TOML/JSON bools, or true/false-like strings and 0/1."""
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in _TRUE_WORDS | _FALSE_WORDS:
        return v.strip().lower() in _TRUE_WORDS
    raise RouteError([Diagnostic("E_SPEC", "error", f"route #{index}: {key} must be true or false, not {v!r}")])


@dataclass
class Diagnostic:
    code: str
    severity: str                 # error | warning | info
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None
    route: Optional[str] = None

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}: " if self.file and self.line else (f"{self.file}: " if self.file else "")
        r = f" [{self.route}]" if self.route else ""
        return f"{loc}{self.severity} {self.code}: {self.message}{r}"


class RouteError(Exception):
    """One or more routing errors (nothing has been written)."""

    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__("\n".join(str(d) for d in self.diagnostics) or "routing failed")


@dataclass(frozen=True)
class RouteSpec:
    src: str
    dst: str
    name: Optional[str] = None
    net: Optional[str] = None
    dst_port: Optional[str] = None
    dst_modport: Optional[str] = None
    iface_conn: Mapping[str, str] = field(default_factory=dict)
    iface_params: Mapping[str, str] = field(default_factory=dict)
    modport_policy: str = "carry"        # carry | plain
    check_types: bool = True
    comment: bool = True
    # False: a missing dst port is an error (E_DST_PORT_MISSING) instead of
    # being created; a port inside an AUTO fence counts as existing
    # (wrapper-level //auto_route sets it)
    create_dst: bool = True

    # ------------------------------------------------------------------

    @classmethod
    def from_mapping(cls, d: Mapping[str, object], *, index: int = 0) -> "RouteSpec":
        unknown = sorted(set(d) - _SPEC_KEYS)
        if unknown:
            raise RouteError([Diagnostic("E_SPEC", "error", f"route #{index}: unknown key(s) {', '.join(unknown)}")])
        if "src" not in d or "dst" not in d:
            raise RouteError([Diagnostic("E_SPEC", "error", f"route #{index}: 'src' and 'dst' are required")])
        policy = str(d.get("modport_policy", "carry"))
        if policy not in ("carry", "plain"):
            raise RouteError([Diagnostic("E_SPEC", "error", f"route #{index}: modport_policy must be carry|plain")])
        return cls(
            src=str(d["src"]), dst=str(d["dst"]),
            name=(str(d["name"]) if d.get("name") is not None else None),
            net=(str(d["net"]) if d.get("net") is not None else None),
            dst_port=(str(d["dst_port"]) if d.get("dst_port") is not None else None),
            dst_modport=(str(d["dst_modport"]) if d.get("dst_modport") is not None else None),
            iface_conn=dict(d.get("iface_conn") or {}),
            iface_params=dict(d.get("iface_params") or {}),
            modport_policy=policy,
            check_types=bool(d.get("check_types", True)),
            comment=bool(d.get("comment", True)),
            create_dst=_spec_bool(d, "create_dst", True, index),
        )

    @classmethod
    def parse(cls, text: str) -> "RouteSpec":
        """``"SRC -> DST"``."""
        if " -> " not in text:
            raise RouteError([Diagnostic("E_SPEC", "error", f"route {text!r}: expected 'SRC -> DST'")])
        src, _, dst = text.partition(" -> ")
        return cls(src=src.strip(), dst=dst.strip())

    @classmethod
    def coerce(cls, x: Union["RouteSpec", Mapping[str, object], str], *, index: int = 0) -> "RouteSpec":
        if isinstance(x, RouteSpec):
            return x
        if isinstance(x, str):
            return cls.parse(x)
        return cls.from_mapping(x, index=index)

    # ------------------------------------------------------------------

    def label(self) -> str:
        return self.name or f"{self.src} -> {self.dst}"

    def src_parts(self) -> tuple[str, str]:
        path, port = split_endpoint(self.src)
        if not port:
            raise RouteError([Diagnostic("E_SPEC", "error", f"src {self.src!r} must be 'PATH_REGEX:port'", route=self.label())])
        if not _IDENT_RE.match(port):
            raise RouteError([Diagnostic("E_SPEC", "error", f"src port {port!r} is not an identifier", route=self.label())])
        return path, port

    def dst_parts(self) -> tuple[str, Optional[str]]:
        path, port = split_endpoint(self.dst)
        if self.dst_port:
            port = self.dst_port
        if port is not None and not _PORT_TPL_RE.match(port):
            raise RouteError([Diagnostic("E_SPEC", "error", f"dst port template {port!r} is invalid", route=self.label())])
        return path, port


# ----------------------------------------------------------------------
# Endpoint grammar
# ----------------------------------------------------------------------

def split_endpoint(text: str) -> tuple[str, Optional[str]]:
    """Split ``PATH:port`` on the last ``:`` at paren/bracket depth 0.

    ``(?:...)`` groups and ``[^:]`` classes keep their colons.
    """
    depth = 0
    last = -1
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ":" and depth == 0:
            last = i
        i += 1
    if last < 0:
        return text.strip(), None
    return text[:last].strip(), text[last + 1:].strip() or None


def expand_backrefs(template: str, m: "re.Match[str]", *, regex_escape: bool, route: Optional[str] = None) -> str:
    """Substitute ``\\N`` / ``\\g<name>`` in *template* with groups of *m*.

    Unlike :meth:`re.Match.expand`, other escapes (``\\.``, ``\\d`` ...) are
    kept verbatim so the result can still be a regex.  Group text is
    ``re.escape``-d when *regex_escape* is set.
    """
    out: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        c = template[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        nxt = template[i + 1]
        if nxt == "\\":
            out.append("\\\\")
            i += 2
            continue
        if nxt.isdigit():
            j = i + 1
            while j < n and template[j].isdigit():
                j += 1
            ref: Union[int, str] = int(template[i + 1:j])
            i = j
        elif nxt == "g" and i + 2 < n and template[i + 2] == "<":
            j = template.find(">", i + 3)
            if j < 0:
                raise RouteError([Diagnostic("E_BAD_BACKREF", "error", f"unterminated \\g< in {template!r}", route=route)])
            name = template[i + 3:j]
            ref = int(name) if name.isdigit() else name
            i = j + 1
        else:
            out.append(c + nxt)
            i += 2
            continue
        try:
            val = m.group(ref)
        except (IndexError, error_types()):
            raise RouteError([Diagnostic("E_BAD_BACKREF", "error",
                                         f"backreference \\{ref} in {template!r} has no matching group in {m.re.pattern!r}",
                                         route=route)]) from None
        if val is None:
            raise RouteError([Diagnostic("E_BACKREF_UNMATCHED", "error",
                                         f"backreference \\{ref} in {template!r} matched nothing", route=route)])
        out.append(re.escape(val) if regex_escape else val)
    return "".join(out)


def error_types():
    return re.error


# ----------------------------------------------------------------------
# Pairing
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Endpoint:
    path: str
    port: str

    def __str__(self) -> str:
        return f"{self.path}:{self.port}"


@dataclass
class RoutePair:
    spec: RouteSpec
    src: Endpoint
    dst: Endpoint
    src_match: "re.Match[str]"
    dst_pattern: str
    dst_match: "re.Match[str]"
    mode: str = "lca"             # lca | dst_is_ancestor | src_is_ancestor

    @property
    def label(self) -> str:
        return self.spec.label()


_FROM_TOP_NOTE = "(patterns are full-matched against the whole path from the top, e.g. 'top.*core.*instE')"


def pair_routes(spec: RouteSpec, paths: Iterable[str]) -> list[RoutePair]:
    """Match *spec* against instance *paths* (fullmatch) and pair the ends."""
    label = spec.label()
    src_path_re, src_port = spec.src_parts()
    dst_tpl, dst_port_tpl = spec.dst_parts()
    try:
        src_re = re.compile(src_path_re)
    except re.error as exc:
        raise RouteError([Diagnostic("E_BAD_SRC_REGEX", "error", f"bad src regex {src_path_re!r}: {exc}", route=label)])
    all_paths = list(paths)
    src_matches = [(p, m) for p in all_paths if (m := src_re.fullmatch(p))]
    if not src_matches:
        raise RouteError([Diagnostic("E_NO_SRC_MATCH", "error",
                                     f"no instance matches src {src_path_re!r} {_FROM_TOP_NOTE}",
                                     route=label)])
    pairs: list[RoutePair] = []
    errors: list[Diagnostic] = []
    for sp, sm in src_matches:
        dst_pat = expand_backrefs(dst_tpl, sm, regex_escape=True, route=label)
        try:
            dst_re = re.compile(dst_pat)
        except re.error as exc:
            errors.append(Diagnostic("E_BAD_DST_REGEX", "error", f"bad dst regex {dst_pat!r} (expanded from {dst_tpl!r}): {exc}", route=label))
            continue
        dms = [(p, m) for p in all_paths if (m := dst_re.fullmatch(p))]
        if not dms:
            errors.append(Diagnostic("E_NO_DST_MATCH", "error",
                                     f"src {sp!r}: no instance matches dst {dst_pat!r} (expanded from {dst_tpl!r}) "
                                     f"{_FROM_TOP_NOTE}", route=label))
            continue
        port_name = expand_backrefs(dst_port_tpl or src_port, sm, regex_escape=False, route=label)
        if not _IDENT_RE.match(port_name):
            errors.append(Diagnostic("E_BAD_PORT_NAME", "error", f"dst port {port_name!r} is not an identifier", route=label))
            continue
        for dp, dm in dms:
            if dp == sp:
                errors.append(Diagnostic("E_SAME_INSTANCE", "error", f"src and dst are the same instance {sp!r}", route=label))
                continue
            pairs.append(RoutePair(spec, Endpoint(sp, src_port), Endpoint(dp, port_name), sm, dst_pat, dm))
    if errors:
        raise RouteError(errors)
    return pairs


def is_identifier(s: str) -> bool:
    return bool(_IDENT_RE.match(s))
