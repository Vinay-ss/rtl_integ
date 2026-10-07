"""Strip mode: remove every AUTO attribute, keep the generated code.

Pipeline order::

    AUTO expansion (unchanged elisp order)
      -> instance lineup/comments  (end of AutoEngine.run, only when enabled)
      -> [route edits, optional --then-expand]
      -> strip  (ONCE, last, after all passes, over every processed source file)

* Strip can never run inside ``AutoEngine.run``: ``SubDeclParser`` reads pin
  directions from the ``// Outputs`` headers and the routing fence guard
  (``integ/edits.py``) needs the fences.
* A stripped file cannot be expanded again (by design, for the preprocessor
  flow).
* Strip is a mode, not a config field: ``--strip-autos`` on ``expand``,
  ``diff``, ``integrate`` and ``route``; the ``strip FILE...`` subcommand;
  ``Design.strip_autos(files=None)``; ``Design.expand_all(..., strip_autos=True)``.
  With ``--dry-run`` / ``--diff`` the diff shows the result after stripping.

Function contract
-----------------
``strip_autos(text: str) -> str``

* Pure and idempotent (``strip_autos(strip_autos(x)) == strip_autos(x)``).
* Keeps CRLF when given CRLF (callers read/write with ``newline=""``).
* Acts only on **real** comments (a ``/*...*/`` or ``//`` span not inside
  another comment or a string).  A marker written inside a ``//`` comment is
  inactive and must survive.
* Matches a fixed list of marker names, case-insensitively, allowing inner
  spaces; no wildcard (``/*AUTOnotARG*/`` survives).

Removes:

* marker comments (AUTOARG, AUTOINST, AUTOINSTPARAM, AUTOSENSE, AS, AUTOWIRE,
  AUTOLOGIC, AUTOREG, AUTOREGINPUT, AUTOINPUT, AUTOOUTPUT, AUTOINOUT,
  AUTOOUTPUTEVERY, AUTOINOUTMODULE, AUTOINOUTCOMP, AUTOINOUTIN,
  AUTOINOUTPARAM, AUTOINOUTMODPORT, AUTOASSIGNMODPORT, AUTOTIEOFF,
  AUTOUNUSED, AUTOUNDEF, AUTORESET, AUTOASCIIENUM(...), AUTOINSERTLISP(...),
  AUTOINSERTLAST(...)) plus ``/*memory or*/``: the whole line when the
  comment is alone on it, otherwise only the comment (keeping one space
  between identifier characters);
* the fence lines ``// Beginning of auto...`` and ``// End of automatics``
  (the content between them is kept);
* real ``AUTO_TEMPLATE`` comment blocks; ``AUTO_LISP(...)``,
  ``AUTO_CONSTANT(...)``, ``/* auto enum X */`` tags, ``AUTONOHOOKUP`` tokens;
* ``//auto_route`` and ``/* auto_route */`` annotations;
* the ``// Outputs|Inputs|Inouts|Interfaces|Interfaced|Parameters`` header
  lines inside instance and AUTOARG lists;
* the ``// Templated...``, ``// Implicit .*`` and ``// routed: ...``
  trailing segments;
* the Local Variables block (``//`` and ``/* */`` forms) and the
  ``-*- mode: Verilog ... -*-`` cookie;
* the ``.*,`` / ``.*`` token, only in instances that have expanded
  ``// Implicit .*`` pins (delete the token and fix commas).

Keeps: port comments (``// <dir> <width> <type>``, see
``auto/inst_lineup.py``), ``// To/From ...`` provenance comments, user
comments and all code.

Implementation notes
--------------------
* A small tokenizer (``_scan``) finds the real comments, skipping strings and
  escaped identifiers; it also tracks code parens so that section headers are
  only removed inside a paren group that holds an AUTOINST / AUTOINSTPARAM /
  AUTOARG marker or a ``.*`` token, and so that ``.*`` is only removed when
  its group has active ``// Implicit .*`` pins.
* Trailing ``// Templated`` / ``// Implicit .*`` / ``// routed:`` /
  ``// AUTONOHOOKUP`` segments (and ``AUTONOHOOKUP`` tokens) are removed only
  from comments that follow code on their line (pin lines), so commented-out
  code and ``//``-commented template copies are left alone.
* Removed spans become a sentinel; a line left with only whitespace is dropped
  (no new doubled blank line, no blank line at the start or end of the file),
  otherwise the gap is closed with at most one space, and only where two
  identifier characters (or the original spacing) need it.
* ``find_active_markers`` reports what ``strip_autos`` would still remove (it
  returns ``[]`` for stripped text); offsets refer to the LF-normalized text.
"""

