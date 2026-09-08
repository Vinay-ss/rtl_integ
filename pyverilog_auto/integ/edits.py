"""Text edits for routing: planned in char offsets, applied on bytes.

* Edits are planned against ``SourceFile.text`` (char offsets, LF newlines)
  and applied to ``SourceFile.data`` through the file's byte<->char map, so
  CRLF and non-ASCII files keep their bytes untouched outside the edits.
* All edits of a run are validated first (overlaps, stale files, fences);
  application is per file, highest offset first, so earlier offsets stay
  valid.
* Inserted text uses the file's own end-of-line sequence.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional

from .model import MarkerInfo, ModuleDef, SrcRange
from .sources import SourceFile

if TYPE_CHECKING:
    from .design import Design


class EditError(RuntimeError):
    pass


@dataclass(frozen=True)
class TextEdit:
    """Replace ``text[start:end]`` with *text* (insertion when start == end)."""

    file: str                 # SourceFile.key
    start: int                # char offset
    end: int
    text: str
    seq: int = 0              # planning order, breaks ties at equal offsets
    kind: str = ""            # ansi_port | body_port | arglist_name | pin | pin_unconnected | net | iface_inst | template
    module: str = ""
    route: str = ""
    description: str = ""
    key: tuple = ()           # idempotency key

    @property
    def is_insertion(self) -> bool:
        return self.start == self.end


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------

def line_start(text: str, offset: int) -> int:
    nl = text.rfind("\n", 0, offset)
    return 0 if nl < 0 else nl + 1


def line_end(text: str, offset: int) -> int:
    nl = text.find("\n", offset)
    return len(text) if nl < 0 else nl


def line_indent(text: str, offset: int) -> str:
    """Leading whitespace of the line containing *offset*."""
    s = line_start(text, offset)
    m = re.match(r"[ \t]*", text[s:line_end(text, offset)])
    return m.group(0) if m else ""


def column_of(text: str, offset: int, tabsize: int = 8) -> int:
    """Display column of *offset* on its line (tabs expand to *tabsize*)."""
    s = line_start(text, offset)
    col = 0
    for ch in text[s:offset]:
        col = (col // tabsize + 1) * tabsize if ch == "\t" else col + 1
    return col


def pad_to(s: str, col: int) -> str:
    return s + " " * max(1, col - len(s))


def fence_spans_of(mod: ModuleDef) -> list[tuple[int, int]]:
    spans = [(mi.fence_range.start, mi.fence_range.end)
             for lst in mod.markers.values() for mi in lst if mi.fence_range is not None]
    spans.sort()
    return spans


def inside_fence(offset: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if s <= offset < e:
            return True
        if s > offset:
            break
    return False


# ----------------------------------------------------------------------
# List-entry insertion (port lists, connection lists, name lists)
# ----------------------------------------------------------------------

def _entries_before(text: str, list_range: SrcRange, limit: int) -> list[tuple[int, int]]:
    """Top-level entries (char spans, stripped) between the opening paren and *limit*.

    Comments are treated as part of whitespace; markers are comments too, so
    *limit* must be the start of the marker when an anchor is given.
    """
    inner_start = list_range.start + 1
    seg = text[inner_start:limit]
    masked = _mask(seg)
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for i, c in enumerate(masked):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(masked)))
    out: list[tuple[int, int]] = []
    for s, e in spans:
        piece = masked[s:e]
        lead = len(piece) - len(piece.lstrip())
        trail = len(piece) - len(piece.rstrip())
        if piece.strip():
            out.append((inner_start + s + lead, inner_start + e - trail))
    return out


def _mask(text: str) -> str:
    """Blank comments (keeps offsets)."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _fmt_entry(entry: str, pad_col: Optional[int]) -> str:
    if pad_col is not None and "(" in entry:
        head, _, rest = entry.partition("(")
        return pad_to(head.rstrip(), pad_col) + "(" + rest
    return entry


