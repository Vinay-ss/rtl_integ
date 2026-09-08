"""SlangModuleReader — ``ModDecls`` from pyslang syntax trees.

The reader is CST-driven (it never uses elaborated symbols): it walks a
``ModuleDeclarationSyntax`` exactly where ``DeclParser`` scans text, and
slices packed/unpacked dimensions from the original file bytes so that
symbolic widths (``[WIDTH-1:0]``, `` [`W-1:0] ``) survive verbatim.  The
field conventions mirror ``parser/decl_parser.py`` so AUTOINST output is
unchanged when the reader is swapped in.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Optional

from ..parser.decl_parser import DeclParser, _TYPE_KEYWORDS
from ..signal import ModDecls, Modi, Modport, Signal
from .sources import SourceFile

if TYPE_CHECKING:
    from ..config import VerilogConfig
    from .design import Design
    from .model import ModuleDef

_WS_RE = re.compile(r"\s+")
_DIRECTIVE_RE = re.compile(r"^[ \t]*`(ifdef|ifndef|elsif|else|endif|include)\b", re.M)
_INTEGER_TYPE_KINDS = {
    "LogicType", "RegType", "BitType", "ByteType", "ShortIntType", "IntType", "LongIntType",
    "IntegerType", "TimeType",
}
_KEYWORD_TYPE_KINDS = {
    "ShortRealType", "RealType", "RealTimeType", "StringType", "CHandleType", "EventType", "VoidType",
    "Untyped", "PropertyType", "SequenceType",
}
_GENERATE_KINDS = {"GenerateRegion", "LoopGenerate", "IfGenerate", "CaseGenerate", "GenerateBlock"}
_PROC_KINDS = {"AlwaysBlock", "AlwaysCombBlock", "AlwaysFFBlock", "AlwaysLatchBlock", "InitialBlock", "FinalBlock"}


def _tok_text(tok) -> Optional[str]:
    """valueText of a token, or None when the token is absent/missing/empty."""
    if tok is None or tok.isMissing:
        return None
    return tok.valueText or None


def _pyslang():
    import pyslang
    from pyslang.syntax import SyntaxKind, SyntaxNode, SyntaxTree

    return pyslang, SyntaxKind, SyntaxNode, SyntaxTree


def nodes(seq) -> Iterator[Any]:
    """Yield only syntax nodes of a (separated) list (skip tokens/None)."""
    from pyslang.syntax import SyntaxNode

    for x in seq:
        if isinstance(x, SyntaxNode):
            yield x


def kind_name(node) -> str:
    return node.kind.name if node is not None else ""


@dataclass
class _Ctx:
    sf: SourceFile
    sm: Any
    buffer_repr: str
    typedef_re: Optional["re.Pattern[str]"]
    decls: ModDecls
    tree: Any


class SlangModuleReader:
    """Produce :class:`ModDecls` for a module from its pyslang CST."""

    def __init__(self, design: "Design | None" = None, *, directive_policy: str = "text",
                 fill_modports: bool = True) -> None:
        self.design = design
        self.directive_policy = directive_policy
        self.fill_modports = fill_modports
        self._own_sm = None
        self._own_bag = None
        self._tree_cache: dict[tuple[str, int, int], Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, mod: "ModuleDef", config: "VerilogConfig") -> ModDecls:
        """Declarations of a module known to the design (uses its CST)."""
        assert self.design is not None
        sf = self.design.files[mod.file]
        decl = mod.syntax
        if decl is None or sf.tree is None:
            raise RuntimeError(f"no syntax tree for {mod.name}")
        sm = self.design.frontend.sm  # type: ignore[attr-defined]
        return self.read_syntax(decl, sf, config, sm)

    def read_module_from_file(self, modi: Modi, config: "VerilogConfig") -> Optional[ModDecls]:
        """Standalone use (no Design): parse *modi.filepath* and read *modi.name*.

        Returns ``None`` when the module must be read by the text parser
        (directive policy) or cannot be found.
        """
        sf, tree, sm = self._load_standalone(modi.filepath)
        _, SyntaxKind, _, _ = _pyslang()
        candidates = []
        for m in nodes(tree.root.members):
            if m.kind in (SyntaxKind.ModuleDeclaration, SyntaxKind.InterfaceDeclaration,
                          SyntaxKind.ProgramDeclaration, SyntaxKind.PackageDeclaration):
                if m.header.name.valueText == modi.name:
                    candidates.append(m)
        if not candidates:
            return None
        decl = candidates[0]
        if len(candidates) > 1:
            # Several definitions with the same name: pick the one whose name ends at modi.point
            for c in candidates:
                tok = c.header.name
                end = sf.char_offset(tok.location.offset + len(tok.rawText))
                if end == modi.point:
                    decl = c
                    break
        if self.needs_text_parser(decl, sf, tree, sm):
            return None
        return self.read_syntax(decl, sf, config, sm)

    def needs_text_parser(self, decl, sf: SourceFile, tree=None, sm=None) -> Optional[str]:
        """Reason the text parser must be used for *decl*, or ``None``."""
        tree = tree if tree is not None else sf.tree
        sm = sm if sm is not None else (self.design.frontend.sm if self.design else self._own_sm)  # type: ignore[attr-defined]
        rng = decl.sourceRange
        bstart, bend = rng.start.offset, rng.end.offset
        if self.directive_policy == "text":
            text = sf.data[bstart:bend].decode("utf-8", "replace")
            m = _DIRECTIVE_RE.search(text)
            if m:
                return f"contains `{m.group(1)}"
        if tree is not None:
            for d in tree.diagnostics:
                loc = d.location
                try:
                    is_err = d.isError()
                except Exception:
                    is_err = True
                if not is_err:
                    continue
                if sm is not None and sm.isMacroLoc(loc):
                    loc = sm.getFullyExpandedLoc(loc) if hasattr(sm, "getFullyExpandedLoc") else sm.getExpansionLoc(loc)
                if bstart <= loc.offset < bend:
                    return f"parse error at byte {loc.offset}"
        return None

    # ------------------------------------------------------------------
    # Core: syntax -> ModDecls
    # ------------------------------------------------------------------

    def read_syntax(self, decl, sf: SourceFile, config: "VerilogConfig", sm) -> ModDecls:
        _, SyntaxKind, _, _ = _pyslang()
        tdre = None
        if config is not None and config.typedef_regexp:
            try:
                tdre = re.compile(config.typedef_regexp)
            except re.error:
                tdre = None
        buffer_repr = str(decl.header.name.location.buffer)
        ctx = _Ctx(sf=sf, sm=sm, buffer_repr=buffer_repr, typedef_re=tdre, decls=ModDecls(), tree=sf.tree)
        header = decl.header
        if header.parameters is not None:
            last_kw: Optional[str] = None
            for d in nodes(header.parameters.declarations):
                last_kw = self._param_decl(d, ctx, in_port_list=True, inherited_kw=last_kw)
        if header.ports is not None and header.ports.kind == SyntaxKind.AnsiPortList:
            prev: Optional[tuple[str, Signal]] = None
            for p in nodes(header.ports.ports):
                prev = self._ansi_port(p, prev, ctx)
        self._members(decl.members, ctx)
        rng = decl.sourceRange
        start = sf.char_offset(rng.start.offset)
        end = sf.char_offset(rng.end.offset)
        ctx.decls.consts.extend(DeclParser._read_auto_constants(sf.text, start, end))
        return ctx.decls

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _members(self, members, ctx: _Ctx) -> None:
        for m in nodes(members):
            self._member(m, ctx)

    def _member(self, m, ctx: _Ctx) -> None:
        k = kind_name(m)
        if k == "PortDeclaration":
            self._port_decl(m, ctx)
        elif k in ("DataDeclaration", "NetDeclaration", "UserDefinedNetDeclaration"):
            self._data_decl(m, ctx)
        elif k == "ParameterDeclarationStatement":
            self._param_decl(m.parameter, ctx, in_port_list=False)
        elif k == "GenvarDeclaration":
            for ident in nodes(m.identifiers):
                ctx.decls.consts.append(Signal(name=self._name_text(ident.identifier if hasattr(ident, "identifier") else ident.getFirstToken())))
        elif k == "ContinuousAssign":
            for a in nodes(m.assignments):
                self._assign_lhs(a, ctx)
        elif k == "ModportDeclaration":
            if self.fill_modports:
                self._modport(m, ctx)
        elif k == "ClockingDeclaration":
            self._clocking(m, ctx)
        elif k == "GenerateRegion":
            self._members(m.members, ctx)
        elif k == "LoopGenerate":
            self._gen_block(m.block, ctx)
        elif k == "IfGenerate":
            self._gen_block(m.block, ctx)
            if m.elseClause is not None:
                self._gen_block(m.elseClause.clause, ctx)
        elif k == "CaseGenerate":
            for item in nodes(m.items):
                clause = getattr(item, "clause", None)
                if clause is not None:
                    self._gen_block(clause, ctx)
        elif k == "GenerateBlock":
            self._members(m.members, ctx)
        elif k in _PROC_KINDS:
            self._proc_vars(m, ctx)
        # everything else (functions, classes, instantiations, typedefs...) is ignored

    def _gen_block(self, block, ctx: _Ctx) -> None:
        if block is None:
            return
        if kind_name(block) == "GenerateBlock":
            self._members(block.members, ctx)
        else:
            self._member(block, ctx)

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    def _ansi_port(self, p, prev: Optional[tuple[str, Signal]], ctx: _Ctx) -> Optional[tuple[str, Signal]]:
        k = kind_name(p)
        if k == "ExplicitAnsiPort":
            direction = p.direction.valueText if p.direction is not None and not p.direction.isMissing else (prev[0] if prev else "input")
            sig = Signal(name=self._name_text(p.name))
            self._target(direction, ctx).append(sig)
            return (direction, sig)
        if k != "ImplicitAnsiPort":
            return prev
        header = p.header
        decl = p.declarator
        name = self._name_text(decl.name)
        memory = self._dims_text(decl.dimensions, ctx) or None
        hk = kind_name(header)
        if hk == "InterfacePortHeader":
            iface = header.nameOrKeyword.valueText
            if iface == "interface":
                return prev  # generic interface port: dropped
            modport = header.modport.member.valueText if header.modport is not None else None
            sig = Signal(name=name, type=iface, modport=modport, memory=memory)
            ctx.decls.interfaces.append(sig)
            return ("interface", sig)
        if hk == "InterconnectPortHeader":
            direction = header.direction.valueText if header.direction is not None and not header.direction.isMissing else (prev[0] if prev else "input")
            sig = Signal(name=name, memory=memory)
            self._target(direction, ctx).append(sig)
            return (direction, sig)
        direction_tok = header.direction if hasattr(header, "direction") else None
        direction = _tok_text(direction_tok)
        net_type = None
        if hk == "NetPortHeader":
            net_type = header.netType.valueText
        dt = header.dataType
        type_text, signed, dims, named = self._type_info(dt, ctx, net_type)
        if named is not None:
            # User-defined type that does not match typedef_regexp: an interface
            # port (same heuristic as DeclParser / verilog-mode).
            sig = Signal(name=name, type=named, memory=memory, signed=signed)
            ctx.decls.interfaces.append(sig)
            return ("interface", sig)
        if direction is None:
            if prev is not None and prev[0] != "interface":
                direction = prev[0]
                if kind_name(dt) == "ImplicitType" and not dims and net_type is None:
                    # `input [7:0] a, b` -> b inherits everything
                    type_text = prev[1].type
                    signed = prev[1].signed if signed is None else signed
                    dims = self._split_dims(prev[1])
            else:
                direction = "input"
        bits, multidim = self._bits_multidim(dims)
        sig = Signal(name=name, bits=bits, memory=memory, signed=signed, type=type_text, multidim=multidim)
        self._target(direction, ctx).append(sig)
        return (direction, sig)

    def _port_decl(self, m, ctx: _Ctx) -> None:
        """Non-ANSI ``input [7:0] a, b;`` in the body."""
        header = m.header
        hk = kind_name(header)
        if hk == "InterfacePortHeader":
            # Not legal in a body; DeclParser ignores it as well.
            return
        direction_tok = getattr(header, "direction", None)
        direction = _tok_text(direction_tok) or "input"
        net_type = header.netType.valueText if hk == "NetPortHeader" else None
        if hk == "InterconnectPortHeader":
            type_text, signed, dims, named = None, None, [], None
        else:
            type_text, signed, dims, named = self._type_info(header.dataType, ctx, net_type)
            if named is not None:
                type_text = None   # documented divergence: no bogus type-name signal
        bits, multidim = self._bits_multidim(dims)
        for d in nodes(m.declarators):
            name = self._name_text(d.name)
            memory = self._dims_text(d.dimensions, ctx) or None
            self._target(direction, ctx).append(
                Signal(name=name, bits=bits, memory=memory, signed=signed, type=type_text, multidim=multidim))

    def _data_decl(self, m, ctx: _Ctx) -> None:
        k = kind_name(m)
        dt = m.type
        if k == "UserDefinedNetDeclaration":
            type_text, signed, dims = None, None, []
        else:
            type_text, signed, dims, named = self._type_info(dt, ctx, None)
            if named is not None:
                # Declaration with an unknown user type (no typedef_regexp match):
                # DeclParser does not recognise it as a declaration at all.
                return
            if kind_name(dt) in _INTEGER_TYPE_KINDS or kind_name(dt) in _KEYWORD_TYPE_KINDS or kind_name(dt) == "ImplicitType":
                # Body variables carry no keyword type (DeclParser parity)
                type_text = None
        bits, multidim = self._bits_multidim(dims)
        for d in nodes(m.declarators):
            name = self._name_text(d.name)
            memory = self._dims_text(d.dimensions, ctx) or None
            ctx.decls.vars.append(Signal(name=name, bits=bits, memory=memory, signed=signed, type=type_text, multidim=multidim))

    def _param_decl(self, d, ctx: _Ctx, *, in_port_list: bool, inherited_kw: Optional[str] = None) -> Optional[str]:
        """Handle one parameter declaration; return the keyword in effect.

        In a ``#( ... )`` list an entry without ``parameter``/``localparam``
        inherits the previous entry's keyword; a leading entry without any
        keyword (``#(D = 64)``) is invisible to DeclParser and skipped.
        """
        k = kind_name(d)
        kw = getattr(d, "keyword", None)
        if kw is not None and not kw.isMissing and kw.valueText:
            keyword: Optional[str] = kw.valueText
        elif in_port_list:
            keyword = inherited_kw
        else:
            keyword = "parameter"
        if keyword is None:
            return None
        target = ctx.decls.consts if keyword == "localparam" else ctx.decls.gparams
        if k == "TypeParameterDeclaration":
            for a in nodes(d.declarators):
                target.append(Signal(name=self._name_text(a.name)))
            return keyword
        if k != "ParameterDeclaration":
            return keyword
        type_text, signed, dims, named = self._type_info(d.type, ctx, None)
        if named is not None:
            type_text = named
        bits, multidim = self._bits_multidim(dims)
        for decl in nodes(d.declarators):
            name = self._name_text(decl.name)
            memory = self._dims_text(decl.dimensions, ctx) or None
            target.append(Signal(name=name, bits=bits, memory=memory, signed=signed, type=type_text, multidim=multidim))
        return keyword

    def _proc_vars(self, node, ctx: _Ctx) -> None:
        """Variables declared inside procedural code (``for (int i ...)``,
        block-local declarations): DeclParser sees them as vars."""
        from pyslang.syntax import SyntaxNode

        stack = [node]
        while stack:
            n = stack.pop()
            k = kind_name(n)
            if k in ("FunctionDeclaration", "TaskDeclaration", "ClassDeclaration", "ClassMethodDeclaration"):
                continue
            if k == "DataDeclaration":
                self._data_decl(n, ctx)
                continue
            if k == "ForVariableDeclaration":
                decl = getattr(n, "declarator", None)
                if decl is not None:
                    ctx.decls.vars.append(Signal(name=self._name_text(decl.name)))
                continue
            for child in n:
                if isinstance(child, SyntaxNode):
                    stack.append(child)

    def _assign_lhs(self, a, ctx: _Ctx) -> None:
        if kind_name(a) != "AssignmentExpression":
            return
        for name in self._lhs_names(a.left):
            ctx.decls.assigns.append(Signal(name=name))

    def _lhs_names(self, e) -> list[str]:
        k = kind_name(e)
        if k == "IdentifierName":
            return [self._name_text(e.identifier)]
        if k in ("MemberAccessExpression", "ScopedName"):
            return []   # dotted/hierarchical LHS: skipped (DeclParser parity)
        if k == "ConcatenationExpression":
            out: list[str] = []
            for x in nodes(e.expressions):
                out.extend(self._lhs_names(x))
            return out
        if k == "IdentifierSelectName":
            return [self._name_text(e.identifier)]
        left = getattr(e, "left", None)
        if left is not None:
            return self._lhs_names(left)
        return []

    def _modport(self, m, ctx: _Ctx) -> None:
        for item in nodes(m.items):
            mp = Modport(name=self._name_text(item.name))
            for pl in nodes(item.ports):
                if kind_name(pl) != "ModportSimplePortList":
                    continue
                direction = pl.direction.valueText
                for p in nodes(pl.ports):
                    pk = kind_name(p)
                    if pk in ("ModportNamedPort", "ModportExplicitPort"):
                        mp.signals.append(Signal(name=self._name_text(p.name), type=direction))
            ctx.decls.modports.append(mp)

    def _clocking(self, m, ctx: _Ctx) -> None:
        gd = getattr(m, "globalOrDefault", None)
        if gd is not None and not gd.isMissing and gd.valueText:
            return
        name_tok = getattr(m, "blockName", None)
        if name_tok is None or name_tok.isMissing:
            return
        mp = Modport(name=self._name_text(name_tok))
        for it in nodes(m.items):
            if kind_name(it) != "ClockingItem":
                continue
            d = it.direction
            if d is None:
                continue
            dtext = str(d)
            has_in = "input" in dtext
            has_out = "output" in dtext
            if "inout" in dtext or (has_in and has_out):
                direction = "inout"
            elif has_out:
                direction = "output"
            elif has_in:
                direction = "input"
            else:
                continue
            for decl in nodes(it.decls):
                tok = decl.getFirstToken()
                if tok is not None and tok.rawText:
                    mp.signals.append(Signal(name=self._name_text(tok), type=direction))
        ctx.decls.modports.append(mp)

    # ------------------------------------------------------------------
    # Types and text
    # ------------------------------------------------------------------

    def _type_info(self, dt, ctx: _Ctx, net_type: Optional[str]) -> tuple[Optional[str], Optional[str], list[str], Optional[str]]:
        """(type_text, signed, packed_dims, named_nontypedef)."""
        if dt is None:
            return (net_type, None, [], None)
        k = kind_name(dt)
        signing = getattr(dt, "signing", None)
        signed = signing.valueText if signing is not None and not signing.isMissing and signing.valueText else None
        dims = self._dims_list(getattr(dt, "dimensions", None), ctx)
        if k in _INTEGER_TYPE_KINDS:
            kw = dt.keyword.valueText
            return (f"{net_type} {kw}" if net_type else kw, signed, dims, None)
        if k == "ImplicitType":
            return (net_type, signed, dims, None)
        if k in _KEYWORD_TYPE_KINDS:
            kw = dt.keyword.valueText if hasattr(dt, "keyword") else self._node_text(dt, ctx)
            return (f"{net_type} {kw}" if net_type else kw, signed, dims, None)
        if k == "NamedType":
            name, sel_dims = self._named_type_parts(dt.name, ctx)
            dims = dims + sel_dims
            if ctx.typedef_re is not None and ctx.typedef_re.search(name):
                return (f"{net_type} {name}" if net_type else name, signed, dims, None)
            return (net_type, signed, dims, name)
        if k in ("StructUnionType", "EnumType", "VirtualInterfaceType", "TypeReference"):
            return (net_type, signed, dims, None)
        return (net_type, signed, dims, None)

    def _named_type_parts(self, name_node, ctx: _Ctx) -> tuple[str, list[str]]:
        """``pkg::type_t [1:0]`` is parsed as a name with selectors; split it
        into the type name and its packed dimensions."""
        k = kind_name(name_node)
        if k == "IdentifierSelectName":
            base = self._name_text(name_node.identifier)
            sels = [self._node_text_ws(s, ctx) for s in nodes(name_node.selectors)]
            return base, sels
        if k == "ScopedName":
            left = _WS_RE.sub("", self._node_text(name_node.left, ctx))
            right, sels = self._named_type_parts(name_node.right, ctx)
            return f"{left}::{right}", sels
        if k == "ClassName":
            return self._name_text(name_node.identifier), []
        return _WS_RE.sub("", self._node_text(name_node, ctx)), []

    def _dims_list(self, dims, ctx: _Ctx) -> list[str]:
        if dims is None:
            return []
        return [self._node_text_ws(d, ctx) for d in nodes(dims)]

    def _dims_text(self, dims, ctx: _Ctx) -> str:
        return "".join(self._dims_list(dims, ctx))

    @staticmethod
    def _bits_multidim(dims: list[str]) -> tuple[Optional[str], Optional[list[str]]]:
        if not dims:
            return (None, None)
        if len(dims) == 1:
            return (dims[0], None)
        return (dims[-1], list(dims[:-1]))

    @staticmethod
    def _split_dims(sig: Signal) -> list[str]:
        dims = list(sig.multidim) if sig.multidim else []
        if sig.bits:
            dims.append(sig.bits)
        return dims

    def _name_text(self, tok) -> str:
        raw = tok.rawText
        if raw.startswith("\\"):
            return raw + " "
        return tok.valueText or raw

    def _node_text_ws(self, node, ctx: _Ctx) -> str:
        return _WS_RE.sub("", self._node_text(node, ctx))

    def _node_text(self, node, ctx: _Ctx) -> str:
        """Source text of *node* from the file bytes, macros left unexpanded."""
        ft = node.getFirstToken()
        lt = node.getLastToken()
        if ft is None or lt is None:
            return ""
        sm = ctx.sm
        s = ft.location
        e = lt.location
        guard = 0
        while sm.isMacroLoc(s) and guard < 16:
            s = sm.getExpansionRange(s).start
            guard += 1
        if sm.isMacroLoc(e):
            guard = 0
            while sm.isMacroLoc(e) and guard < 16:
                e = sm.getExpansionRange(e).end
                guard += 1
            end_off = e.offset
        else:
            end_off = e.offset + len(lt.rawText)
        if str(s.buffer) != ctx.buffer_repr or str(e.buffer) != ctx.buffer_repr:
            return self._printed(node, ctx)
        return ctx.sf.data[s.offset:end_off].decode("utf-8", "replace")

    def _printed(self, node, ctx: _Ctx) -> str:
        from pyslang.syntax import SyntaxPrinter

        sp = SyntaxPrinter(ctx.sm)
        sp.setIncludeTrivia(False).setIncludeDirectives(True).setIncludeSkipped(True)
        if hasattr(sp, "setExpandMacros"):
            sp.setExpandMacros(False)
        return sp.print(node).str()

    @staticmethod
    def _target(direction: str, ctx: _Ctx) -> list[Signal]:
        if direction == "output":
            return ctx.decls.outputs
        if direction == "inout":
            return ctx.decls.inouts
        return ctx.decls.inputs   # input / ref

    # ------------------------------------------------------------------
    # Standalone parsing (no Design)
    # ------------------------------------------------------------------

    def _load_standalone(self, filepath: str):
        pyslang, _, _, SyntaxTree = _pyslang()
        if self._own_sm is None:
            self._own_sm = pyslang.SourceManager()
            self._own_bag = pyslang.Bag()
        path = os.path.normpath(os.path.abspath(filepath))
        st = os.stat(path)
        key = (os.path.normcase(path), st.st_size, st.st_mtime_ns)
        cached = self._tree_cache.get(key)
        if cached is not None:
            return cached[0], cached[1], self._own_sm
        sf = SourceFile.read(path, "library")
        tree = SyntaxTree.fromFileInMemory(sf.slang_text, self._own_sm, path, path, self._own_bag)
        sf.tree = tree
        self._tree_cache[key] = (sf, tree)
        return sf, tree, self._own_sm