from __future__ import annotations

import bisect
import re
from typing import NamedTuple, Optional

__all__ = ["MARKER_NAMES", "find_active_markers", "strip_autos"]

#: AUTO marker comments (``/*NAME*/`` or ``/*NAME(args)*/``), case-insensitive.
MARKER_NAMES = (
    "AUTOARG", "AUTOINST", "AUTOINSTPARAM", "AUTOSENSE", "AS", "AUTOWIRE",
    "AUTOLOGIC", "AUTOREG", "AUTOREGINPUT", "AUTOINPUT", "AUTOOUTPUT",
    "AUTOINOUT", "AUTOOUTPUTEVERY", "AUTOINOUTMODULE", "AUTOINOUTCOMP",
    "AUTOINOUTIN", "AUTOINOUTPARAM", "AUTOINOUTMODPORT", "AUTOASSIGNMODPORT",
    "AUTOTIEOFF", "AUTOUNUSED", "AUTOUNDEF", "AUTORESET", "AUTOASCIIENUM",
    "AUTOINSERTLISP", "AUTOINSERTLAST",
)
# markers whose expansion is a paren list with "// Outputs"-style headers
_LIST_MARKERS = frozenset({"AUTOARG", "AUTOINST", "AUTOINSTPARAM"})

_MARKER_RE = re.compile(
    r"/\*\s*(?:(?P<name>"
    + "|".join(sorted(MARKER_NAMES, key=len, reverse=True))
    + r")(?:\s*\(.*\))?|memory\s+or)\s*\*/",
    re.I | re.S,
)
# a "MODULE AUTO_TEMPLATE" header line (optional "regexp"), then "(" or the end of the line
_TEMPLATE_RE = re.compile(
    r'^[ \t]*(?:/\*)?[ \t]*[A-Za-z_][\w$]*\s+AUTO_TEMPLATE\b[ \t]*(?:"(?:[^"\\\n]|\\.)*")?\s*(?:\(|$)',
    re.M,
)
_LISP_RE = re.compile(r"\b(?:AUTO_LISP|AUTO_CONSTANT)\s*\(", re.I)
_ENUM_RE = re.compile(r"(?://|/\*)\s*auto\s+enum\s+[\w$]+\s*(?:\*/)?", re.I)
_ROUTE_RE = re.compile(r"(?://|/\*)\s*auto_route\b", re.I)
_FENCE_RE = re.compile(r"//\s*(?:Beginning of auto(?:matic|reset)\b|End of automatics\b)")
_HEADER_RE = re.compile(r"//\s*(?:Outputs|Inputs|Inouts|Interfaces|Interfaced|Parameters)\s*")
_SEG_DROP_RE = re.compile(
    r"//\s*(?:Templated(?:\s+[LT]?\d+|\s+LHS:.*)?(?:\s+AUTONOHOOKUP)?"
    r"|Implicit\s+\.\*|routed:.*|AUTONOHOOKUP)\s*",
    re.S,
)
_IMPLICIT_SEG_RE = re.compile(r"//\s*Implicit\s+\.\*\s*")
_NOHOOKUP_RE = re.compile(r"[ \t]*\bAUTONOHOOKUP\b")
_LV_START_RE = re.compile(r"//\s*Local\s+Variables\s*:\s*", re.I)
_LV_END_RE = re.compile(r"//\s*End\s*:\s*", re.I)
_LV_BLOCK_RE = re.compile(r"Local\s+Variables\s*:.*?\bEnd\s*:", re.I | re.S)
_COOKIE_RE = re.compile(r"[ \t]*-\*-(?P<body>.*?)-\*-")
_EMPTY_LINE_CMT_RE = re.compile(r"//\s*")
_EMPTY_BLOCK_CMT_RE = re.compile(r"/\*\s*\*/")

_IDENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")
_SENTINELS = ("\x00", "", "", "")


class _Comment(NamedTuple):
    start: int
    end: int      # line comments end at their newline (exclusive)
    block: bool
    paren: int    # offset of the innermost open code paren, -1 at top level