def insert_list_entries(
    sf: SourceFile,
    list_range: SrcRange,
    entries: list[tuple[str, Optional[str]]],
    *,
    anchor: Optional[SrcRange] = None,
    followers_nonempty: bool = False,
    pad_col: Optional[int] = None,
    **meta,
) -> list[TextEdit]:
    """Insert *entries* (``(text, comment)`` pairs) into the parenthesised
    list *list_range* as one block.

    * With *anchor* (an AUTO marker inside the list) the block goes on its own
      lines right before the marker; the last entry ends with ``,`` iff
      *followers_nonempty* (a real entry follows, or the marker's expansion
      will be non-empty).  If the marker shares its line with the opening
      paren, the marker is moved to its own line first.
    * Without *anchor* the block is appended before the closing paren; the
      previous entry gets a ``,`` if it lacks one, and the paren moves to its
      own line when it shared a line with the last entry.
    """
    if not entries:
        return []
    text = sf.text
    edits: list[TextEdit] = []
    close = list_range.end - 1
    assert text[list_range.start] == "(" and text[close] == ")", "list_range must span ( ... )"

    def lines_for(indent: str, trailing_comma: bool) -> str:
        out = []
        n = len(entries)
        for i, (entry, comment) in enumerate(entries):
            e = _fmt_entry(entry, pad_col)
            comma = "," if (i < n - 1 or trailing_comma) else ""
            tail = f"   {comment}" if comment else ""
            out.append(indent + e + comma + tail)
        return "\n".join(out)

    if anchor is not None:
        limit = anchor.start
        prev_entries = _entries_before(text, list_range, limit)
        if prev_entries:
            ps, pe = prev_entries[-1]
            between = _mask(text[pe:limit])
            if "," not in between:
                edits.append(TextEdit(sf.key, pe, pe, ",", seq=0, kind="comma", **meta))
        ls = line_start(text, anchor.start)
        if ls <= list_range.start:
            # `sub u (/*AUTOINST*/` -> marker moves to its own line, indented
            # like AUTOINST does (column after the paren).
            indent = " " * (column_of(text, list_range.start) + 1)
            ins = "\n" + lines_for(indent, followers_nonempty) + "\n" + indent
            edits.append(TextEdit(sf.key, anchor.start, anchor.start, ins, seq=1, **meta))
        else:
            indent = line_indent(text, anchor.start)
            ins = lines_for(indent, followers_nonempty) + "\n"
            edits.append(TextEdit(sf.key, ls, ls, ins, seq=1, **meta))
        return edits

    prev_entries = _entries_before(text, list_range, close)
    paren_indent = " " * (column_of(text, list_range.start) + 1)
    if prev_entries:
        ps, pe = prev_entries[-1]
        between = _mask(text[pe:close])
        comma_at = between.find(",")
        if comma_at >= 0:
            at = pe + comma_at + 1          # right after the existing comma
            lead = ""
        else:
            at = pe                          # right after the entry: add the comma ourselves
            lead = ","
        indent = paren_indent if line_start(text, ps) <= list_range.start else line_indent(text, ps)
        ins = lead + "\n" + lines_for(indent, False)
        if "\n" not in _mask(text[at:close]):
            ins += "\n" + indent[:-1]        # move the closing paren to its own line
        edits.append(TextEdit(sf.key, at, at, ins, seq=1, **meta))
    else:
        ins = "\n" + lines_for(paren_indent, False) + "\n" + paren_indent[:-1]
        edits.append(TextEdit(sf.key, close, close, ins, seq=1, **meta))
    return edits


def insert_list_entry(
    sf: SourceFile,
    list_range: SrcRange,
    entry: str,
    *,
    anchor: Optional[SrcRange] = None,
    followers_nonempty: bool = False,
    comment: Optional[str] = None,
    pad_col: Optional[int] = None,
    **meta,
) -> list[TextEdit]:
    """Single-entry convenience wrapper around :func:`insert_list_entries`."""
    return insert_list_entries(sf, list_range, [(entry, comment)], anchor=anchor,
                               followers_nonempty=followers_nonempty, pad_col=pad_col, **meta)


def insert_lines_before(sf: SourceFile, offset: int, lines: Iterable[str], indent: Optional[str] = None, **meta) -> TextEdit:
    """Insert whole lines before the line containing *offset*."""
    text = sf.text
    ls = line_start(text, offset)
    ind = line_indent(text, offset) if indent is None else indent
    body = "".join(ind + ln + "\n" for ln in lines)
    return TextEdit(sf.key, ls, ls, body, **meta)


