"""pyslang front-end: loading, CST scan, elaboration (instance tree), port facts.

Only this module and ``reader.py`` import pyslang.  Everything text-based
(markers, fences, explicit pins) is shared with the text backend through
``textscan``.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Optional

from .frontend_text import FrontendOptions, build_static_tree
from .model import (
    Diag, ElaboratedPort, Instance, ModuleDef, ModuleKind, ModuleRef, PortInfo, SrcRange, SymbolInfo,
)
from .reader import SlangModuleReader, kind_name, nodes
from .sources import SourceFile
from . import textscan as ts

if TYPE_CHECKING:
    from .design import Design
    from .filelist import Filelist

_DECL_KINDS = {"ModuleDeclaration", "InterfaceDeclaration", "ProgramDeclaration", "PackageDeclaration"}
_GENERATE_KINDS = {"GenerateRegion", "LoopGenerate", "IfGenerate", "CaseGenerate", "GenerateBlock"}
_INTEGER_TYPE_KINDS = {
    "LogicType", "RegType", "BitType", "ByteType", "ShortIntType", "IntType", "LongIntType",
    "IntegerType", "TimeType",
}


def _probe() -> None:
    """Import-time check of the handful of pyslang attributes we rely on."""
    import pyslang
    from pyslang import Bag, SourceManager  # noqa: F401
    from pyslang.ast import Compilation, CompilationFlags, CompilationOptions, SymbolKind  # noqa: F401
    from pyslang.driver import SourceLoader  # noqa: F401
    from pyslang.parsing import PreprocessorOptions  # noqa: F401
    from pyslang.syntax import SyntaxKind, SyntaxNode, SyntaxTree  # noqa: F401

    for attr in ("sourceRange", "getFirstToken", "getLastToken"):
        if not hasattr(SyntaxNode, attr):
            raise ImportError(f"pyslang.SyntaxNode lacks {attr}")
    if not hasattr(SyntaxTree, "fromFileInMemory"):
        raise ImportError("pyslang.SyntaxTree lacks fromFileInMemory")
    major = int(str(pyslang.__version__).split(".")[0])
    if major < 11:
        raise ImportError(f"pyslang >= 11 required, found {pyslang.__version__}")


class SlangFrontend:
    """Backend built on pyslang 11."""

    name = "slang"

    def __init__(self) -> None:
        _probe()
        import pyslang

        self.pyslang = pyslang
        self.sm = pyslang.SourceManager()
        self.bag = pyslang.Bag()
        self.diagnostics: list[Diag] = []
        self._comp = None
        self._reader: Optional[SlangModuleReader] = None
        self._masked: dict[tuple[str, int], str] = {}
        self._opts: Optional[FrontendOptions] = None
        self._binds: list[tuple[SourceFile, Any]] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _make_bag(self, opts: FrontendOptions):
        from pyslang import Bag
        from pyslang.parsing import PreprocessorOptions

        po = PreprocessorOptions()
        po.additionalIncludePaths = list(opts.include_dirs)
        po.predefines = [f"{k}={v}" if v else k for k, v in opts.defines.items()]
        return Bag([po])

    def load(self, fl: "Filelist", opts: FrontendOptions) -> dict[str, SourceFile]:
        from pyslang.driver import SourceLoader

        self._opts = opts
        self.bag = self._make_bag(opts)
        loader = SourceLoader(self.sm)
        for f in fl.sources:
            loader.addFiles(f)
        for f in fl.library_files:
            loader.addLibraryFiles("", f)
        for d in fl.library_dirs:
            loader.addSearchDirectories(d)
        for e in opts.libexts:
            loader.addSearchExtension(e)
        trees = loader.loadAndParseSources(self.bag)
        for err in loader.errors:
            self.diagnostics.append(Diag("error", f"source loader: {err}", None, None, "parse"))
        libfile_keys = {os.path.normcase(os.path.normpath(os.path.abspath(f))) for f in fl.library_files}
        files: dict[str, SourceFile] = {}
        for t in trees:
            name = self._tree_path(t)
            if not name:
                continue
            path = os.path.normpath(os.path.abspath(name))
            key = os.path.normcase(path)
            if key in files:
                continue
            role = "library" if (t.isLibraryUnit or key in libfile_keys) else "source"
            try:
                sf = SourceFile.read(path, role)
            except OSError as exc:
                self.diagnostics.append(Diag("error", f"cannot read {path}: {exc}", path, None, "filelist"))
                continue
            sf.tree = t
            files[key] = sf
        # Any listed source that slang did not return (should not happen) is loaded directly.
        for f in list(fl.sources) + list(fl.library_files):
            key = os.path.normcase(os.path.normpath(os.path.abspath(f)))
            if key not in files:
                self.load_extra(files, f, "library" if key in libfile_keys else "source")
        # Keep filelist order: sources first (in order), then libraries.
        ordered: dict[str, SourceFile] = {}
        for f in list(fl.sources) + list(fl.library_files):
            key = os.path.normcase(os.path.normpath(os.path.abspath(f)))
            if key in files:
                ordered[key] = files[key]
        for key, sf in files.items():
            ordered.setdefault(key, sf)
        return ordered

    def _tree_path(self, tree) -> str:
        tok = tree.root.getFirstToken()
        if tok is None:
            return ""
        try:
            return self.sm.getFileName(tok.location)
        except Exception:
            return ""

    def load_extra(self, files: dict[str, SourceFile], path: str, role: str) -> Optional[SourceFile]:
        from pyslang.syntax import SyntaxTree

        key = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        if key in files:
            return files[key]
        try:
            sf = SourceFile.read(path, role)  # type: ignore[arg-type]
        except OSError as exc:
            self.diagnostics.append(Diag("error", f"cannot read {path}: {exc}", path, None, "filelist"))
            return None
        sf.tree = self._parse_text(sf)
        files[key] = sf
        self._comp = None
        return sf

    def _parse_text(self, sf: SourceFile):
        """Parse the overlay text of *sf*.  The SourceManager keys buffers by
        their *path*, which may be registered only once, so re-parses use a
        versioned path while keeping the real file name for display and
        include resolution (the directory part is unchanged)."""
        from pyslang.syntax import SyntaxTree

        path = sf.path if sf.version == 0 else f"{sf.path}#v{sf.version}"
        return SyntaxTree.fromFileInMemory(sf.slang_text, self.sm, sf.path, path, self.bag)

    def refresh(self, sf: SourceFile) -> None:
        sf.tree = self._parse_text(sf)
        self._masked.pop((sf.key, sf.version - 1), None)
        self._comp = None

    def invalidate(self) -> None:
        self._comp = None

    def reader(self, design: "Design") -> SlangModuleReader:
        if self._reader is None:
            policy = self._opts.directive_policy if self._opts else "text"
            self._reader = SlangModuleReader(design, directive_policy=policy)
        return self._reader

    # ------------------------------------------------------------------
    # Definitions
    # ------------------------------------------------------------------

    def _masked_text(self, sf: SourceFile) -> str:
        key = (sf.key, sf.version)
        m = self._masked.get(key)
        if m is None:
            m = ts.mask_comments_and_strings(sf.text)
            self._masked = {key: m}   # keep only the latest per call site (bounded memory)
        return m

    def scan_definitions(self, sf: SourceFile, opts: FrontendOptions) -> list[ModuleDef]:
        self._opts = opts
        if sf.tree is None:
            sf.tree = self._parse_text(sf)
        for d in sf.tree.diagnostics:
            try:
                is_err = d.isError()
            except Exception:
                is_err = True
            if is_err:
                loc = d.location
                line = self.sm.getLineNumber(loc) if not self.sm.isMacroLoc(loc) else None
                self.diagnostics.append(Diag("warning", f"parse: {d.code}", sf.path, line, "parse"))
        defs: list[ModuleDef] = []
        binds = []
        for m in nodes(sf.tree.root.members):
            k = kind_name(m)
            if k in _DECL_KINDS:
                defs.append(self._build_module(sf, m, opts))
            elif k == "BindDirective":
                binds.append(m)
        for b in binds:
            self._attach_bind(sf, b, defs)
        sf.modules = [d.name for d in defs]
        return defs

    def _rng(self, sf: SourceFile, bstart: int, bend: int, **flags) -> SrcRange:
        return SrcRange.from_bytes(sf, bstart, bend, **flags)

    def _tok_range(self, sf: SourceFile, tok, **flags) -> SrcRange:
        off = tok.location.offset
        return self._rng(sf, off, off + len(tok.rawText), **flags)

    def _node_range(self, sf: SourceFile, node, **flags) -> SrcRange:
        r = node.sourceRange
        return self._rng(sf, r.start.offset, r.end.offset, **flags)

    def _build_module(self, sf: SourceFile, decl, opts: FrontendOptions) -> ModuleDef:
        text = sf.text
        masked = self._masked_text(sf)
        header = decl.header
        kw_tok = header.moduleKeyword
        name_tok = header.name
        kw_word = kw_tok.valueText
        kind: ModuleKind
        if kw_word in ("module", "macromodule"):
            kind = "module"
        elif kw_word == "connectmodule":
            kind = "connectmodule"
        else:
            kind = kw_word  # interface | program | package
        kw_start = sf.char_offset(kw_tok.location.offset)
        name_end = sf.char_offset(name_tok.location.offset + len(name_tok.rawText))
        semi = header.semi
        header_end = sf.char_offset(semi.location.offset + len(semi.rawText)) if semi is not None and not semi.isMissing else name_end
        end_tok = decl.endmodule
        end_start = sf.char_offset(end_tok.location.offset)
        end_end = sf.char_offset(end_tok.location.offset + len(end_tok.rawText))
        ports_node = header.ports
        port_list_range = None
        port_list_close = None
        ansi = False
        if ports_node is not None:
            pk = kind_name(ports_node)
            ansi = pk == "AnsiPortList"
            if pk in ("AnsiPortList", "NonAnsiPortList", "WildcardPortList"):
                o = ports_node.openParen.location.offset
                c = ports_node.closeParen.location.offset
                port_list_range = self._rng(sf, o, c + 1)
                port_list_close = sf.char_offset(c)
        markers = ts.find_markers(sf, text, masked, kw_start, end_end)
        fences = ts.fence_spans(markers)
        mod = ModuleDef(
            name=name_tok.valueText, kind=kind, file=sf.key,
            keyword_range=SrcRange.from_chars(sf, kw_start, name_end),
            header_range=SrcRange.from_chars(sf, kw_start, header_end),
            name_end=name_end, port_list_range=port_list_range, port_list_close=port_list_close,
            ansi=ansi, header_end=header_end, body_start=header_end,
            end_range=SrcRange.from_chars(sf, end_start, end_end),
            markers=markers, is_library=(sf.role == "library"), syntax=decl,
        )
        reader = SlangModuleReader(None, directive_policy=opts.directive_policy)
        reason = reader.needs_text_parser(decl, sf, sf.tree, self.sm)
        if reason:
            mod.needs_text_parser = True
            mod.text_parser_reason = reason
        tdre = re.compile(opts.typedef_regexp) if opts.typedef_regexp else None
        # Parameters
        if header.parameters is not None:
            for d in nodes(header.parameters.declarations):
                for a in nodes(getattr(d, "declarators", [])):
                    nm = a.name.valueText
                    if nm not in mod.params:
                        mod.params.append(nm)
                    mod.symbols.setdefault(nm, SymbolInfo(nm, "param", None, self._tok_range(sf, a.name)))
        # Ports
        prev: Optional[PortInfo] = None
        if ansi:
            for p in nodes(ports_node.ports):
                pi = self._ansi_port_info(sf, p, prev, tdre, fences)
                if pi is not None:
                    mod.ports.append(pi)
                    prev = pi
        name_entries: dict[str, SrcRange] = {}
        if ports_node is not None and kind_name(ports_node) == "NonAnsiPortList":
            for p in nodes(ports_node.ports):
                expr = getattr(p, "expr", None)
                if expr is not None and kind_name(expr) == "PortReference":
                    name_entries[expr.name.valueText] = self._tok_range(sf, expr.name)
        # Body walk
        self._walk_body(sf, decl.members, mod, fences, tdre, in_generate=False, masked=masked)
        # `module m (/*AUTOARG*/);` and `module m (); input a;` parse as an EMPTY ANSI list in slang,
        # but they are non-ANSI (AUTOARG regenerates the name list; ports live in the body).
        if mod.ansi and not any(p.ansi for p in mod.ports):
            autoarg_in_list = port_list_range is not None and any(
                port_list_range.contains(mi.range.start) for mi in markers.get("AUTOARG", []))
            if autoarg_in_list or any(not p.ansi for p in mod.ports):
                mod.ansi = False
        for p in mod.ports:
            if not p.ansi:
                p.list_entry_range = name_entries.get(p.name)
            mod.symbols.setdefault(p.name, SymbolInfo(p.name, "port", p.type_text, p.range, p.in_auto_fence))
            if p.is_interface and p.iface_type:
                mod.refs.append(ModuleRef(module=p.iface_type, kind="iface_port", inst_name=p.name,
                                          range=p.range or mod.header_range))
        for name, s, e in ts.auto_module_refs(text, kw_start, end_end):
            mod.refs.append(ModuleRef(module=name, kind="auto", inst_name=None, range=SrcRange.from_chars(sf, s, e)))
        return mod

    def _ansi_port_info(self, sf: SourceFile, p, prev: Optional[PortInfo], tdre, fences) -> Optional[PortInfo]:
        k = kind_name(p)
        rng = self._node_range(sf, p)
        rng = SrcRange.from_chars(sf, rng.start, rng.end, in_auto_fence=ts.in_spans(rng.start, fences))
        if k == "ExplicitAnsiPort":
            direction = p.direction.valueText if p.direction is not None and not p.direction.isMissing else (prev.direction if prev else "input")
            return PortInfo(name=p.name.valueText, direction=direction, range=rng, in_auto_fence=rng.in_auto_fence)
        if k != "ImplicitAnsiPort":
            return None
        header = p.header
        decl = p.declarator
        name = decl.name.valueText
        unpacked = self._dims(sf, decl.dimensions)
        hk = kind_name(header)
        if hk == "InterfacePortHeader":
            iface = header.nameOrKeyword.valueText
            modport = header.modport.member.valueText if header.modport is not None else None
            return PortInfo(name=name, direction=None, is_interface=True, iface_type=iface, modport=modport,
                            type_text=iface, unpacked_dims=unpacked, range=rng, in_auto_fence=rng.in_auto_fence)
        direction_tok = getattr(header, "direction", None)
        direction = (direction_tok.valueText or None) if direction_tok is not None and not direction_tok.isMissing else None
        net_type = header.netType.valueText if hk == "NetPortHeader" else None
        dt = getattr(header, "dataType", None)
        type_text, signed, packed, named = self._type_info(sf, dt, tdre, net_type)
        if named is not None and (direction is None or tdre is None):
            if direction is None:
                return PortInfo(name=name, direction=None, is_interface=True, iface_type=named, modport=None,
                                type_text=named, unpacked_dims=unpacked, range=rng, in_auto_fence=rng.in_auto_fence)
            type_text = named
        if direction is None:
            if prev is not None and not prev.is_interface:
                direction = prev.direction
                if kind_name(dt) == "ImplicitType" and not packed and net_type is None:
                    type_text, packed, signed = prev.type_text, prev.packed_dims, prev.signed if signed is None else signed
            else:
                direction = "input"
        return PortInfo(name=name, direction=direction, type_text=type_text, packed_dims=packed,
                        unpacked_dims=unpacked, signed=signed, range=rng, in_auto_fence=rng.in_auto_fence)

    def _type_info(self, sf: SourceFile, dt, tdre, net_type: Optional[str]):
        if dt is None:
            return (net_type, None, "", None)
        k = kind_name(dt)
        signing = getattr(dt, "signing", None)
        signed = signing.valueText if signing is not None and not signing.isMissing and signing.valueText else None
        packed = self._dims(sf, getattr(dt, "dimensions", None))
        if k in _INTEGER_TYPE_KINDS:
            kw = dt.keyword.valueText
            return (f"{net_type} {kw}" if net_type else kw, signed, packed, None)
        if k == "ImplicitType":
            return (net_type, signed, packed, None)
        if k == "NamedType":
            name = ts.strip_ws(self._text(sf, dt.name))
            if tdre is not None and tdre.search(name):
                return (f"{net_type} {name}" if net_type else name, signed, packed, None)
            return (net_type, signed, packed, name)
        kw = getattr(dt, "keyword", None)
        if kw is not None and not kw.isMissing:
            return (f"{net_type} {kw.valueText}" if net_type else kw.valueText, signed, packed, None)
        return (net_type, signed, packed, None)

    def _dims(self, sf: SourceFile, dims) -> str:
        if dims is None:
            return ""
        return "".join(ts.strip_ws(self._text(sf, d)) for d in nodes(dims))

    def _text(self, sf: SourceFile, node) -> str:
        ft = node.getFirstToken()
        lt = node.getLastToken()
        if ft is None or lt is None:
            return ""
        s = ft.location
        e = lt.location
        guard = 0
        while self.sm.isMacroLoc(s) and guard < 16:
            s = self.sm.getExpansionRange(s).start
            guard += 1
        if self.sm.isMacroLoc(e):
            guard = 0
            while self.sm.isMacroLoc(e) and guard < 16:
                e = self.sm.getExpansionRange(e).end
                guard += 1
            end_off = e.offset
        else:
            end_off = e.offset + len(lt.rawText)
        return sf.data[s.offset:end_off].decode("utf-8", "replace")

    def _walk_body(self, sf: SourceFile, members, mod: ModuleDef, fences, tdre, *, in_generate: bool, masked: str) -> None:
        for m in nodes(members):
            k = kind_name(m)
            if k == "HierarchyInstantiation":
                self._instantiation(sf, m, mod, fences, in_generate, masked)
            elif k == "PortDeclaration":
                self._body_port(sf, m, mod, fences, tdre)
            elif k in ("DataDeclaration", "NetDeclaration", "UserDefinedNetDeclaration"):
                type_text = ts.strip_ws(self._text(sf, m.type)) if k != "UserDefinedNetDeclaration" else None
                if k == "NetDeclaration":
                    type_text = f"{m.netType.valueText} {type_text}".strip() if type_text else m.netType.valueText
                symk = "net" if k != "DataDeclaration" else "var"
                for d in nodes(m.declarators):
                    nm = d.name.valueText
                    r = self._node_range(sf, m)
                    mod.symbols.setdefault(nm, SymbolInfo(nm, symk, type_text or None, r, ts.in_spans(r.start, fences)))
            elif k == "ParameterDeclarationStatement":
                p = m.parameter
                for d in nodes(getattr(p, "declarators", [])):
                    nm = d.name.valueText
                    if nm not in mod.params:
                        mod.params.append(nm)
                    mod.symbols.setdefault(nm, SymbolInfo(nm, "param", None, self._node_range(sf, m)))
            elif k == "TypedefDeclaration":
                nm = m.name.valueText
                mod.symbols.setdefault(nm, SymbolInfo(nm, "typedef", ts.strip_ws(self._text(sf, m.type)), self._node_range(sf, m)))
            elif k == "GenvarDeclaration":
                for ident in nodes(m.identifiers):
                    tok = ident.getFirstToken()
                    if tok is not None:
                        mod.symbols.setdefault(tok.valueText, SymbolInfo(tok.valueText, "genvar", None, self._node_range(sf, m)))
            elif k == "ModportDeclaration":
                for item in nodes(m.items):
                    mod.modports.append(item.name.valueText)
            elif k == "GenerateRegion":
                self._walk_body(sf, m.members, mod, fences, tdre, in_generate=in_generate, masked=masked)
            elif k in ("LoopGenerate", "IfGenerate"):
                self._walk_gen(sf, m.block, mod, fences, tdre, masked)
                if k == "IfGenerate" and m.elseClause is not None:
                    self._walk_gen(sf, m.elseClause.clause, mod, fences, tdre, masked)
            elif k == "CaseGenerate":
                for item in nodes(m.items):
                    clause = getattr(item, "clause", None)
                    if clause is not None:
                        self._walk_gen(sf, clause, mod, fences, tdre, masked)
            elif k == "GenerateBlock":
                self._walk_body(sf, m.members, mod, fences, tdre, in_generate=True, masked=masked)

    def _walk_gen(self, sf, block, mod, fences, tdre, masked) -> None:
        if block is None:
            return
        if kind_name(block) == "GenerateBlock":
            self._walk_body(sf, block.members, mod, fences, tdre, in_generate=True, masked=masked)
        else:
            self._walk_body(sf, [block], mod, fences, tdre, in_generate=True, masked=masked)

    def _body_port(self, sf: SourceFile, m, mod: ModuleDef, fences, tdre) -> None:
        header = m.header
        hk = kind_name(header)
        direction_tok = getattr(header, "direction", None)
        direction = ((direction_tok.valueText or None) if direction_tok is not None and not direction_tok.isMissing else None) or "input"
        net_type = header.netType.valueText if hk == "NetPortHeader" else None
        dt = getattr(header, "dataType", None)
        type_text, signed, packed, named = self._type_info(sf, dt, tdre, net_type)
        decl_r = self._node_range(sf, m)
        in_fence = ts.in_spans(decl_r.start, fences)
        decl_r = SrcRange.from_chars(sf, decl_r.start, decl_r.end, in_auto_fence=in_fence)
        for d in nodes(m.declarators):
            nm = d.name.valueText
            r = self._tok_range(sf, d.name, in_auto_fence=in_fence)
            mod.ports.append(PortInfo(name=nm, direction=direction, type_text=type_text, packed_dims=packed,
                                      unpacked_dims=self._dims(sf, d.dimensions), signed=signed, range=r,
                                      decl_range=decl_r, in_auto_fence=in_fence, ansi=False))

    def _instantiation(self, sf: SourceFile, m, mod: ModuleDef, fences, in_generate: bool, masked: str) -> None:
        text = sf.text
        type_name = m.type.valueText if m.type.valueText else m.type.rawText
        params_node = m.parameters
        param_range = self._node_range(sf, params_node) if params_node is not None else None
        overrides: dict[str, str] = {}
        if params_node is not None:
            for a in nodes(params_node.parameters):
                if kind_name(a) == "NamedParamAssignment" and a.expr is not None:
                    overrides[a.name.valueText] = ts.strip_ws(self._text(sf, a.expr))
        stmt_r = self._node_range(sf, m)
        for inst in nodes(m.instances):
            d = inst.decl
            inst_name = d.name.valueText if d is not None else None
            dims = self._dims(sf, d.dimensions) if d is not None else ""
            o = sf.char_offset(inst.openParen.location.offset)
            c = sf.char_offset(inst.closeParen.location.offset)
            fence = None
            for mi in mod.markers.get("AUTOINST", []):
                if mi.fence_range is not None and o < mi.range.start < c:
                    fence = (mi.fence_range.start, mi.fence_range.end)
                    break
            style, pins, marker_range, dotstar_range = ts.scan_connections(sf, text, masked, o, c, fence)
            ref = ModuleRef(
                module=type_name, kind="inst", inst_name=inst_name, range=stmt_r,
                conn_range=SrcRange.from_chars(sf, o, c + 1), param_range=param_range,
                array_dims=dims or None, is_array=bool(dims), in_generate=in_generate,
                connections_style=style, marker_range=marker_range, dotstar_range=dotstar_range,
                explicit_pins=pins, param_overrides=dict(overrides),
            )
            mod.refs.append(ref)
            if inst_name:
                mod.symbols.setdefault(inst_name, SymbolInfo(inst_name, "instance", type_name, stmt_r,
                                                             ts.in_spans(stmt_r.start, fences)))

    def _attach_bind(self, sf: SourceFile, b, defs: list[ModuleDef]) -> None:
        inst = b.instantiation
        if inst is None or kind_name(inst) != "HierarchyInstantiation":
            return
        target = ts.strip_ws(self._text(sf, b.target)) if b.target is not None else ""
        owner = next((d for d in defs if d.name == target), None) or (defs[0] if defs else None)
        if owner is None:
            return
        for hi in nodes(inst.instances):
            d = hi.decl
            owner.refs.append(ModuleRef(module=inst.type.valueText, kind="bind",
                                        inst_name=d.name.valueText if d is not None else None,
                                        range=self._node_range(sf, b)))

    # ------------------------------------------------------------------
    # Elaboration
    # ------------------------------------------------------------------

    def _compilation(self, design: "Design", tops: Optional[list[str]]):
        from pyslang import Bag
        from pyslang.ast import Compilation, CompilationFlags, CompilationOptions
        from pyslang.parsing import PreprocessorOptions

        opts = CompilationOptions()
        opts.flags = CompilationFlags.IgnoreUnknownModules | CompilationFlags.DisableInstanceCaching
        if tops:
            opts.topModules = set(tops)
        po = PreprocessorOptions()
        if self._opts is not None:
            po.additionalIncludePaths = list(self._opts.include_dirs)
            po.predefines = [f"{k}={v}" if v else k for k, v in self._opts.defines.items()]
        comp = Compilation(Bag([po, opts]))
        for sf in design.files.values():
            if sf.tree is not None:
                comp.addSyntaxTree(sf.tree)
        return comp

    def elaborate(self, design: "Design", tops: Optional[list[str]]) -> list[Instance]:
        from pyslang.ast import SymbolKind

        try:
            comp = self._compilation(design, tops)
            root = comp.getRoot()
            top_syms = list(root.topInstances)
        except Exception as exc:
            self.diagnostics.append(Diag("warning", f"pyslang elaboration failed ({exc}); using static tree", None, None, "elab"))
            return build_static_tree(design, tops, self.diagnostics)
        if tops is None or not tops:
            self._comp = comp
        for mod in design.modules.values():
            mod.instances = []
        roots: list[Instance] = []
        for sym in top_syms:
            roots.append(self._walk_instance(design, sym, None, SymbolKind))
        if tops:
            for t in tops:
                if t not in {r.module_name for r in roots}:
                    self.diagnostics.append(Diag("error", f"top module {t!r} not found or not elaborated", None, None, "elab"))
        return roots

    def _walk_instance(self, design: "Design", sym, parent: Optional[Instance], SymbolKind) -> Instance:
        defn = sym.definition
        mod_name = defn.name
        mod = design.modules.get(mod_name)
        path = sym.hierarchicalPath
        name, gen_path = self._split_path(path, parent)
        try:
            arr = tuple(int(x) for x in sym.arrayPath)
        except Exception:
            arr = ()
        node = Instance(
            path=path, name=name, module_name=mod_name, module=mod, parent=parent,
            file=mod.file if mod else None, is_interface=bool(sym.isInterface), is_blackbox=False,
            array_index=arr or None, gen_path=gen_path, depth=(parent.depth + 1) if parent else 0, symbol=sym,
        )
        if parent is not None and parent.module is not None:
            base = name.split("[")[0]
            node.ref = next((r for r in parent.module.refs if r.kind in ("inst", "bind") and r.inst_name == base), None)
        if mod is not None:
            mod.instances.append(node)
        self._scan_scope(design, sym.body, node, SymbolKind)
        return node

    def _scan_scope(self, design: "Design", scope, node: Instance, SymbolKind) -> None:
        for m in scope:
            k = m.kind
            if k == SymbolKind.Instance:
                node.children.append(self._walk_instance(design, m, node, SymbolKind))
            elif k == SymbolKind.InstanceArray:
                self._scan_array(design, m, node, SymbolKind)
            elif k == SymbolKind.GenerateBlock:
                if not m.isUninstantiated:
                    self._scan_scope(design, m, node, SymbolKind)
            elif k == SymbolKind.GenerateBlockArray:
                for entry in m.entries:
                    if not entry.isUninstantiated:
                        self._scan_scope(design, entry, node, SymbolKind)
            elif k == SymbolKind.UninstantiatedDef:
                path = m.hierarchicalPath
                name, gen_path = self._split_path(path, node)
                child = Instance(path=path, name=name, module_name=m.definitionName, module=None, parent=node,
                                 is_blackbox=True, gen_path=gen_path, depth=node.depth + 1, symbol=m)
                if node.module is not None:
                    base = name.split("[")[0]
                    child.ref = next((r for r in node.module.refs if r.kind == "inst" and r.inst_name == base), None)
                node.children.append(child)

    def _scan_array(self, design: "Design", arr, node: Instance, SymbolKind) -> None:
        for el in arr.elements:
            if el.kind == SymbolKind.Instance:
                node.children.append(self._walk_instance(design, el, node, SymbolKind))
            elif el.kind == SymbolKind.InstanceArray:
                self._scan_array(design, el, node, SymbolKind)

    @staticmethod
    def _split_path(path: str, parent: Optional[Instance]) -> tuple[str, tuple[str, ...]]:
        if parent is None:
            return path, ()
        prefix = parent.path + "."
        rest = path[len(prefix):] if path.startswith(prefix) else path.rsplit(".", 1)[-1]
        parts = rest.split(".")
        return parts[-1], tuple(parts[:-1])

    # ------------------------------------------------------------------
    # Port facts for routing
    # ------------------------------------------------------------------

    def port_at(self, design: "Design", inst: Instance, port: str) -> ElaboratedPort:
        from pyslang.ast import SymbolKind

        sym = inst.symbol
        if sym is None or inst.is_blackbox:
            raise LookupError(f"{inst.path} has no elaborated body")
        p = sym.body.findPort(port)
        if p is None:
            raise LookupError(f"{inst.path} ({inst.module_name}) has no port {port!r}")
        pinfo = inst.module.port(port) if inst.module else None
        sym_packed = pinfo.packed_dims if pinfo else ""
        sym_type = pinfo.type_text if pinfo else None
        unpacked = pinfo.unpacked_dims if pinfo else ""
        if p.kind == SymbolKind.InterfacePort:
            return ElaboratedPort(name=port, kind="iface", direction=None, type_text_numeric=p.interfaceDef.name,
                                  packed_dims_numeric="", unpacked_dims=unpacked, bit_width=None,
                                  iface_type=p.interfaceDef.name, modport=(p.modport or None),
                                  symbolic_packed_dims=sym_packed, symbolic_type_text=sym_type)
        direction_map = {"In": "input", "Out": "output", "InOut": "inout", "Ref": "ref"}
        dname = str(p.direction).split(".")[-1]
        direction = direction_map.get(dname, dname.lower())
        t = p.type
        canon = t.canonicalType
        is_struct = bool(canon.isStruct)
        qualified = None
        local = False
        if t.isAlias:
            qualified = t.hierarchicalPath
            ps = t.parentScope
            local = ps is not None and ps.containingInstance is not None
            if local:
                qualified = t.name
        packed_numeric = ""
        width = None
        try:
            if t.hasFixedRange:
                r = t.fixedRange
                packed_numeric = f"[{r.left}:{r.right}]" if hasattr(r, "left") else str(r)
            width = int(t.bitWidth)
        except Exception:
            pass
        param_dependent = False
        if pinfo and inst.module is not None and sym_packed:
            params = set(inst.module.params)
            idents = set(re.findall(r"[A-Za-z_]\w*", sym_packed))
            param_dependent = bool(idents & params)
        return ElaboratedPort(
            name=port, kind="struct" if is_struct else "signal", direction=direction,
            type_text_numeric=str(t), packed_dims_numeric=packed_numeric, unpacked_dims=unpacked,
            bit_width=width, is_struct=is_struct, struct_qualified_name=qualified, struct_is_local=local,
            param_dependent=param_dependent, symbolic_packed_dims=sym_packed, symbolic_type_text=sym_type,
        )
