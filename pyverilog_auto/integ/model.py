"""Design model: source ranges, module definitions, references, instances.

Everything here is backend-agnostic; ``frontend_text`` and ``frontend_slang``
populate these structures, ``Design`` (design.py) exposes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Literal, Optional

from ..signal import Modi

if TYPE_CHECKING:
    from .sources import SourceFile


ModuleKind = Literal["module", "interface", "program", "package", "connectmodule", "primitive"]
RefKind = Literal["inst", "auto", "iface_port", "bind"]
ConnStyle = Literal["named", "ordered", "empty", "wildcard"]

# AUTO markers whose argument names another module (creates a dependency edge).
AUTO_MODULE_REF_MARKERS = (
    "AUTOINOUTMODULE", "AUTOINOUTCOMP", "AUTOINOUTIN", "AUTOINOUTPARAM",
    "AUTOINOUTMODPORT", "AUTOASSIGNMODPORT",
)


# ----------------------------------------------------------------------
# Source ranges
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SrcRange:
    """A half-open range in one file, in both char and byte offsets."""

    file: str            # SourceFile.key
    start: int           # char offset into SourceFile.text
    end: int
    bstart: int          # byte offset into SourceFile.data
    bend: int
    line: int = 0        # 1-based line of start
    col: int = 0         # 0-based column of start
    is_macro: bool = False
    in_auto_fence: bool = False

    @classmethod
    def from_chars(cls, sf: "SourceFile", start: int, end: int, **flags) -> "SrcRange":
        line, col = sf.line_col(start)
        return cls(sf.key, start, end, sf.byte_offset(start), sf.byte_offset(end), line, col, **flags)

    @classmethod
    def from_bytes(cls, sf: "SourceFile", bstart: int, bend: int, **flags) -> "SrcRange":
        start = sf.char_offset(bstart)
        end = sf.char_offset(bend)
        line, col = sf.line_col(start)
        return cls(sf.key, start, end, bstart, bend, line, col, **flags)

    def text(self, sf: "SourceFile") -> str:
        return sf.text[self.start:self.end]

    def contains(self, char_off: int) -> bool:
        return self.start <= char_off < self.end

    def __len__(self) -> int:
        return self.end - self.start


# ----------------------------------------------------------------------
# Module-level facts
# ----------------------------------------------------------------------

@dataclass
class PortInfo:
    """One port of a module as written in the source (both header styles)."""

    name: str
    direction: Optional[str]            # input | output | inout | ref ; None for interface ports
    is_interface: bool = False
    iface_type: Optional[str] = None
    modport: Optional[str] = None
    type_text: Optional[str] = None     # net/var keyword(s) or typedef name, e.g. "logic", "wire", "pkg::t"
    packed_dims: str = ""               # symbolic, whitespace-stripped, e.g. "[W-1:0][3:0]"
    unpacked_dims: str = ""
    signed: Optional[str] = None        # "signed" | "unsigned" | None
    range: Optional[SrcRange] = None    # the port declaration (header entry, or body declaration)
    decl_range: Optional[SrcRange] = None        # non-ANSI: body `input ... name;` statement
    list_entry_range: Optional[SrcRange] = None  # non-ANSI: name in the header list
    in_auto_fence: bool = False
    ansi: bool = True


@dataclass
class SymbolInfo:
    name: str
    kind: Literal["net", "var", "port", "instance", "param", "typedef", "genvar", "iface_inst"]
    type_text: Optional[str]
    range: Optional[SrcRange]
    in_auto_fence: bool = False


@dataclass
class MarkerInfo:
    """An AUTO marker in a module with the region it owns."""

    kind: str                              # AUTOINPUT, AUTOOUTPUT, AUTOINOUT, AUTOWIRE, AUTOLOGIC, AUTOREG,
                                           # AUTOARG, AUTOINST, AUTOINSTPARAM, DOTSTAR, ... (uppercase)
    range: SrcRange                        # the marker comment itself
    fence_range: Optional[SrcRange] = None # generated region (fence or marker..close-paren), None if not expanded
    args: str = ""                         # text inside the marker parens, if any


@dataclass
class PinInfo:
    port: str
    expr_text: str
    range: SrcRange


@dataclass
class ModuleRef:
    """One use of another module by name inside a module body."""

    module: str                            # referenced definition name (deticked)
    kind: RefKind
    inst_name: Optional[str]
    range: SrcRange                        # whole statement / marker
    conn_range: Optional[SrcRange] = None  # "(" ... ")" of the port-connection list (inst)
    param_range: Optional[SrcRange] = None # "#(" ... ")" (inst)
    array_dims: Optional[str] = None       # "[3:0]" for instance arrays
    in_generate: bool = False
    is_array: bool = False
    connections_style: Optional[ConnStyle] = None
    marker_range: Optional[SrcRange] = None   # /*AUTOINST*/ inside the connection list
    dotstar_range: Optional[SrcRange] = None  # .* inside the connection list
    explicit_pins: list[PinInfo] = field(default_factory=list)   # outside fences
    param_overrides: dict[str, str] = field(default_factory=dict)
    resolved: Optional["ModuleDef"] = None

    @property
    def uses_autoinst(self) -> bool:
        return self.marker_range is not None or self.dotstar_range is not None


@dataclass
class ModuleDef:
    """A module / interface / program / package definition found in a file."""

    name: str
    kind: ModuleKind
    file: str                              # SourceFile.key
    keyword_range: SrcRange                # "module" / "interface" keyword
    header_range: SrcRange                 # keyword .. header ';' inclusive
    name_end: int                          # char offset after the name token -> Modi.point
    port_list_range: Optional[SrcRange]    # "(" ... ")" of the header list, or None
    port_list_close: Optional[int]         # char offset of the closing ")"
    ansi: bool
    header_end: int                        # char offset just after the header ';'
    body_start: int                        # == header_end
    end_range: SrcRange                    # "endmodule" keyword
    ports: list[PortInfo] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)
    modports: list[str] = field(default_factory=list)
    markers: dict[str, list[MarkerInfo]] = field(default_factory=dict)
    refs: list[ModuleRef] = field(default_factory=list)
    is_library: bool = False
    needs_text_parser: bool = False
    text_parser_reason: Optional[str] = None
    instances: list["Instance"] = field(default_factory=list)
    syntax: object = field(default=None, repr=False, compare=False)   # pyslang ModuleDeclarationSyntax

    @property
    def path(self) -> str:
        return self.file

    def modi(self, filepath: str) -> Modi:
        """Locator for the classic parsers (``DeclParser`` re-finds the keyword)."""
        kind = "module" if self.kind in ("connectmodule", "primitive") else self.kind
        return Modi(name=self.name, filepath=filepath, point=self.name_end, type=kind)

    def has_marker(self, *kinds: str) -> bool:
        return any(k in self.markers and self.markers[k] for k in kinds)

    def port(self, name: str) -> Optional[PortInfo]:
        for p in self.ports:
            if p.name == name:
                return p
        return None

    def instantiations(self) -> list[ModuleRef]:
        return [r for r in self.refs if r.kind in ("inst", "bind")]


# ----------------------------------------------------------------------
# Elaborated instances
# ----------------------------------------------------------------------

@dataclass
class Instance:
    """One node of the instance tree (hierarchical path is the identity)."""

    path: str                              # e.g. "top.gen_cores[1].u_core.u_dma"
    name: str                              # last path component
    module_name: str
    module: Optional[ModuleDef]
    parent: Optional["Instance"]
    children: list["Instance"] = field(default_factory=list)
    file: Optional[str] = None
    is_interface: bool = False
    is_blackbox: bool = False
    array_index: Optional[tuple[int, ...]] = None
    gen_path: tuple[str, ...] = ()         # generate-block components between parent and self
    ref: Optional[ModuleRef] = None        # instantiation site in the parent's body
    depth: int = 0
    symbol: object = None                  # pyslang InstanceSymbol (slang backend only)

    def ancestors(self) -> list["Instance"]:
        out: list[Instance] = []
        p = self.parent
        while p is not None:
            out.append(p)
            p = p.parent
        out.reverse()
        return out

    def walk(self) -> Iterator["Instance"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def root(self) -> "Instance":
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Instance({self.path!r} : {self.module_name})"


@dataclass
class ElaboratedPort:
    """Facts about one port of one elaborated instance (pyslang backend)."""

    name: str
    kind: Literal["signal", "struct", "iface"]
    direction: Optional[str]                 # input | output | inout | ref | None (interface)
    type_text_numeric: str                   # evaluated type, e.g. "logic[7:0]"
    packed_dims_numeric: str                 # "[7:0]" from the elaborated type (or "")
    unpacked_dims: str                       # symbolic text of unpacked dims (or "")
    bit_width: Optional[int]
    is_struct: bool = False
    struct_qualified_name: Optional[str] = None   # "pkg::req_t"
    struct_is_local: bool = False
    iface_type: Optional[str] = None
    modport: Optional[str] = None
    param_dependent: bool = False            # symbolic width mentions a parameter of the module
    symbolic_packed_dims: str = ""           # from the source text (PortInfo)
    symbolic_type_text: Optional[str] = None


@dataclass
class Diag:
    severity: Literal["error", "warning", "note"]
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    source: str = "design"                 # filelist | parse | elab | design

    def __str__(self) -> str:
        loc = ""
        if self.file:
            loc = f"{self.file}:{self.line}: " if self.line else f"{self.file}: "
        return f"{loc}{self.severity}: {self.message}"