class _Scan(NamedTuple):
    comments: list[_Comment]
    stars: list[tuple[int, int]]   # (offset of ".*", innermost open paren)
    close: dict[int, int]          # open paren offset -> close paren offset
    mask: str                      # text with comment characters blanked


def _scan(text: str) -> _Scan:
    """Tokenize just enough to find real comments, strings and code parens."""
    n = len(text)
    comments: list[_Comment] = []
    stars: list[tuple[int, int]] = []
    close: dict[int, int] = {}
    stack: list[int] = []
    i = 0
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] in "/*":
            top = stack[-1] if stack else -1
            if text[i + 1] == "/":
                j = text.find("\n", i)
                j = n if j < 0 else j
                comments.append(_Comment(i, j, False, top))
            else:
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
                comments.append(_Comment(i, j, True, top))
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            i = min(j + 1, n) if j < n and text[j] == '"' else min(j, n)
            continue
        if ch == "\\":  # escaped identifier: runs to the next whitespace
            j = i + 1
            while j < n and not text[j].isspace():
                j += 1
            i = j
            continue
        if ch == "(":
            stack.append(i)
        elif ch == ")":
            if stack:
                close[stack.pop()] = i
        elif ch == "." and i + 1 < n and text[i + 1] == "*":
            stars.append((i, stack[-1] if stack else -1))
        i += 1
    parts: list[str] = []
    last = 0
    for c in comments:
        parts.append(text[last:c.start])
        parts.append(re.sub(r"[^\n]", " ", text[c.start:c.end]))
        last = c.end
    parts.append(text[last:])
    return _Scan(comments, stars, close, "".join(parts))


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    j = text.find("\n", pos)
    return len(text) if j < 0 else j


def _alone(text: str, c: _Comment) -> bool:
    """True when only whitespace surrounds comment *c* on its line(s)."""
    return (not text[_line_start(text, c.start):c.start].strip()
            and not text[c.end:_line_end(text, c.end)].strip())


def _match_paren(s: str, i: int, limit: int) -> Optional[int]:
    """Offset after the paren matching ``s[i] == '('`` (Lisp strings and
    ``\\`` escapes skipped), or ``None``."""
    depth = 0
    j = i
    while j < limit:
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == '"':
            j += 1
            while j < limit and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def _strip_cookie(body: str) -> str:
    """Remove a ``-*- ... Verilog ... -*-`` cookie from the first line of *body*."""
    nl = body.find("\n")
    m = _COOKIE_RE.search(body, 0, len(body) if nl < 0 else nl)
    if m and "verilog" in m.group("body").lower():
        return body[:m.start()] + body[m.end():]
    return body


def _line_segments(body: str) -> list[tuple[str, str]]:
    """Split a ``//`` comment into ``(segment, trailing_ws)`` at each ``//``."""
    pos = [m.start() for m in re.finditer("//", body)]
    out = []
    for a, b in zip(pos, pos[1:] + [len(body)]):
        raw = body[a:b]
        seg = raw.rstrip(" \t")
        out.append((seg, raw[len(seg):]))
    return out


def _strip_segments(body: str) -> tuple[str, bool]:
    """Drop the Templated / Implicit .* / routed: / AUTONOHOOKUP segments of a
    pin-line comment.  Returns ``(new_body, has_implicit)``."""
    out = ""
    gap = ""
    implicit = False
    for seg, trail in _line_segments(body):
        new: Optional[str] = seg
        if _SEG_DROP_RE.fullmatch(seg):
            implicit = implicit or bool(_IMPLICIT_SEG_RE.fullmatch(seg))
            new = None
        else:
            stripped = _NOHOOKUP_RE.sub("", seg)
            if stripped != seg:
                new = None if _EMPTY_LINE_CMT_RE.fullmatch(stripped) else stripped
        if new is not None:
            out += (gap if out else "") + new
        gap = trail
    if out == body.rstrip(" \t"):
        return body, implicit
    return out, implicit


