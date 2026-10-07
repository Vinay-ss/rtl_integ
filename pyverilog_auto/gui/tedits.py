"""Line-based edits of template files (and new files), with a diff preview.

Templates are edited by whole lines: an instantiation statement occupies
template lines ``first..last``.  Files keep their end-of-line convention;
edits are planned against a content hash and refused if the file changed
on disk before they are applied.
"""

from __future__ import annotations

import difflib
import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional


class TemplateEditError(RuntimeError):
    pass


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _LineEdit:
    first: int          # 1-based first line replaced (insertion: line to insert before)
    last: int           # last line replaced; first - 1 for a pure insertion
    lines: list[str]    # replacement lines without end-of-line
    order: int


@dataclass
class _FileState:
    path: str
    lines: list[str]
    eol: str
    trailing_eol: bool
    hash: str
    edits: list[_LineEdit] = field(default_factory=list)
    replace_all: Optional[list[str]] = None


def _read(path: str) -> tuple[list[str], str, bool, str]:
    with open(path, "rb") as fh:
        data = fh.read()
    text = data.decode("utf-8", "surrogateescape")
    crlf = text.count("\r\n")
    eol = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
    body = text.replace("\r\n", "\n")
    trailing = body.endswith("\n")
    lines = body.split("\n")
    if trailing:
        lines.pop()
    return lines, eol, trailing, file_hash(data)


def _longest_blank_run(lines: list[str]) -> int:
    best = run = 0
    for ln in lines:
        run = run + 1 if not ln.strip() else 0
        best = max(best, run)
    return best


def _collapse_blank_runs(lines: list[str], allowed: int) -> list[str]:
    """Shorten runs of blank lines longer than *allowed* (at least 1): edits
    that delete statements must not leave gaps the file did not have."""
    allowed = max(1, allowed)
    out: list[str] = []
    run = 0
    for ln in lines:
        run = run + 1 if not ln.strip() else 0
        if run <= allowed:
            out.append(ln)
    return out


def split_text(text: str) -> list[str]:
    body = text.replace("\r\n", "\n")
    lines = body.split("\n")
    if body.endswith("\n"):
        lines.pop()
    return lines