def insert_lines_after(sf: SourceFile, offset: int, lines: Iterable[str], indent: Optional[str] = None, **meta) -> TextEdit:
    """Insert whole lines after the line containing *offset*."""
    text = sf.text
    le = line_end(text, offset)
    ind = line_indent(text, offset) if indent is None else indent
    body = "".join("\n" + ind + ln for ln in lines)
    return TextEdit(sf.key, le, le, body, **meta)


# ----------------------------------------------------------------------
# Edit sets
# ----------------------------------------------------------------------

@dataclass
class EditSet:
    design: "Design"
    edits: list[TextEdit] = field(default_factory=list)

    def add(self, *edits: TextEdit) -> None:
        for e in edits:
            self.edits.append(e)

    def extend(self, edits: Iterable[TextEdit]) -> None:
        self.edits.extend(edits)

    def by_file(self) -> dict[str, list[TextEdit]]:
        out: dict[str, list[TextEdit]] = {}
        for e in self.edits:
            out.setdefault(e.file, []).append(e)
        return out

    # -- validation ---------------------------------------------------

    def check(self, fence_spans: Optional[dict[str, list[tuple[int, int]]]] = None) -> None:
        """Raise :class:`EditError` on overlapping ranges, edits inside AUTO
        fences, or files changed on disk since they were loaded."""
        for key, edits in self.by_file().items():
            sf = self.design.files.get(key)
            if sf is None:
                raise EditError(f"edit targets a file that is not in the design: {key}")
            on_disk = sf.on_disk_stamp()
            if on_disk is not None and sf.version == 0 and on_disk != sf.stamp():
                raise EditError(f"{sf.path} changed on disk since it was loaded (E_STALE_FILE)")
            ordered = sorted(edits, key=lambda e: (e.start, e.end, e.seq))
            last_end = -1
            last = None
            for e in ordered:
                if last is not None and e.start < last_end and not (e.is_insertion and last.is_insertion):
                    raise EditError(f"overlapping edits in {sf.path} at {e.start} ({e.description}) and ({last.description})")
                last_end = max(last_end, e.end)
                last = e
                if fence_spans and inside_fence(e.start, fence_spans.get(key, [])) and e.kind != "comma":
                    raise EditError(f"edit inside an AUTO fence in {sf.path} at {e.start}: {e.description}")

    # -- application --------------------------------------------------

    def render(self) -> dict[str, bytes]:
        """New bytes per file (no side effects)."""
        out: dict[str, bytes] = {}
        for key, edits in self.by_file().items():
            sf = self.design.files[key]
            eol = sf.eol
            data = bytearray(sf.data)
            ordered = sorted(edits, key=lambda e: (e.start, e.seq))
            for e in reversed(ordered):
                bs = sf.byte_offset(e.start)
                be = sf.byte_offset(e.end)
                ins = e.text.replace("\n", eol) if eol != "\n" else e.text
                data[bs:be] = ins.encode("utf-8")
            out[key] = bytes(data)
        return out

    def diff(self, rendered: Optional[dict[str, bytes]] = None) -> str:
        rendered = rendered if rendered is not None else self.render()
        chunks: list[str] = []
        for key, new in rendered.items():
            sf = self.design.files[key]
            old_t = sf.text
            new_t = new.decode("utf-8", "replace").replace("\r\n", "\n")
            chunks.append("".join(difflib.unified_diff(
                old_t.splitlines(keepends=True), new_t.splitlines(keepends=True),
                fromfile=f"a/{sf.path}", tofile=f"b/{sf.path}")))
        return "".join(chunks)

    def apply(self, *, dry_run: bool = False) -> list[str]:
        """Apply all edits; returns the paths of changed files.

        Files are written unless *dry_run*; the design's overlay is updated
        either way and the changed files are re-scanned.
        """
        rendered = self.render()
        changed: list[str] = []
        for key, new in rendered.items():
            sf = self.design.files[key]
            if new == sf.data:
                continue
            if not dry_run:
                with open(sf.path, "wb") as fh:
                    fh.write(new)
            sf.update(new.decode("utf-8", "replace"))
            if not dry_run:
                sf.refresh_stamp()
            changed.append(sf.path)
        if changed:
            self.design.refresh(changed)
        return changed
