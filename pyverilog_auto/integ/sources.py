"""SourceFile — raw bytes, decoded text and byte<->char offset maps.

pyslang reports byte offsets into the file as loaded; ``VerilogBuffer``
works on the text as returned by ``open(path).read()`` (utf-8 with
replacement, universal newlines).  :class:`SourceFile` keeps both views of
one file and converts offsets between them exactly, including CRLF files
and non-ASCII comments.
"""

from __future__ import annotations

import bisect
import codecs
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Role = Literal["source", "library"]

_EVENT_RE = re.compile(rb"\r\n|[\x80-\xff]+")


def decode_source(data: bytes) -> str:
    """Decode *data* exactly like ``VerilogBuffer.from_file`` does."""
    text = data.decode("utf-8", errors="replace")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


class _OffsetMap:
    """Piecewise-linear byte<->char map for :func:`decode_source` output."""

    __slots__ = ("b", "c", "kind", "nchars")

    def __init__(self, data: bytes) -> None:
        # Segment starts (byte offset, char offset, kind, chars-in-segment)
        self.b: list[int] = [0]
        self.c: list[int] = [0]
        self.kind: list[str] = ["lin"]
        self.nchars: list[int] = [0]
        b = 0
        c = 0
        for m in _EVENT_RE.finditer(data):
            s, e = m.span()
            c += s - b
            b = s
            tok = m.group()
            if tok == b"\r\n":
                # "\r" is dropped by universal newlines: 1 byte -> 0 chars
                self._push(b, c, "cr", 0)
                b += 1
                self._push(b, c, "lin", 0)
                continue
            dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
            start = b
            for i in range(len(tok)):
                out = dec.decode(tok[i:i + 1])
                if out:
                    self._push(start, c, "mb", len(out))
                    c += len(out)
                    start = b + i + 1
            out = dec.decode(b"", final=True)
            if out:
                self._push(start, c, "mb", len(out))
                c += len(out)
                start = e
            b = e
            self._push(b, c, "lin", 0)

    def _push(self, b: int, c: int, kind: str, nchars: int) -> None:
        if self.b and self.b[-1] == b and self.kind[-1] == "lin" and kind == "lin":
            return
        self.b.append(b)
        self.c.append(c)
        self.kind.append(kind)
        self.nchars.append(nchars)

    def char_offset(self, byte_off: int) -> int:
        i = bisect.bisect_right(self.b, byte_off) - 1
        if self.kind[i] == "lin":
            return self.c[i] + (byte_off - self.b[i])
        return self.c[i]

    def byte_offset(self, char_off: int) -> int:
        """First byte of the char at *char_off* (a CRLF newline maps to its ``\\r``)."""
        i = bisect.bisect_left(self.c, char_off)
        if i < len(self.c) and self.c[i] == char_off:
            return self.b[i]
        i -= 1
        if self.kind[i] == "lin":
            return self.b[i] + (char_off - self.c[i])
        return self.b[i]


@dataclass
class SourceFile:
    """One design file: bytes as loaded, decoded text, role and version."""

    path: str                     # absolute, normalized (display + VerilogBuffer filepath)
    key: str                      # os.path.normcase(path): dict key everywhere
    role: Role
    data: bytes
    text: str
    size: int
    mtime_ns: int
    version: int = 0
    tree: Any = None              # pyslang SyntaxTree (slang backend only)
    modules: list[str] = field(default_factory=list)
    _map: Optional[_OffsetMap] = field(default=None, repr=False, compare=False)
    _line_starts: Optional[list[int]] = field(default=None, repr=False, compare=False)
    _identity: bool = field(default=False, repr=False, compare=False)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def read(cls, path: str, role: Role = "source") -> "SourceFile":
        path = os.path.normpath(os.path.abspath(path))
        with open(path, "rb") as fh:
            data = fh.read()
        st = os.stat(path)
        sf = cls(path=path, key=os.path.normcase(path), role=role, data=data,
                 text=decode_source(data), size=st.st_size, mtime_ns=st.st_mtime_ns)
        sf._reset_maps()
        return sf

    @classmethod
    def from_text(cls, path: str, text: str, role: Role = "source") -> "SourceFile":
        """In-memory file (tests, overlays)."""
        path = os.path.normpath(os.path.abspath(path))
        data = text.encode("utf-8")
        sf = cls(path=path, key=os.path.normcase(path), role=role, data=data,
                 text=decode_source(data), size=len(data), mtime_ns=0)
        sf._reset_maps()
        return sf

    def update(self, new_text: str) -> None:
        """Replace the content (overlay after AUTO expansion or an edit).

        *new_text* is encoded as utf-8; the stored text is re-decoded with
        the same newline normalization as :meth:`read` so offsets stay
        consistent.
        """
        self.data = new_text.encode("utf-8")
        self.text = decode_source(self.data)
        self.size = len(self.data)
        self.version += 1
        self.tree = None
        self._reset_maps()

    def refresh_stamp(self) -> None:
        """Re-read size/mtime from disk after writing."""
        try:
            st = os.stat(self.path)
            self.size, self.mtime_ns = st.st_size, st.st_mtime_ns
        except OSError:
            pass

    def stamp(self) -> tuple[int, int]:
        return (self.size, self.mtime_ns)

    def on_disk_stamp(self) -> Optional[tuple[int, int]]:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    # ------------------------------------------------------------------
    # Offsets
    # ------------------------------------------------------------------

    def _reset_maps(self) -> None:
        self._identity = self.data.isascii() and b"\r" not in self.data
        self._map = None
        self._line_starts = None

    def char_offset(self, byte_off: int) -> int:
        if self._identity:
            return byte_off
        if self._map is None:
            self._map = _OffsetMap(self.data)
        return self._map.char_offset(byte_off)

    def byte_offset(self, char_off: int) -> int:
        if self._identity:
            return char_off
        if self._map is None:
            self._map = _OffsetMap(self.data)
        return self._map.byte_offset(char_off)

    def line_col(self, char_off: int) -> tuple[int, int]:
        """1-based line and 0-based column of a char offset."""
        if self._line_starts is None:
            starts = [0]
            pos = self.text.find("\n")
            while pos >= 0:
                starts.append(pos + 1)
                pos = self.text.find("\n", pos + 1)
            self._line_starts = starts
        line = bisect.bisect_right(self._line_starts, char_off)
        return line, char_off - self._line_starts[line - 1]

    def line_start(self, char_off: int) -> int:
        """Char offset of the first character of the line containing *char_off*."""
        nl = self.text.rfind("\n", 0, char_off)
        return 0 if nl < 0 else nl + 1

    def line_end(self, char_off: int) -> int:
        """Char offset of the newline ending the line containing *char_off* (or len)."""
        nl = self.text.find("\n", char_off)
        return len(self.text) if nl < 0 else nl

    @property
    def eol(self) -> str:
        return "\r\n" if b"\r\n" in self.data else "\n"

    @property
    def slang_text(self) -> str:
        """Text to hand to pyslang for in-memory parses.

        Newlines are NOT normalized so that the byte offsets slang reports
        (into the utf-8 encoding of this string) index ``self.data``
        exactly; only invalid utf-8 bytes (replaced) can shift them.
        """
        return self.data.decode("utf-8", errors="replace")