class TemplateEditSet:
    def __init__(self) -> None:
        self.files: dict[str, _FileState] = {}
        self.new_files: dict[str, str] = {}
        self.removed: set[str] = set()       # normcase paths deleted on apply
        self._seq = 0

    # -- reading ----------------------------------------------------------

    def _state(self, path: str) -> _FileState:
        key = os.path.normcase(os.path.abspath(path))
        st = self.files.get(key)
        if st is None:
            lines, eol, trailing, h = _read(path)
            st = _FileState(os.path.abspath(path), lines, eol, trailing, h)
            self.files[key] = st
        return st

    def lines(self, path: str) -> list[str]:
        return list(self._state(path).lines)

    def line_range_text(self, path: str, first: int, last: int) -> list[str]:
        return self._state(path).lines[first - 1:last]

    # -- planning -----------------------------------------------------------

    def _add(self, path: str, first: int, last: int, lines: list[str]) -> None:
        st = self._state(path)
        if os.path.normcase(st.path) in self.removed:
            return
        n = len(st.lines)
        if not (1 <= first <= n + 1) or last < first - 1 or last > n:
            raise TemplateEditError(f"{path}: bad line range {first}..{last} (file has {n} lines)")
        self._seq += 1
        st.edits.append(_LineEdit(first, last, list(lines), self._seq))

    def replace_lines(self, path: str, first: int, last: int, lines: list[str]) -> None:
        self._add(path, first, last, lines)

    def delete_lines(self, path: str, first: int, last: int) -> None:
        self._add(path, first, last, [])

    def insert_before(self, path: str, line: int, lines: list[str]) -> None:
        self._add(path, line, line - 1, lines)

    def insert_after(self, path: str, line: int, lines: list[str]) -> None:
        self._add(path, line + 1, line, lines)

    def set_text(self, path: str, text: str) -> None:
        st = self._state(path)
        st.replace_all = split_text(text)

    def add_file(self, path: str, text: str) -> None:
        if os.path.exists(path):
            raise TemplateEditError(f"{path} already exists")
        self.new_files[os.path.abspath(path)] = text

    def remove_file(self, path: str) -> None:
        """Delete *path* when applied (its line edits, if any, are dropped)."""
        st = self._state(path)
        st.edits.clear()
        st.replace_all = None
        self.removed.add(os.path.normcase(st.path))

    # -- rendering ------------------------------------------------------------

    def _render_lines(self, st: _FileState) -> list[str]:
        if st.replace_all is not None:
            if st.edits:
                raise TemplateEditError(f"{st.path}: full replacement combined with line edits")
            return list(st.replace_all)
        edits = sorted(st.edits, key=lambda e: (e.first, e.last, e.order))
        prev_end = 0
        for e in edits:
            if e.last >= e.first and e.first <= prev_end:
                raise TemplateEditError(f"{st.path}: overlapping edits at line {e.first}")
            prev_end = max(prev_end, e.last)
        out = list(st.lines)
        for e in sorted(edits, key=lambda e: (e.first, e.order), reverse=True):
            out[e.first - 1:e.last] = e.lines
        return _collapse_blank_runs(out, _longest_blank_run(st.lines))

    def render(self) -> dict[str, str]:
        """New text of every touched file (LF end-of-lines)."""
        out: dict[str, str] = {}
        for st in self.files.values():
            if st.edits or st.replace_all is not None:
                lines = self._render_lines(st)
                out[st.path] = "\n".join(lines) + ("\n" if st.trailing_eol or not lines else "")
        for path, text in self.new_files.items():
            out[path] = text
        return out

    def diff(self, root: Optional[str] = None) -> str:
        chunks: list[str] = []
        rendered = self.render()
        for path, new in rendered.items():
            name = os.path.relpath(path, root).replace("\\", "/") if root else path
            key = os.path.normcase(path)
            if key in self.files:
                st = self.files[key]
                old = "\n".join(st.lines) + ("\n" if st.trailing_eol else "")
                chunks.append("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                           fromfile=f"a/{name}", tofile=f"b/{name}")))
            else:
                chunks.append("".join(difflib.unified_diff([], new.splitlines(keepends=True),
                                                           fromfile="/dev/null", tofile=f"b/{name}")))
        for key in sorted(self.removed):
            st = self.files[key]
            name = os.path.relpath(st.path, root).replace("\\", "/") if root else st.path
            old = "\n".join(st.lines) + ("\n" if st.trailing_eol else "")
            chunks.append("".join(difflib.unified_diff(old.splitlines(keepends=True), [],
                                                       fromfile=f"a/{name}", tofile="/dev/null")))
        return "".join(chunks)

    def _changed(self, st: _FileState) -> bool:
        return bool(st.edits or st.replace_all is not None or os.path.normcase(st.path) in self.removed)

    def touched(self) -> list[str]:
        paths = [st.path for st in self.files.values() if self._changed(st)]
        return paths + list(self.new_files)

    # -- applying ---------------------------------------------------------------

    def check(self) -> None:
        for st in self.files.values():
            if not self._changed(st):
                continue
            try:
                with open(st.path, "rb") as fh:
                    if file_hash(fh.read()) != st.hash:
                        raise TemplateEditError(f"{st.path} changed on disk since the plan was made")
            except FileNotFoundError:
                raise TemplateEditError(f"{st.path} no longer exists") from None
        for path in self.new_files:
            if os.path.exists(path):
                raise TemplateEditError(f"{path} already exists")
        self.render()

    def before_bytes(self) -> dict[str, Optional[bytes]]:
        """Current bytes of every file the apply will write (None: new file)."""
        out: dict[str, Optional[bytes]] = {}
        for st in self.files.values():
            if self._changed(st):
                with open(st.path, "rb") as fh:
                    out[st.path] = fh.read()
        for path in self.new_files:
            out[path] = None
        return out

    def apply(self) -> dict[str, bytes]:
        """Write every file; return the bytes written."""
        self.check()
        written: dict[str, bytes] = {}
        rendered = self.render()
        for path, text in rendered.items():
            key = os.path.normcase(path)
            eol = self.files[key].eol if key in self.files else "\n"
            data = (text.replace("\n", eol) if eol != "\n" else text).encode("utf-8", "surrogateescape")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            written[path] = data
        for key in self.removed:
            os.remove(self.files[key].path)
        return written
