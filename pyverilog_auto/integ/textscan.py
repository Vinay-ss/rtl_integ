"""Text-level scanning helpers shared by both front-ends.

AUTO markers live inside comments, so even the pyslang backend needs regex
scans for: markers and their generated fences, explicit pins relative to an
``/*AUTOINST*/`` marker, and ``AUTOINOUTMODULE("x")``-style references.
The text backend additionally uses these helpers to find module regions,
instantiations and ports without any parser library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..parser.decl_parser import VERILOG_KEYWORDS, _TYPE_KEYWORDS
from ..parser.sub_decls import _GATE_KEYWORDS
from .model import AUTO_MODULE_REF_MARKERS, ConnStyle, MarkerInfo, ModuleKind, PinInfo, PortInfo, SrcRange
from .sources import SourceFile

# Markers that generate a "// Beginning of automatic ... // End of automatics" fence.
LINED_MARKERS = {
    "AUTOINPUT", "AUTOOUTPUT", "AUTOINOUT", "AUTOWIRE", "AUTOLOGIC", "AUTOREG", "AUTOREGINPUT",
    "AUTOTIEOFF", "AUTOUNUSED", "AUTORESET", "AUTOUNDEF", "AUTOASCIIENUM", "AUTOINOUTMODULE",
    "AUTOINOUTCOMP", "AUTOINOUTIN", "AUTOINOUTPARAM", "AUTOINOUTMODPORT", "AUTOASSIGNMODPORT",
    "AUTOOUTPUTEVERY", "AUTOINSERTLISP", "AUTOINSERTLAST",
}
# Markers whose generated text runs from the marker to the enclosing close paren.
PAREN_MARKERS = {"AUTOARG", "AUTOINST", "AUTOINSTPARAM", "AUTOSENSE", "AS", "AUTOCONCATWIDTH"}

_MARKER_RE = re.compile(r"/\*\s*(AUTO[A-Za-z0-9_]+|AS)\b(.*?)\*/", re.S)
_FENCE_BEGIN_RE = re.compile(r"^[ \t]*// Beginning of automatic", re.M)
_FENCE_END_RE = re.compile(r"^[ \t]*// End of automatics[^\n]*", re.M)
_IMPLICIT_STAR_RE = re.compile(r"^[^\n]*// Implicit \.\*[^\n]*$", re.M)
_DOTSTAR_RE = re.compile(r"\.\*")

_DEFUN_RE = re.compile(
    r"\b(?P<kw>macromodule|connectmodule|module|interface|program|package|primitive)\b"
    r"[ \t\r\n\f]+(?P<name>[A-Za-z_][\w$]*|\\\S+)"
)
_END_DEFUN_RE = re.compile(
    r"\b(?:endconnectmodule|endmodule|endinterface|endprogram|endpackage|endprimitive)\b"
)
_IDENT = r"(?:[A-Za-z_][\w$]*|\\\S+|`[A-Za-z_]\w*)"
_INST_RE = re.compile(
    r"(?<![\w$.`\\])(?P<type>" + _IDENT + r")"
    r"[ \t]*(?P<params>#[ \t\r\n]*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?"
    r"[ \t\r\n]+(?P<inst>[A-Za-z_][\w$]*|\\\S+)"
    r"[ \t\r\n]*(?P<dims>(?:\[[^\]]*\][ \t]*)+)?"
    r"[ \t\r\n]*\(",
)
_STMT_START_WORDS = {"begin", "end", "generate", "endgenerate", "else"}
_NET_TYPES = {"wire", "tri", "tri0", "tri1", "triand", "trior", "trireg", "uwire", "wand", "wor",
              "supply0", "supply1"}
_PORT_ENTRY_RE = re.compile(
    r"^\s*(?:(?P<dir>input|output|inout|ref)\s+)?"
    r"(?:(?P<net>wire|tri|tri0|tri1|triand|trior|trireg|uwire|wand|wor|supply0|supply1)\s+)?"
    r"(?:(?P<var>var)\s+)?"
    r"(?:(?P<type>[A-Za-z_][\w$]*(?:::[A-Za-z_][\w$]*)*)(?:\.(?P<modport>[A-Za-z_]\w*))?\s+)?"
    r"(?:(?P<signed>signed|unsigned)\s+)?"
    r"(?P<packed>(?:\[[^\]]*\]\s*)*)"
    r"(?P<name>[A-Za-z_][\w$]*|\\\S+)\s*"
    r"(?P<unpacked>(?:\[[^\]]*\]\s*)*)"
    r"(?:=.*)?$",
    re.S,
)
_BODY_PORT_RE = re.compile(
    r"\b(?P<dir>input|output|inout)\b\s+"
    r"(?:(?P<net>wire|tri|tri0|tri1|triand|trior|trireg|uwire|wand|wor|supply0|supply1|reg|logic|bit|integer|int)\s+)?"
    r"(?:(?P<signed>signed|unsigned)\s+)?"
    r"(?P<packed>(?:\[[^\]]*\]\s*)*)"
    r"(?P<names>[^;]*);",
    re.S,
)
_WS_RE = re.compile(r"\s+")


# ----------------------------------------------------------------------
# Masking
# ----------------------------------------------------------------------

def mask_comments_and_strings(text: str) -> str:
    """Return *text* with comment and string contents replaced by spaces.

    Offsets and newlines are preserved so positions in the masked text map
    1:1 onto the original.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif ch == '"':
            j = i + 1
            while j < n and text[j] != '"' and text[j] != "\n":
                if text[j] == "\\":
                    j += 1
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def matching_close(masked: str, open_pos: int, limit: Optional[int] = None) -> Optional[int]:
    """Position of the ``)``/``]``/``}`` matching the bracket at *open_pos*."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = masked[open_pos]
    closer = pairs[opener]
    depth = 0
    end = len(masked) if limit is None else limit
    i = open_pos
    while i < end:
        c = masked[i]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def enclosing_open_paren(masked: str, pos: int, floor: int = 0) -> Optional[int]:
    """Position of the unmatched ``(`` that encloses *pos* (searching backward)."""
    depth = 0
    i = pos - 1
    while i >= floor:
        c = masked[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                return i
            depth -= 1
        i -= 1
    return None


def split_top_level(text: str, sep: str = ",") -> list[tuple[int, int]]:
    """Split *text* at top-level *sep* characters; return (start, end) spans."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for i, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(text)))
    return spans


def strip_ws(s: str) -> str:
    return _WS_RE.sub("", s)


# ----------------------------------------------------------------------
# Module regions (text backend)
# ----------------------------------------------------------------------

@dataclass
class ModuleRegion:
    kind: ModuleKind
    name: str
    kw_start: int        # char offset of the keyword
    name_end: int        # char offset after the name
    header_end: int      # char offset after the header ';'
    end_kw_start: int    # char offset of endmodule/endinterface/...
    end_kw_end: int
    port_list_open: Optional[int]   # char offset of "(" of the port list
    port_list_close: Optional[int]  # char offset of ")"
    param_list_open: Optional[int]  # char offset of "#("
    param_list_close: Optional[int]


def find_module_regions(text: str, masked: Optional[str] = None) -> list[ModuleRegion]:
    """Locate module/interface/program/package definitions in *text*."""
    masked = mask_comments_and_strings(text) if masked is None else masked
    regions: list[ModuleRegion] = []
    pos = 0
    n = len(masked)
    while pos < n:
        m = _DEFUN_RE.search(masked, pos)
        if not m:
            break
        kw = m.group("kw")
        name = m.group("name")
        kind: ModuleKind
        if kw in ("module", "macromodule"):
            kind = "module"
        elif kw == "connectmodule":
            kind = "connectmodule"
        else:
            kind = kw  # interface | program | package | primitive
        # Header: optional import list, optional #( ... ), optional ( ... ), then ';'
        i = m.end()
        param_open = param_close = None
        port_open = port_close = None
        while i < n and masked[i] in " \t\r\n\f":
            i += 1
        # import pkg::*; lines may precede the parameter list
        while masked.startswith("import", i):
            semi = masked.find(";", i)
            if semi < 0:
                break
            i = semi + 1
            while i < n and masked[i] in " \t\r\n\f":
                i += 1
        if i < n and masked[i] == "#":
            j = i + 1
            while j < n and masked[j] in " \t\r\n\f":
                j += 1
            if j < n and masked[j] == "(":
                close = matching_close(masked, j)
                if close is not None:
                    param_open, param_close = i, close
                    i = close + 1
        while i < n and masked[i] in " \t\r\n\f":
            i += 1
        if i < n and masked[i] == "(":
            close = matching_close(masked, i)
            if close is not None:
                port_open, port_close = i, close
                i = close + 1
        semi = masked.find(";", i)
        header_end = (semi + 1) if semi >= 0 else i
        em = _END_DEFUN_RE.search(masked, header_end)
        if em is None:
            end_s = end_e = n
            pos = n
        else:
            end_s, end_e = em.start(), em.end()
            pos = end_e
        regions.append(ModuleRegion(kind, name, m.start(), m.end(), header_end, end_s, end_e,
                                    port_open, port_close, param_open, param_close))
    return regions


# ----------------------------------------------------------------------
# Markers and fences
# ----------------------------------------------------------------------

def find_markers(sf: SourceFile, text: str, masked: str, start: int, end: int) -> dict[str, list[MarkerInfo]]:
    """Find AUTO markers in ``text[start:end]`` and compute their generated regions."""
    markers: dict[str, list[MarkerInfo]] = {}
    for m in _MARKER_RE.finditer(text, start, end):
        kind = m.group(1).upper()
        args = m.group(2).strip()
        if args.startswith("(") and args.endswith(")"):
            args = args[1:-1]
        fence: Optional[SrcRange] = None
        if kind in LINED_MARKERS or kind not in PAREN_MARKERS:
            line_end = text.find("\n", m.end())
            line_end = end if line_end < 0 or line_end > end else line_end
            bm = _FENCE_BEGIN_RE.match(text, line_end + 1) if line_end + 1 < end else None
            if bm is not None:
                em = _FENCE_END_RE.search(text, bm.end(), end)
                if em is not None:
                    fence = SrcRange.from_chars(sf, bm.start(), em.end())
        else:
            open_pos = enclosing_open_paren(masked, m.start(), start)
            if open_pos is not None:
                close = matching_close(masked, open_pos, end)
                if close is not None:
                    fence = SrcRange.from_chars(sf, m.end(), close)
        markers.setdefault(kind, []).append(
            MarkerInfo(kind=kind, range=SrcRange.from_chars(sf, m.start(), m.end()), fence_range=fence, args=args)
        )
    # .* implicit connections leave "// Implicit .*" lines; treat those as a fence.
    for m in _DOTSTAR_RE.finditer(masked, start, end):
        fence = None
        open_pos = enclosing_open_paren(masked, m.start(), start)
        if open_pos is not None:
            close = matching_close(masked, open_pos, end)
            if close is not None:
                lines = list(_IMPLICIT_STAR_RE.finditer(text, m.end(), close))
                if lines:
                    fence = SrcRange.from_chars(sf, lines[0].start(), lines[-1].end())
        markers.setdefault("DOTSTAR", []).append(
            MarkerInfo(kind="DOTSTAR", range=SrcRange.from_chars(sf, m.start(), m.end()), fence_range=fence)
        )
    return markers


def fence_spans(markers: dict[str, list[MarkerInfo]]) -> list[tuple[int, int]]:
    """All generated regions (char offsets) of a module, sorted."""
    spans = [(mi.fence_range.start, mi.fence_range.end)
             for lst in markers.values() for mi in lst if mi.fence_range is not None]
    spans.sort()
    return spans


def in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if s <= pos < e:
            return True
        if s > pos:
            break
    return False


def auto_module_refs(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """``(module_name, marker_start, marker_end)`` for AUTOINOUTMODULE-family markers."""
    out: list[tuple[str, int, int]] = []
    pat = re.compile(
        r"/\*\s*(" + "|".join(AUTO_MODULE_REF_MARKERS) + r")\s*\(\s*\"([^\"]+)\"", re.I,
    )
    for m in pat.finditer(text, start, end):
        out.append((m.group(2), m.start(), m.end()))
    return out


# ----------------------------------------------------------------------
# Instantiations (text backend) and connection lists (both backends)
# ----------------------------------------------------------------------

@dataclass
class InstMatch:
    type_name: str
    inst_name: str
    dims: Optional[str]
    start: int          # statement start (module name)
    params_span: Optional[tuple[int, int]]
    open_paren: int
    close_paren: int
    stmt_end: int       # after the ';'


def _statement_start_ok(masked: str, pos: int, floor: int) -> bool:
    """True if *pos* can begin a statement (after ';', ')', braces, a label
    colon, a block keyword, or at the region start)."""
    i = pos - 1
    while i >= floor and masked[i] in " \t\r\n\f":
        i -= 1
    if i < floor:
        return True
    c = masked[i]
    if c in ";){}:":
        return True
    j = i
    while j >= floor and (masked[j].isalnum() or masked[j] in "_$"):
        j -= 1
    word = masked[j + 1:i + 1]
    if word in _STMT_START_WORDS:
        return True
    # `begin : label` / `end : label` -> the label is preceded by ':'
    k = j
    while k >= floor and masked[k] in " \t\r\n\f":
        k -= 1
    return bool(word) and k >= floor and masked[k] == ":"


def find_instantiations(masked: str, start: int, end: int) -> list[InstMatch]:
    """Instantiation statements inside ``masked[start:end]``."""
    out: list[InstMatch] = []
    pos = start
    while pos < end:
        m = _INST_RE.search(masked, pos, end)
        if not m:
            break
        pos = m.start() + 1
        t = m.group("type")
        inst = m.group("inst")
        if t in VERILOG_KEYWORDS or t in _GATE_KEYWORDS or inst in VERILOG_KEYWORDS:
            continue
        if not _statement_start_ok(masked, m.start(), start):
            continue
        open_paren = m.end() - 1
        close = matching_close(masked, open_paren, end)
        if close is None:
            continue
        semi = masked.find(";", close)
        stmt_end = (semi + 1) if 0 <= semi < end else close + 1
        params_span = None
        if m.group("params"):
            ps = m.start("params")
            params_span = (ps, m.end("params"))
        dims = m.group("dims")
        out.append(InstMatch(t, inst, strip_ws(dims) if dims else None, m.start("type"),
                             params_span, open_paren, close, stmt_end))
        pos = stmt_end
    return out


def scan_connections(
    sf: SourceFile, text: str, masked: str, open_paren: int, close_paren: int,
    fence: Optional[tuple[int, int]],
) -> tuple[ConnStyle, list[PinInfo], Optional[SrcRange], Optional[SrcRange]]:
    """Analyse a connection list ``( ... )``.

    Returns (style, explicit pins outside *fence*, AUTOINST marker range,
    ``.*`` range).
    """
    inner_start, inner_end = open_paren + 1, close_paren
    marker_range: Optional[SrcRange] = None
    dotstar_range: Optional[SrcRange] = None
    for m in _MARKER_RE.finditer(text, inner_start, inner_end):
        if m.group(1).upper() == "AUTOINST":
            marker_range = SrcRange.from_chars(sf, m.start(), m.end())
            break
    sm = re.search(r"\.\*", masked[inner_start:inner_end])
    if sm:
        dotstar_range = SrcRange.from_chars(sf, inner_start + sm.start(), inner_start + sm.end())

    pins: list[PinInfo] = []
    has_ordered = False
    has_any = False
    inner = masked[inner_start:inner_end]
    for s, e in split_top_level(inner):
        seg = inner[s:e]
        stripped = seg.strip()
        if not stripped:
            continue
        seg_abs = inner_start + s + (len(seg) - len(seg.lstrip()))
        if fence is not None and fence[0] <= seg_abs < fence[1]:
            continue
        has_any = True
        if stripped == ".*":
            continue
        pm = re.match(r"\.\s*([A-Za-z_][\w$]*|\\\S+)\s*(?:\((.*)\))?\s*$", stripped, re.S)
        if pm:
            port = pm.group(1)
            expr_masked = pm.group(2)
            if expr_masked is None:
                expr = port
            else:
                # Take the expression text from the ORIGINAL text (masked has comments blanked).
                es = seg_abs + pm.start(2)
                ee = seg_abs + pm.end(2)
                expr = strip_ws(text[es:ee])
            pins.append(PinInfo(port=port, expr_text=expr, range=SrcRange.from_chars(sf, seg_abs, seg_abs + len(stripped))))
        else:
            has_ordered = True
    if dotstar_range is not None and not pins and not has_ordered:
        style: ConnStyle = "wildcard"
    elif has_ordered:
        style = "ordered"
    elif pins or has_any:
        style = "named"
    else:
        style = "empty"
    return style, pins, marker_range, dotstar_range


def parse_param_overrides(masked: str, text: str, span: Optional[tuple[int, int]]) -> dict[str, str]:
    """``#(.A(1), .B(x))`` -> {"A": "1", "B": "x"} (named only)."""
    if span is None:
        return {}
    s, e = span
    open_paren = masked.find("(", s, e)
    if open_paren < 0:
        return {}
    close = matching_close(masked, open_paren, e)
    if close is None:
        return {}
    inner = masked[open_paren + 1:close]
    out: dict[str, str] = {}
    for a, b in split_top_level(inner):
        seg = inner[a:b].strip()
        m = re.match(r"\.\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$", seg, re.S)
        if m:
            vs = open_paren + 1 + a + inner[a:b].find(m.group(2))
            out[m.group(1)] = strip_ws(text[vs:vs + len(m.group(2))])
    return out


# ----------------------------------------------------------------------
# Ports (text backend)
# ----------------------------------------------------------------------

def parse_ansi_port_list(sf: SourceFile, text: str, masked: str, open_paren: int, close_paren: int,
                         typedef_regexp: Optional[str] = None) -> list[PortInfo]:
    """Parse an ANSI header port list with regexes (text backend only)."""
    ports: list[PortInfo] = []
    inner_start = open_paren + 1
    inner = masked[inner_start:close_paren]
    prev: Optional[PortInfo] = None
    tdre = re.compile(typedef_regexp) if typedef_regexp else None
    for s, e in split_top_level(inner):
        seg = inner[s:e]
        if not seg.strip():
            continue
        m = _PORT_ENTRY_RE.match(seg)
        if not m:
            continue
        lead = len(seg) - len(seg.lstrip())
        abs_s = inner_start + s + lead
        abs_e = inner_start + s + len(seg.rstrip())
        rng = SrcRange.from_chars(sf, abs_s, abs_e)
        direction = m.group("dir")
        net = m.group("net")
        tname = m.group("type")
        modport = m.group("modport")
        packed = strip_ws(m.group("packed") or "")
        unpacked = strip_ws(m.group("unpacked") or "")
        signed = m.group("signed")
        name = m.group("name")
        if tname in _TYPE_KEYWORDS:
            type_text = f"{net} {tname}" if net else tname
            is_iface = False
        elif tname is None:
            type_text = net
            is_iface = False
        elif tdre is not None and tdre.search(tname):
            type_text = f"{net} {tname}" if net else tname
            is_iface = False
        elif direction is None and net is None:
            # User type without direction in an ANSI header: interface port
            # (same heuristic as DeclParser / verilog-mode).
            type_text = tname
            is_iface = True
        else:
            type_text = tname
            is_iface = False
        if direction is None and not is_iface and prev is not None and not prev.is_interface:
            # `input [7:0] a, b` -> b inherits direction/type of a
            direction = prev.direction
            if tname is None and not packed and not net:
                type_text = prev.type_text
                packed = prev.packed_dims
                signed = prev.signed
        p = PortInfo(name=name, direction=None if is_iface else direction, is_interface=is_iface,
                     iface_type=tname if is_iface else None, modport=modport if is_iface else None,
                     type_text=type_text, packed_dims=packed, unpacked_dims=unpacked, signed=signed,
                     range=rng, ansi=True)
        ports.append(p)
        prev = p
    return ports


def parse_body_ports(sf: SourceFile, text: str, masked: str, start: int, end: int,
                     fences: list[tuple[int, int]]) -> list[PortInfo]:
    """Non-ANSI ``input/output/inout ... name, name;`` declarations in a body."""
    ports: list[PortInfo] = []
    for m in _BODY_PORT_RE.finditer(masked, start, end):
        names_txt = m.group("names")
        packed = strip_ws(m.group("packed") or "")
        base = m.start("names")
        for s, e in split_top_level(names_txt):
            seg = names_txt[s:e]
            nm = re.match(r"\s*([A-Za-z_][\w$]*|\\\S+)\s*((?:\[[^\]]*\]\s*)*)", seg)
            if not nm:
                continue
            abs_s = base + s + nm.start(1)
            in_fence = in_spans(m.start(), fences)
            ports.append(PortInfo(
                name=nm.group(1), direction=m.group("dir"), type_text=m.group("net"),
                packed_dims=packed, unpacked_dims=strip_ws(nm.group(2) or ""), signed=m.group("signed"),
                range=SrcRange.from_chars(sf, abs_s, abs_s + len(nm.group(1)), in_auto_fence=in_fence),
                decl_range=SrcRange.from_chars(sf, m.start(), m.end(), in_auto_fence=in_fence),
                in_auto_fence=in_fence, ansi=False,
            ))
    return ports


def parse_nonansi_name_list(sf: SourceFile, masked: str, open_paren: int, close_paren: int) -> dict[str, SrcRange]:
    """Names in a non-ANSI header list ``(a, b, c)`` -> their ranges."""
    out: dict[str, SrcRange] = {}
    inner_start = open_paren + 1
    inner = masked[inner_start:close_paren]
    for s, e in split_top_level(inner):
        seg = inner[s:e]
        m = re.match(r"\s*([A-Za-z_][\w$]*|\\\S+)\s*$", seg)
        if m:
            a = inner_start + s + m.start(1)
            out[m.group(1)] = SrcRange.from_chars(sf, a, a + len(m.group(1)))
    return out