def _block_edit(body: str, on_first_line: bool) -> tuple[Optional[str], Optional[str]]:
    """Return ``(new_body, kind)`` for a block comment; ``new_body`` is
    ``None`` to delete it, ``kind`` is ``None`` when nothing changes."""
    if _MARKER_RE.fullmatch(body):
        return None, "marker"
    if _TEMPLATE_RE.search(body):
        return None, "auto_template"
    if _ROUTE_RE.match(body):
        return None, "auto_route"
    if _ENUM_RE.fullmatch(body):
        return None, "auto_enum"
    new = body
    kind = None
    m = _LV_BLOCK_RE.search(new)
    if m:
        new = new[:m.start()] + new[m.end():]
        kind = "local_variables"
    limit = len(new) - 2 if new.endswith("*/") else len(new)
    m = _LISP_RE.search(new, 0, limit)
    while m:
        end = _match_paren(new, m.end() - 1, limit)
        end = limit if end is None else end
        kind = kind or ("auto_lisp" if "LISP" in m.group(0).upper() else "auto_constant")
        new = new[:m.start()] + new[end:]
        limit = len(new) - 2 if new.endswith("*/") else len(new)
        m = _LISP_RE.search(new, m.start(), limit)
    stripped = _NOHOOKUP_RE.sub("", new)
    if stripped != new:
        new, kind = stripped, kind or "autonohookup"
    if on_first_line:
        stripped = _strip_cookie(new)
        if stripped != new:
            new, kind = stripped, kind or "mode_cookie"
    if kind is None:
        return body, None
    return (None if not new or _EMPTY_BLOCK_CMT_RE.fullmatch(new) else new), kind


def _line_edit(body: str, on_first_line: bool, code_before: bool,
               in_list: bool) -> tuple[Optional[str], Optional[str], bool]:
    """Return ``(new_body, kind, has_implicit)`` for a ``//`` comment."""
    if _FENCE_RE.match(body):
        return None, "fence", False
    if _ROUTE_RE.match(body):
        return None, "auto_route", False
    if _ENUM_RE.fullmatch(body):
        return None, "auto_enum", False
    if in_list and _HEADER_RE.fullmatch(body):
        return None, "header", False
    new = body
    kind = None
    implicit = False
    if on_first_line:
        stripped = _strip_cookie(new)
        if stripped != new:
            new, kind = stripped, "mode_cookie"
    if code_before:
        stripped, implicit = _strip_segments(new)
        if stripped != new:
            new, kind = stripped, kind or "pin_comment"
    if kind is None:
        return body, None, implicit
    return (None if not new or _EMPTY_LINE_CMT_RE.fullmatch(new) else new), kind, implicit


def _is_star_token(mask: str, p: int) -> bool:
    """``.*`` used as a port connection: after ``(`` / ``,`` and before ``,`` / ``)``."""
    j = p - 1
    while j >= 0 and mask[j].isspace():
        j -= 1
    k = p + 2
    while k < len(mask) and mask[k].isspace():
        k += 1
    return j >= 0 and mask[j] in "(," and k < len(mask) and mask[k] in ",)"


_Edit = tuple[int, int, Optional[str], str]   # start, end, replacement (None: delete), kind


