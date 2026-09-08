"""Text front-end: regex-based definition/instantiation scan (no pyslang).

This backend supports filelist parsing, hierarchy mapping and leaf-first
expansion.  It does not elaborate: generate blocks and instance arrays are
represented statically (one child per instantiation statement).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .model import Diag, Instance, ModuleDef, ModuleRef, PortInfo, SrcRange, SymbolInfo
from .sources import SourceFile
from . import textscan as ts

if TYPE_CHECKING:
    from .design import Design
    from .filelist import Filelist


@dataclass
class FrontendOptions:
    directive_policy: str = "text"
    strict: bool = False
    libexts: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    include_dirs: list[str] = field(default_factory=list)
    typedef_regexp: Optional[str] = None


class TextFrontend:
    """Backend that works without pyslang."""

    name = "text"

    def __init__(self) -> None:
        self.diagnostics: list[Diag] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, fl: "Filelist", opts: FrontendOptions) -> dict[str, SourceFile]:
        files: dict[str, SourceFile] = {}
        for path in fl.sources:
            self._load_one(files, path, "source")
        for path in fl.library_files:
            self._load_one(files, path, "library")
        return files

    def load_extra(self, files: dict[str, SourceFile], path: str, role: str) -> Optional[SourceFile]:
        return self._load_one(files, path, role)

    def _load_one(self, files: dict[str, SourceFile], path: str, role: str) -> Optional[SourceFile]:
        key = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        if key in files:
            return files[key]
        try:
            sf = SourceFile.read(path, role)  # type: ignore[arg-type]
        except OSError as exc:
            self.diagnostics.append(Diag("error", f"cannot read {path}: {exc}", path, None, "filelist"))
            return None
        files[key] = sf
        return sf

    def refresh(self, sf: SourceFile) -> None:
        """Nothing to re-parse; ``scan_definitions`` re-reads the text."""

    # ------------------------------------------------------------------
    # Definitions
    # ------------------------------------------------------------------

    def scan_definitions(self, sf: SourceFile, opts: FrontendOptions) -> list[ModuleDef]:
        text = sf.text
        masked = ts.mask_comments_and_strings(text)
        defs: list[ModuleDef] = []
        for region in ts.find_module_regions(text, masked):
            defs.append(self._build_module(sf, text, masked, region, opts))
        sf.modules = [d.name for d in defs]
        return defs

    def _build_module(self, sf: SourceFile, text: str, masked: str, r: ts.ModuleRegion,
                      opts: FrontendOptions) -> ModuleDef:
        body_start = r.header_end
        body_end = r.end_kw_start
        markers = ts.find_markers(sf, text, masked, r.kw_start, r.end_kw_end)
        fences = ts.fence_spans(markers)
        ansi = False
        ports: list[PortInfo] = []
        if r.port_list_open is not None and r.port_list_close is not None:
            inner = masked[r.port_list_open + 1:r.port_list_close]
            # ANSI if any entry carries a direction/type keyword; a bare name list is non-ANSI.
            if re.search(r"\b(input|output|inout|ref)\b", inner) or re.search(r"[A-Za-z_]\w*\s+[A-Za-z_]\w*", inner):
                ansi = True
                ports = ts.parse_ansi_port_list(sf, text, masked, r.port_list_open, r.port_list_close,
                                                opts.typedef_regexp)
            elif not inner.strip():
                # empty list: ANSI when AUTOINPUT/AUTOOUTPUT/AUTOINOUT sit inside the parens
                # (they expand to ANSI ports), non-ANSI when AUTOARG does or ports live in the body
                raw_inner = text[r.port_list_open + 1:r.port_list_close]
                if re.search(r"/\*\s*AUTO(?:INPUT|OUTPUT|INOUT)\b", raw_inner, re.I) and not re.search(
                        r"/\*\s*AUTOARG\b", raw_inner, re.I):
                    ansi = True
        if not ansi:
            ports = ts.parse_body_ports(sf, text, masked, body_start, body_end, fences)
            if r.port_list_open is not None and r.port_list_close is not None:
                names = ts.parse_nonansi_name_list(sf, masked, r.port_list_open, r.port_list_close)
                for p in ports:
                    p.list_entry_range = names.get(p.name)
        mod = ModuleDef(
            name=r.name,
            kind=r.kind,
            file=sf.key,
            keyword_range=SrcRange.from_chars(sf, r.kw_start, r.name_end),
            header_range=SrcRange.from_chars(sf, r.kw_start, r.header_end),
            name_end=r.name_end,
            port_list_range=(SrcRange.from_chars(sf, r.port_list_open, r.port_list_close + 1)
                             if r.port_list_open is not None and r.port_list_close is not None else None),
            port_list_close=r.port_list_close,
            ansi=ansi,
            header_end=r.header_end,
            body_start=body_start,
            end_range=SrcRange.from_chars(sf, r.end_kw_start, r.end_kw_end),
            ports=ports,
            markers=markers,
            is_library=(sf.role == "library"),
        )
        # Parameters (names only)
        if r.param_list_open is not None and r.param_list_close is not None:
            inner = masked[r.param_list_open:r.param_list_close]
            mod.params = re.findall(r"\b(?:parameter|localparam)?\s*(?:[A-Za-z_]\w*\s+)?([A-Za-z_]\w*)\s*=", inner)
        for m in re.finditer(r"\bparameter\b[^;=]*?\b([A-Za-z_]\w*)\s*=", masked[body_start:body_end]):
            if m.group(1) not in mod.params:
                mod.params.append(m.group(1))
        # Modports
        mod.modports = re.findall(r"\bmodport\s+([A-Za-z_]\w*)", masked[body_start:body_end])
        # Instantiations
        for im in ts.find_instantiations(masked, body_start, body_end):
            fence = None
            for mi in markers.get("AUTOINST", []):
                if mi.fence_range is not None and im.open_paren < mi.range.start < im.close_paren:
                    fence = (mi.fence_range.start, mi.fence_range.end)
                    break
            style, pins, marker_range, dotstar_range = ts.scan_connections(
                sf, text, masked, im.open_paren, im.close_paren, fence)
            ref = ModuleRef(
                module=im.type_name, kind="inst", inst_name=im.inst_name,
                range=SrcRange.from_chars(sf, im.start, im.stmt_end),
                conn_range=SrcRange.from_chars(sf, im.open_paren, im.close_paren + 1),
                param_range=(SrcRange.from_chars(sf, *im.params_span) if im.params_span else None),
                array_dims=im.dims, is_array=im.dims is not None,
                in_generate=_in_generate(masked, body_start, im.start),
                connections_style=style, marker_range=marker_range, dotstar_range=dotstar_range,
                explicit_pins=pins,
                param_overrides=ts.parse_param_overrides(masked, text, im.params_span),
            )
            mod.refs.append(ref)
            mod.symbols[im.inst_name] = SymbolInfo(im.inst_name, "instance", im.type_name, ref.range,
                                                   ts.in_spans(im.start, fences))
        # Interface ports create edges too
        for p in ports:
            if p.is_interface and p.iface_type:
                mod.refs.append(ModuleRef(module=p.iface_type, kind="iface_port", inst_name=p.name,
                                          range=p.range or mod.header_range))
        # AUTOINOUTMODULE("x") family
        for name, s, e in ts.auto_module_refs(text, r.kw_start, r.end_kw_end):
            mod.refs.append(ModuleRef(module=name, kind="auto", inst_name=None,
                                      range=SrcRange.from_chars(sf, s, e)))
        # Ports as symbols
        for p in ports:
            mod.symbols.setdefault(p.name, SymbolInfo(p.name, "port", p.type_text, p.range, p.in_auto_fence))
        # Nets / vars (cheap regex: keyword-led declarations outside fences)
        for m in re.finditer(r"^[ \t]*(wire|reg|logic|bit|integer|int)\b[^;=]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*[;=]",
                             masked[body_start:body_end], re.M):
            name = m.group(2)
            pos = body_start + m.start()
            if name not in mod.symbols:
                kind = "net" if m.group(1) == "wire" else "var"
                mod.symbols[name] = SymbolInfo(name, kind, m.group(1),
                                               SrcRange.from_chars(sf, pos, body_start + m.end()),
                                               ts.in_spans(pos, fences))
        return mod

    # ------------------------------------------------------------------
    # Static instance tree
    # ------------------------------------------------------------------

    def elaborate(self, design: "Design", tops: Optional[list[str]]) -> list[Instance]:
        return build_static_tree(design, tops, self.diagnostics)

    def reader(self, design: "Design"):
        return None


def _in_generate(masked: str, body_start: int, pos: int) -> bool:
    """True if *pos* lies inside a generate loop/if/case (rough: counts keywords before pos)."""
    region = masked[body_start:pos]
    opens = len(re.findall(r"\b(?:for|if|case)\s*\(", region)) + len(re.findall(r"\bgenerate\b", region))
    closes = len(re.findall(r"\bendgenerate\b", region)) + len(re.findall(r"\bend\b", region))
    return opens > closes


def build_static_tree(design: "Design", tops: Optional[list[str]], diags: list[Diag]) -> list[Instance]:
    """Instance tree from syntax only (used by the text backend and as a fallback)."""
    modules = design.modules
    referenced: set[str] = set()
    for mod in modules.values():
        for ref in mod.refs:
            if ref.kind in ("inst", "bind") and ref.module != mod.name:
                referenced.add(ref.module)
    if tops:
        root_names = [t for t in tops if t in modules]
        for t in tops:
            if t not in modules:
                diags.append(Diag("error", f"top module {t!r} not found", None, None, "design"))
    else:
        root_names = [n for n, m in modules.items()
                      if n not in referenced and m.kind in ("module", "program", "connectmodule")]
    for mod in modules.values():
        mod.instances = []
    roots: list[Instance] = []
    for name in root_names:
        mod = modules[name]
        root = Instance(path=name, name=name, module_name=name, module=mod, parent=None,
                        file=mod.file, is_interface=(mod.kind == "interface"), depth=0)
        mod.instances.append(root)
        _expand_children(root, modules, [name], diags)
        roots.append(root)
    return roots


def _expand_children(node: Instance, modules: dict[str, ModuleDef], stack: list[str], diags: list[Diag]) -> None:
    mod = node.module
    if mod is None:
        return
    for ref in mod.refs:
        if ref.kind not in ("inst", "bind") or ref.inst_name is None:
            continue
        child_mod = modules.get(ref.module)
        suffix = ref.inst_name + (ref.array_dims or "")
        child = Instance(
            path=f"{node.path}.{suffix}", name=suffix, module_name=ref.module, module=child_mod,
            parent=node, file=child_mod.file if child_mod else None,
            is_interface=bool(child_mod and child_mod.kind == "interface"),
            is_blackbox=child_mod is None, ref=ref, depth=node.depth + 1,
        )
        node.children.append(child)
        if child_mod is None:
            continue
        child_mod.instances.append(child)
        if ref.module in stack:
            diags.append(Diag("warning", f"recursive instantiation of {ref.module} at {child.path}",
                              None, None, "design"))
            continue
        if len(stack) > 200:
            diags.append(Diag("warning", f"hierarchy deeper than 200 at {child.path}; stopping", None, None, "design"))
            continue
        stack.append(ref.module)
        _expand_children(child, modules, stack, diags)
        stack.pop()
