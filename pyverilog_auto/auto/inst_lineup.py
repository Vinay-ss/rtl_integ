"""Instance pin lineup and port comments.

``lineup_instances`` is called once at the very end of ``AutoEngine.run``,
after every AUTO expansion (so the elisp expansion order is unchanged), and
only when ``cfg.auto_inst_lineup or cfg.auto_inst_port_comment`` is set.
With both switches off (the default) it is never called, so the Emacs
goldens stay byte-identical.

Pipeline order::

    AUTO expansion (unchanged elisp order)
      -> instance lineup/comments  (end of AutoEngine.run, only when enabled)
      -> [route edits, optional --then-expand]
      -> strip  (ONCE, last, after all passes, over every processed source file)

Config (``VerilogConfig``; Local Variables override the CLI flags):

==============================  =======  ======================================  ==========================
field                           default  Local Variable                          CLI flag
==============================  =======  ======================================  ==========================
``auto_inst_lineup: bool``      False    ``verilog-auto-inst-lineup``            ``--inst-lineup``
``auto_inst_port_comment``      None     ``verilog-auto-inst-port-comment:``     ``--inst-port-comment``
  ``: Optional[str]``                    ``"dir width type"``                    ``dir,width,type``
``auto_inst_comment_column``    0        ``verilog-auto-inst-comment-column``    ``--inst-comment-column N``
  ``: int``                     (auto)
==============================  =======  ======================================  ==========================

``auto_inst_port_comment`` is a space-separated subset of ``dir width type``
(the CLI normalizes ``dir,width,type`` to ``"dir width type"``);
:func:`pyverilog_auto.config.port_comment_fields` turns it into a tuple.

Function contract
-----------------
``lineup_instances(text, lookup, cfg) -> str``

* Pure: no I/O; *text* is LF text (the ``VerilogBuffer`` contents).
* ``lookup(module_name)`` returns the instantiated module's ``ModDecls`` or
  ``None`` when the module is unknown.  The engine passes a real lookup
  (``db.lookup(name, ignore_error=True)`` then ``db.get_decls(modi)``,
  cached, exceptions -> ``None``); tests pass a dict-based lookup.
* With no lookup result, the instance is only aligned and gets no comments.
* Idempotent: ``f(f(x)) == f(x)``.
* Instances to skip (left untouched): positional or ordered connections, or
  a list that does not parse.

Pin-line comment format (shared by Track B and Track C)::

    <indent>.port<pad>(net),<pad>// <dir> <width> <type>[  // Templated...][  // Implicit .*][  // routed: ...][  <user comment>]

* The port-comment segment always comes first.
* A segment is a port comment when it fully matches
  ``// DIR( \\[..\\]+)?( TYPE( signed)?)?`` with nothing after it.  TYPE must
  be a SV built-in type keyword, an ``iface.modport``, or the type the
  formatter would render now.  So ``// input from ctrl`` stays a user comment.
* Strip removes only the ``Templated`` / ``Implicit .*`` / ``routed:``
  segments and keeps the port comment.
* Comment text must stay on the pin's own line, after ``),`` or ``));``.  A
  comment on its own line would hide the following pins from
  ``SubDeclParser``.
* Comment text must not add unbalanced parens or ``.ident (`` sequences
  (``auto/delete.py`` and ``parser/inst_parser.py`` count parens without
  skipping comments).

Layout:

* One pin per line; ``.`` at the first pin's column.
* ``(`` at ``P = max(inst.py column rule, dot_col + len(longest ".port") + 1)``.
* Comment at ``C = max(cfg.auto_inst_comment_column, longest "(net)," end + 1)``;
  the fields inside the comment are padded into sub-columns.
* Fields: ``dir`` = input / output / inout / interface / parameter;
  ``width`` = ``Signal.bits`` plus multidim, with unpacked ``memory`` appended;
  ``type`` = ``Signal.type`` plus `` signed``, or ``iface.modport``; ``wire``
  when nothing is declared.
* Switches: ``lineup`` aligns pins; ``port_comment`` adds aligned comments
  (and splits multi-pin lines, keeping ``(`` spacing unless ``lineup``);
  both together give the full format.
* Lines that are not pins (section headers, ``/*AUTOINST*/``, ``.*,``) are
  kept and re-indented to the ``.`` column.
* Existing trailing comments: a port-comment segment is replaced;
  ``Templated``, ``Implicit .*``, ``routed:`` and user comments are kept as
  later segments.

Implementation notes
--------------------

* Columns are visual columns (tabs expand to 8 columns); every line the
  formatter rebuilds uses spaces.
* ``dot_col`` is the first pin's column, except when an ``/*AUTOINST*/`` /
  ``/*AUTOINSTPARAM*/`` marker precedes the first pin: then it is one column
  after ``(``, where AUTOINST regenerates its pins on every run (otherwise a
  ``#(`` edit on the paren line would move the pins again on the next run).
* ``P = max(cfg.auto_inst_column, 16 + 8*((dot_col+7)//8),
  dot_col + len(longest ".port") + 1)`` -- the ``auto/inst.py`` rule with
  the pin column as indent, so short AUTOINST pins keep their Emacs column.
* ``C = max(cfg.auto_inst_comment_column, longest pin line end +
  MIN_COMMENT_GAP)`` with a minimum gap of one space.  Every trailing
  comment of the list (port comment, ``// Templated``, user comment) starts
  at ``C``.
* The ``#(`` parameter list and the port list are laid out separately: each
  has its own ``.`` / ``(`` / comment columns and comment sub-columns.
* A port comment is ``// `` plus the enabled fields; each field except the
  last is padded to the widest value of its sub-column in the list, and a
  sub-column that is empty for every pin is dropped.  When later segments
  follow (``// Templated``, user comments), the port comment is padded to
  the widest port comment of the list so those segments line up as well.
  Widths are rendered without whitespace.  A ``width`` or ``type`` value
  that could not be recognized again (nested brackets, ``struct {...}``
  types) or that holds an unbalanced paren or a ``.ident (`` sequence is
  left out.  A parameter with no declared type gets no type field (``wire``
  is only for ports).
* Recognition (:func:`is_port_comment`): a pin's trailing ``//`` comment is
  split into segments at whitespace followed by ``//``; a segment is a port
  comment when its whitespace-separated tokens are
  ``[DIR] [WIDTH] [TYPE [signed] | signed]`` with nothing else (DIR may only
  be missing when ``dir`` is not an enabled field).  When a port comment is
  rendered for the pin, every port-comment segment is dropped -- not only the
  first: regenerating AUTOINST leaves the old last-pin comment *after* the
  new ``// Templated`` / ``// Implicit .*`` (``auto/delete.py`` only strips
  those when they end the line).  The other segments are kept in order.
* The last pin keeps the closing ``)`` / ``);`` on its line and its comment
  goes after it.  When other code follows the closing paren on that line
  (``#(...) u_sub (``), that pin gets no comment.
* Block comments that trail a pin on its line (``/*AUTOINST*/`` after a
  hand-written pin) stay right after the pin; the ``//`` comment follows at
  ``C`` or one space later (such lines do not widen ``C``).
* With only ``port_comment`` on, a list where no port is known (unknown
  module or no matching names) is left untouched; existing comments are
  never removed when no port comment can be rendered for a pin.
* Lists left untouched: ordered/positional, wildcard-only (``.*``) or empty
  connections, a directive inside the list (it parses as ordered), pins not
  separated by one trailing comma (leading-comma style, a comment before the
  comma), a comment between ``(`` / ``,`` and a pin on the same line, a
  block comment spanning lines, and anything else that does not parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from ..config import port_comment_fields
from ..integ.sources import SourceFile
from ..integ.textscan import (
    find_instantiations,
    mask_comments_and_strings,
    matching_close,
    scan_connections,
)
from ..parser.decl_parser import _TYPE_KEYWORDS

if TYPE_CHECKING:
    from ..config import VerilogConfig
    from ..signal import ModDecls, Signal

TAB_WIDTH = 8
# Minimum number of spaces between the longest pin line and the comment column.
MIN_COMMENT_GAP = 1
# Separator between the port comment and a kept later segment.
SEGMENT_GAP = "  "

DIRECTIONS = ("input", "output", "inout", "interface", "parameter")
_FIELD_INDEX = {"dir": 0, "width": 1, "type": 2}

_WIDTH_TOKEN_RE = re.compile(r"(?:\[[^\[\]\s]*\])+\Z")
_IFACE_MODPORT_RE = re.compile(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*\Z")
_TYPE_TOKEN_RE = re.compile(r"[A-Za-z_][\w$]*(?:::[A-Za-z_][\w$]*)*(?:\.[A-Za-z_][\w$]*)?\Z")
_DOT_CALL_RE = re.compile(r"\.\s*(?:[A-Za-z_][\w$]*|\\\S+)\s*\(")
_SEGMENT_SPLIT_RE = re.compile(r"[ \t]+(?=//)")
_PIN_HEAD_RE = re.compile(r"\.\s*(?:[A-Za-z_][\w$]*|\\\S+)\s*")
_DOTSTAR_RE = re.compile(r"\.\*")
_AUTOINST_MARKER_RE = re.compile(r"/\*\s*AUTOINST", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


class _Skip(Exception):
    """A connection list that must be left untouched."""


@dataclass
class _Item:
    """One connection (``.port(net)``, ``.port`` or ``.*``) of a list."""

    start: int
    end: int
    name: Optional[str]          # port name; None for ``.*``
    conn: Optional[str]          # original ``( ... )`` text; None for ``.port`` / ``.*``
    raw: str                     # original item text
    sep: str = ""                # original text after the item through its ``,``
    newline_before: bool = True  # the item starts its own line in the input
    prefix: str = ""             # its original indentation (when newline_before)
    before: list[str] = field(default_factory=list)  # comment/blank lines above it
    block: str = ""              # block comments trailing the item on its line
    comment: str = ""            # ``//`` comment trailing the item on its line
    no_comment: bool = False     # code follows the closing paren on this line
    fields: Optional[tuple[str, str, str]] = None   # (dir, width, type)
    rtype: Optional[str] = None  # rendered type token (without ``signed``)


@dataclass
class _List:
    """A parsed connection list ``( ... )``."""

    items: list[_Item]
    region: tuple[int, int]      # text span the formatter rewrites
    lead: str                    # paren line up to and including ``(``
    head: Optional[str]          # rest of the paren line (first pin on a later line)
    gap0: str                    # whitespace between ``(`` and a first pin on the paren line
    after: list[str]             # comment/blank lines between the last pin and the close line
    close_prefix: Optional[str]  # indentation of a close paren on its own line
    close_code: str              # ``)`` plus ``;`` when the close is on the last pin's line
    auto_first: bool = False     # an AUTOINST/AUTOINSTPARAM marker precedes the first pin


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _vcol(s: str, col: int = 0) -> int:
    """Visual column after *s* (tabs to 8 columns; a newline resets)."""
    for ch in s:
        if ch == "\t":
            col += TAB_WIDTH - col % TAB_WIDTH
        elif ch == "\n":
            col = 0
        else:
            col += 1
    return col


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    j = text.find("\n", pos)
    return len(text) if j < 0 else j


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _hazard_free(s: str) -> bool:
    """No unbalanced parens and no ``.ident (`` sequence (see the contract)."""
    return _balanced(s) and _DOT_CALL_RE.search(s) is None


def _has_multiline_block(text: str, start: int, end: int) -> bool:
    """True when a ``/* */`` comment in ``text[start:end]`` spans lines."""
    i = start
    while i < end:
        if text.startswith("//", i):
            i = _line_end(text, i)
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0 or "\n" in text[i:j]:
                return True
            i = j + 2
        elif text[i] == '"':
            j = i + 1
            while j < end and text[j] not in '"\n':
                if text[j] == "\\":
                    j += 1
                j += 1
            i = j + 1
        else:
            i += 1
    return False


def _parse_trail(s: str) -> tuple[str, str]:
    """Split comment-only text trailing a pin into (block comments, ``//`` comment)."""
    s = s.strip()
    blocks: list[str] = []
    while s.startswith("/*"):
        j = s.find("*/", 2)
        if j < 0:
            raise _Skip
        blocks.append(s[:j + 2])
        s = s[j + 2:].lstrip()
    if s and not s.startswith("//"):
        raise _Skip
    return " ".join(blocks), s.rstrip()


def _segments(comment: str) -> list[str]:
    """Split a ``//`` comment at whitespace followed by ``//``."""
    return _SEGMENT_SPLIT_RE.split(comment)


# ----------------------------------------------------------------------
# Port fields
# ----------------------------------------------------------------------

def _width_of(sig: "Signal") -> str:
    width = "".join(sig.multidim or []) + (sig.bits or "") + (sig.memory or "")
    width = _WS_RE.sub("", width)
    if not width or not _WIDTH_TOKEN_RE.match(width) or not _hazard_free(width):
        return ""
    return width


def _type_of(sig: "Signal", direction: str) -> tuple[str, str]:
    """(rendered type field, type token without ``signed``)."""
    if direction == "interface":
        tok = sig.type or ""
        if tok and sig.modport:
            tok = f"{tok}.{sig.modport}"
    elif direction == "parameter":
        tok = sig.type or ""
    else:
        tok = sig.type or "wire"
    if tok and not _TYPE_TOKEN_RE.match(tok):
        tok = ""
    full = tok
    if sig.signed == "signed":
        full = f"{tok} signed" if tok else "signed"
    return full, tok


def _port_fields(decls: "ModDecls", name: str, is_param: bool):
    """((dir, width, type), type token) of port *name*, or None if unknown."""
    if is_param:
        groups = (("parameter", decls.gparams),)
    else:
        groups = (("output", decls.outputs), ("inout", decls.inouts),
                  ("input", decls.inputs), ("interface", decls.interfaces))
    for direction, sigs in groups:
        for sig in sigs or ():
            if sig.name == name:
                full, tok = _type_of(sig, direction)
                return (direction, _width_of(sig), full), (tok or None)
    return None


def is_port_comment(segment: str, fields: tuple[str, ...] = ("dir", "width", "type"),
                    rendered_type: Optional[str] = None) -> bool:
    """True when the ``//`` *segment* is a port comment (see the module docstring).

    TYPE must be a SV built-in type keyword, an ``iface.modport`` or
    *rendered_type* (the type the formatter would render now), so
    ``// input from ctrl`` stays a user comment.
    """
    if not segment.startswith("//"):
        return False
    toks = segment[2:].split()
    n = len(toks)
    if n == 0:
        return False
    i = 0
    if toks[0] in DIRECTIONS:
        i = 1
    elif "dir" in fields:
        return False
    if i < n and _WIDTH_TOKEN_RE.match(toks[i]):
        i += 1
    if i < n:
        tok = toks[i]
        if tok == "signed":
            i += 1
        elif (tok in _TYPE_KEYWORDS or _IFACE_MODPORT_RE.match(tok)
              or (rendered_type is not None and tok == rendered_type)):
            i += 1
            if i < n and toks[i] == "signed":
                i += 1
    return i == n


# ----------------------------------------------------------------------
# Parsing one connection list
# ----------------------------------------------------------------------

def _parse_list(text: str, masked: str, sf: SourceFile, open_: int, close: int,
                lead: Optional[str] = None) -> _List:
    """Parse ``text[open_:close+1]``; raise :class:`_Skip` when it must stay untouched.

    *lead* overrides the paren line up to ``(`` (used when an edit of the
    ``#(`` list on the same line moves the port list's paren).
    """
    style, pins, _marker, _dotstar = scan_connections(sf, text, masked, open_, close, None)
    if style != "named" or not pins:
        raise _Skip
    if _has_multiline_block(text, open_ + 1, close):
        raise _Skip

    items: list[_Item] = []
    for pin in pins:
        s, e = pin.range.start, pin.range.end
        m = _PIN_HEAD_RE.match(masked, s, e)
        if m is None:
            raise _Skip
        conn: Optional[str] = None
        if m.end() < e:
            if masked[m.end()] != "(" or matching_close(masked, m.end(), e) != e - 1:
                raise _Skip
            conn = text[m.end():e]
        items.append(_Item(s, e, pin.port, conn, text[s:e]))
    for m in _DOTSTAR_RE.finditer(masked, open_ + 1, close):
        items.append(_Item(m.start(), m.end(), None, None, ".*"))
    items.sort(key=lambda it: it.start)

    # Between "(" and the first item.
    first = items[0]
    if masked[open_ + 1:first.start].strip():
        raise _Skip
    g0 = text[open_ + 1:first.start]
    head: Optional[str] = None
    gap0 = ""
    if "\n" in g0:
        parts = g0.split("\n")
        if parts[-1].strip():
            raise _Skip
        head = parts[0].rstrip()
        first.before = parts[1:-1]
        first.prefix = parts[-1]
    else:
        if g0.strip():
            raise _Skip
        gap0 = g0
        first.newline_before = False

    # Between items: exactly one "," right after the item.
    for a, b in zip(items, items[1:]):
        if a.end > b.start:
            raise _Skip
        mg = masked[a.end:b.start]
        if mg.strip() != ",":
            raise _Skip
        cpos = a.end + mg.index(",")
        if text[a.end:cpos].strip(" \t"):
            raise _Skip
        a.sep = text[a.end:cpos + 1]
        rest = text[cpos + 1:b.start]
        if "\n" in rest:
            parts = rest.split("\n")
            if parts[-1].strip():
                raise _Skip
            a.block, a.comment = _parse_trail(parts[0])
            b.before = parts[1:-1]
            b.prefix = parts[-1]
        else:
            if rest.strip():
                raise _Skip
            b.newline_before = False

    # After the last item.
    last = items[-1]
    if masked[last.end:close].strip():
        raise _Skip
    rest = text[last.end:close]
    after: list[str] = []
    close_prefix: Optional[str] = None
    close_code = ""
    if "\n" in rest:
        parts = rest.split("\n")
        if parts[-1].strip():
            raise _Skip
        last.block, last.comment = _parse_trail(parts[0])
        after = parts[1:-1]
        close_prefix = parts[-1]
        region_end = close
    else:
        if rest.strip(" \t"):
            raise _Skip
        last.sep = rest
        eol = _line_end(text, close)
        code = masked[close + 1:eol].rstrip()
        if code.strip() in ("", ";"):
            close_code = ")" + text[close + 1:close + 1 + len(code)]
            last.block, last.comment = _parse_trail(text[close + 1 + len(code):eol])
            region_end = eol
        else:
            last.no_comment = True
            region_end = close

    if lead is None:
        lead = text[_line_start(text, open_):open_ + 1]
    auto_first = _AUTOINST_MARKER_RE.search(text, open_ + 1, first.start) is not None
    return _List(items, (open_ + 1, region_end), lead, head, gap0, after, close_prefix, close_code,
                 auto_first)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def _port_comments(items: list[_Item], fields: tuple[str, ...]) -> dict[int, str]:
    """Port comment text per item (keyed by ``id``), padded into sub-columns."""
    rows = [it for it in items if it.fields is not None and not it.no_comment]
    widths = {f: max((len(it.fields[_FIELD_INDEX[f]]) for it in rows), default=0) for f in fields}
    cols = [f for f in fields if widths[f] > 0]
    out: dict[int, str] = {}
    if not cols:
        return out
    for it in rows:
        vals = [it.fields[_FIELD_INDEX[f]] for f in cols]
        if not any(vals):
            continue
        parts = [v.ljust(widths[f]) for f, v in zip(cols[:-1], vals[:-1])] + [vals[-1]]
        out[id(it)] = ("// " + " ".join(parts)).rstrip()
    return out


def _render(lst: _List, lineup: bool, fields: tuple[str, ...], cfg) -> Optional[str]:
    items = lst.items
    pc_texts = _port_comments(items, fields) if fields else {}
    if not lineup and not pc_texts:
        return None
    pc_width = max((len(pc) for pc in pc_texts.values()), default=0)

    first = items[0]
    if not first.newline_before:
        dot_col = _vcol(lst.lead + lst.gap0)
    elif lst.auto_first:
        # AUTOINST regenerates its pins one column after "(" on every run.
        dot_col = _vcol(lst.lead)
    else:
        dot_col = _vcol(first.prefix)
    indent = " " * dot_col

    paren_col = 0
    if lineup:
        heads = [len(it.name) + 1 for it in items if it.conn is not None]
        if heads:
            paren_col = max(cfg.auto_inst_column or 0,
                            16 + 8 * ((dot_col + 7) // 8),
                            dot_col + max(heads) + 1)

    def other(line: str) -> str:
        """A comment-only or blank line inside the list."""
        if lineup:
            return indent + line.strip() if line.strip() else ""
        return line.rstrip()

    # entries: [body, item or None, on the paren line]
    entries: list[list] = []
    if lst.head is not None:
        entries.append([lst.head, None, True])
    for k, it in enumerate(items):
        for line in it.before:
            entries.append([other(line), None, False])
        on_paren = k == 0 and not it.newline_before
        if on_paren:
            ind = lst.gap0
        elif lineup or not it.newline_before:
            ind = indent
        else:
            ind = it.prefix
        if lineup:
            name = "." + it.name if it.name is not None else ".*"
            if it.conn is not None:
                code = name + " " * (paren_col - dot_col - len(name)) + it.conn
            elif it.name is not None and it.name.startswith("\\"):
                code = name + " "
            else:
                code = name
        else:
            code = it.raw
        if k < len(items) - 1:
            sep = "," if lineup else it.sep
        elif lst.close_code:
            sep = ")" + lst.close_code[1:].strip() if lineup else it.sep + lst.close_code
        else:
            sep = "" if lineup else it.sep
        body = ind + code + sep
        if it.block:
            body += " " + it.block
        entries.append([body, it, on_paren])
    for line in lst.after:
        entries.append([other(line), None, False])
    if lst.close_prefix is not None:
        entries.append([lst.close_prefix, None, False])

    def end_col(body: str, on_paren: bool) -> int:
        return _vcol((lst.lead if on_paren else "") + body)

    def comment_of(it: _Item) -> str:
        pc = pc_texts.get(id(it))
        if pc is None:
            return it.comment
        rest = it.comment
        if rest:
            segs = _segments(rest)
            kept = [s for s in segs if not is_port_comment(s, fields, it.rtype)]
            if len(kept) != len(segs):
                rest = SEGMENT_GAP.join(kept)
        if not rest:
            return pc
        # pad to the widest port comment so later segments line up too
        return pc.ljust(pc_width) + SEGMENT_GAP + rest

    # Pins with a trailing block comment (``/*AUTOINST*/`` after a pin) do
    # not push the comment column; they get their comment one space after.
    ends = [end_col(body, on_paren) for body, it, on_paren in entries
            if it is not None and not it.block]
    if not ends:
        ends = [end_col(body, on_paren) for body, it, on_paren in entries if it is not None]
    comment_col = max(cfg.auto_inst_comment_column or 0, max(ends) + MIN_COMMENT_GAP)

    out: list[str] = []
    for body, it, on_paren in entries:
        if it is not None:
            comment = comment_of(it)
            if comment:
                body = body + " " * max(1, comment_col - end_col(body, on_paren)) + comment
        out.append(body)
    return "\n".join(out)


def _format_list(text: str, masked: str, sf: SourceFile, open_: int, close: int,
                 is_param: bool, decls, lineup: bool, fields: tuple[str, ...], cfg,
                 lead: Optional[str] = None):
    try:
        lst = _parse_list(text, masked, sf, open_, close, lead)
    except _Skip:
        return None
    if fields and decls is not None:
        for it in lst.items:
            if it.name is not None:
                found = _port_fields(decls, it.name, is_param)
                if found is not None:
                    it.fields, it.rtype = found
    new = _render(lst, lineup, fields, cfg)
    start, end = lst.region
    if new is None or new == text[start:end]:
        return None
    return start, end, new


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def lineup_instances(
    text: str,
    lookup: Callable[[str], Optional["ModDecls"]],
    cfg: "VerilogConfig",
) -> str:
    """Align instance pins and add port comments; see the module docstring."""
    lineup = bool(getattr(cfg, "auto_inst_lineup", False))
    fields = port_comment_fields(getattr(cfg, "auto_inst_port_comment", None))
    if not lineup and not fields:
        return text
    sf = SourceFile.from_text("<inst_lineup>", text)
    if sf.text != text:  # not LF text: leave it alone
        return text
    masked = mask_comments_and_strings(text)

    cache: dict[str, object] = {}
    edits: list[tuple[int, int, str]] = []
    for inst in find_instantiations(masked, 0, len(masked)):
        decls = None
        if fields:
            if inst.type_name not in cache:
                cache[inst.type_name] = lookup(inst.type_name)
            decls = cache[inst.type_name]
        lists: list[tuple[int, int, bool]] = []
        if inst.params_span is not None:
            ps, pe = inst.params_span
            p_open = masked.find("(", ps, pe)
            p_close = pe - 1
            if p_open >= 0 and matching_close(masked, p_open, pe) == p_close:
                lists.append((p_open, p_close, True))
        lists.append((inst.open_paren, inst.close_paren, False))
        param_edit = None
        for open_, close, is_param in lists:
            lead = None
            if not is_param and param_edit is not None and "\n" not in text[param_edit[1]:open_]:
                # The #( edit ends on the port list's paren line: measure the
                # port list against the rewritten line.
                ps, pe, pnew = param_edit
                line = text[_line_start(text, ps):ps] + pnew + text[pe:open_ + 1]
                lead = line[line.rfind("\n") + 1:]
            edit = _format_list(text, masked, sf, open_, close, is_param, decls, lineup, fields,
                                cfg, lead)
            if edit is not None:
                edits.append(edit)
                if is_param:
                    param_edit = edit

    if not edits:
        return text
    edits.sort()
    pieces: list[str] = []
    pos = 0
    for start, end, new in edits:
        if start < pos:  # overlapping edit (should not happen): keep the earlier one
            continue
        pieces.append(text[pos:start])
        pieces.append(new)
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)