def _analyze(text: str) -> list[_Edit]:
    """Plan every removal on LF *text*."""
    sc = _scan(text)
    comments, mask = sc.comments, sc.mask
    nl = text.find("\n")
    first_line_end = len(text) if nl < 0 else nl

    # paren groups whose headers are AUTO output: AUTOINST/AUTOINSTPARAM/AUTOARG lists, .* lists
    list_groups: set[int] = set()
    for c in comments:
        if c.block:
            m = _MARKER_RE.fullmatch(text, c.start, c.end)
            if m and m.group("name") and m.group("name").upper() in _LIST_MARKERS:
                list_groups.add(c.paren)
    stars = [(p, o) for p, o in sc.stars if o >= 0 and _is_star_token(mask, p)]
    list_groups.update(o for _, o in stars)
    list_groups.discard(-1)

    # "// Local Variables:" ... "// End:" runs of whole-line comments
    lv: set[int] = set()
    k = 0
    while k < len(comments):
        c = comments[k]
        if not c.block and _alone(text, c) and _LV_START_RE.fullmatch(text, c.start, c.end):
            j = k
            while j + 1 < len(comments):
                prev, nxt = comments[j], comments[j + 1]
                if nxt.block or not _alone(text, nxt) or _line_start(text, nxt.start) != prev.end + 1:
                    break
                j += 1
                if _LV_END_RE.fullmatch(text, nxt.start, nxt.end):
                    lv.update(range(k, j + 1))
                    break
            k = j + 1 if k in lv else k + 1
            continue
        k += 1

    edits: list[_Edit] = []
    implicit_at: list[int] = []
    for idx, c in enumerate(comments):
        if idx in lv:
            edits.append((c.start, c.end, None, "local_variables"))
            continue
        body = text[c.start:c.end]
        on_first = c.start < first_line_end
        if c.block:
            new, kind = _block_edit(body, on_first)
        else:
            code_before = bool(mask[_line_start(text, c.start):c.start].strip())
            new, kind, implicit = _line_edit(body, on_first, code_before, c.paren in list_groups)
            if implicit:
                implicit_at.append(c.start)
        if kind is not None:
            edits.append((c.start, c.end, new, kind))

    # .* in instances that carry expanded "// Implicit .*" pins
    for p, o in stars:
        cl = sc.close.get(o)
        hi = _line_end(text, cl) if cl is not None else len(text)
        i = bisect.bisect_right(implicit_at, o)
        if i >= len(implicit_at) or implicit_at[i] >= hi:
            continue
        edits.append((p, p + 2, None, "auto_star"))
        j = p + 2
        while j < len(mask) and mask[j].isspace():
            j += 1
        if j < len(mask) and mask[j] == ",":
            edits.append((j, j + 1, None, "auto_star"))
            continue
        j = p - 1
        while j >= 0 and mask[j].isspace():
            j -= 1
        if j >= 0 and mask[j] == ",":
            edits.append((j, j + 1, None, "auto_star"))
    edits.sort()
    return edits


def _close_gap(line: str, s: str) -> str:
    """Remove the sentinels *s* from a line that keeps other content."""
    line = re.sub(re.escape(s) + r"(?:[ \t]*" + re.escape(s) + ")+", s, line)
    while True:
        k = line.find(s)
        if k < 0:
            return line
        a = k
        while a > 0 and line[a - 1] in " \t":
            a -= 1
        b = k + 1
        while b < len(line) and line[b] in " \t":
            b += 1
        left, right = line[a:k], line[k + 1:b]
        p = line[a - 1] if a > 0 else None
        q = line[b] if b < len(line) else None
        if q is None:                      # removed at end of line: no trailing ws
            rep = ""
        elif p is None:                    # removed at start of line: keep indentation
            rep = left
        elif q == "/" and line[b + 1:b + 2] in ("/", "*"):
            rep = " " if (left or right) else ""
        elif p in "([{" or q in ")]},;":
            rep = ""
        elif left or right:
            rep = left or " "
        elif p in _IDENT_CHARS and q in _IDENT_CHARS:
            rep = " "
        else:
            rep = ""
        line = line[:a] + rep + line[b:]


def _finish(text: str, s: str) -> str:
    """Drop lines emptied by removals and close the remaining gaps."""
    ends_nl = text.endswith("\n")
    lines = text.split("\n")
    if ends_nl:
        lines.pop()
    out: list[str] = []
    pending = False   # just dropped a run of lines
    for line in lines:
        if s in line:
            if not line.replace(s, "").strip():
                pending = True
                continue
            line = _close_gap(line, s)
        if pending:
            pending = False
            if not line.strip() and (not out or not out[-1].strip()):
                continue   # no doubled blank line, no blank line at the top
        out.append(line)
    if pending and out and not out[-1].strip():
        out.pop()          # no blank line at the end
    return "\n".join(out) + ("\n" if ends_nl and out else "")


def find_active_markers(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, kind)`` for every AUTO attribute that
    :func:`strip_autos` would remove or shorten (offsets refer to the
    LF-normalized text).  Empty for stripped text."""
    return [(a, b, kind) for a, b, _, kind in _analyze(text.replace("\r\n", "\n"))]


def strip_autos(text: str) -> str:
    """Remove every AUTO attribute from *text*; see the module docstring."""
    crlf = "\r\n" in text
    t = text.replace("\r\n", "\n") if crlf else text
    edits = _analyze(t)
    if not edits:
        return text
    s = next(c for c in _SENTINELS if c not in t)
    parts: list[str] = []
    last = 0
    for a, b, new, _ in edits:
        parts.append(t[last:a])
        parts.append(s if new is None else new)
        last = b
    parts.append(t[last:])
    out = _finish("".join(parts), s)
    return out.replace("\n", "\r\n") if crlf else out
