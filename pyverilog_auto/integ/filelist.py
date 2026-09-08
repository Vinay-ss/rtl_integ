"""Filelist parsing for design integration.

Accepts the common Verilog ``-f`` filelist dialect used by simulators and
synthesis tools:

* one or more tokens per line, ``//`` and ``#`` line comments, ``/* */``
  block comments, ``"..."``/``'...'`` quoting for paths with spaces,
  backslash-newline continuation, ``$VAR``/``${VAR}`` expansion;
* ``-f FILE`` (nested, paths resolved with the same policy as the parent),
  ``-F FILE`` (nested, paths resolved relative to the nested file's directory);
* ``-y DIR``, ``-v FILE``, ``-I DIR`` / ``-IDIR`` / ``+incdir+A+B``,
  ``+libext+.v+.sv``, ``+define+A=1+B`` / ``-DA=1``, ``--top NAME`` / ``-top NAME``;
* bare tokens are source files (globs are expanded).

The result is a normalized :class:`Filelist` that feeds both the pyslang
front-end and the text fallback, and that builds a :class:`VerilogConfig`
so the classic ``ModuleDatabase`` search keeps working.
"""

from __future__ import annotations

import glob
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal, Optional

if TYPE_CHECKING:
    from ..config import VerilogConfig

DEFAULT_LIBEXTS: list[str] = [".v", ".va", ".sv"]

RelativeTo = Literal["cwd", "filelist"]

# Flags that take the next token as an argument.
_TWO_ARG_FLAGS = {"-f", "-F", "-v", "-y", "-I", "--top", "-top", "-l", "-o", "-work"}
# Two-argument flags whose argument is simply ignored.
_IGNORED_TWO_ARG = {"-l", "-o", "-work"}
# Single flags that are silently ignored (common simulator switches).
_IGNORED_FLAGS = {
    "+librescan", "+notimingchecks", "-sverilog", "-sv", "+v2k", "-full64", "-q", "-quiet",
    "-R", "+vcs+lic+wait", "-lca", "-kdb", "-debug_all", "-debug_access+all",
}
_IGNORED_PREFIXES = (
    "-U", "-timescale=", "+timescale+", "+systemverilogext+", "+verilog2001ext+",
    "+verilog1995ext+", "+lint=", "-debug_access", "+warn=", "+error=", "-error=",
)

_GLOB_CHARS = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class FilelistEntry:
    """A token together with where it came from (for diagnostics)."""

    token: str
    origin: str      # filelist path, or "<argv>" / "<api>"
    line: int

    def where(self) -> str:
        return f"{self.origin}:{self.line}" if self.line else self.origin


@dataclass
class Filelist:
    """Normalized content of one or more filelists."""

    sources: list[str] = field(default_factory=list)        # bare file paths (abs, normpath, deduped)
    library_files: list[str] = field(default_factory=list)  # -v
    library_dirs: list[str] = field(default_factory=list)   # -y
    include_dirs: list[str] = field(default_factory=list)   # +incdir+ / -I
    libexts: list[str] = field(default_factory=list)        # +libext+ ; empty -> DEFAULT_LIBEXTS
    defines: dict[str, str] = field(default_factory=dict)   # +define+ / -D
    tops: list[str] = field(default_factory=list)           # --top / -top
    unknown: list[FilelistEntry] = field(default_factory=list)   # unrecognised flags (warnings)
    missing: list[FilelistEntry] = field(default_factory=list)   # referenced paths that do not exist (errors)
    warnings: list[str] = field(default_factory=list)
    visited: list[str] = field(default_factory=list)        # normcase paths of -f/-F files (cycle guard)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_args(
        cls,
        files: Iterable[str] = (),
        *,
        include_dirs: Iterable[str] = (),
        library_dirs: Iterable[str] = (),
        library_files: Iterable[str] = (),
        libexts: Optional[Iterable[str]] = None,
        defines: Optional[dict[str, str]] = None,
        tops: Iterable[str] = (),
        base_dir: Optional[str] = None,
    ) -> "Filelist":
        """Build a :class:`Filelist` from explicit API/CLI arguments."""
        fl = cls()
        base = base_dir or os.getcwd()
        for f in files:
            fl._add_source(f, base, FilelistEntry(f, "<api>", 0))
        for d in include_dirs:
            fl._add_dir(fl.include_dirs, d, base, FilelistEntry(d, "<api>", 0))
        for d in library_dirs:
            fl._add_dir(fl.library_dirs, d, base, FilelistEntry(d, "<api>", 0))
        for f in library_files:
            fl._add_file(fl.library_files, f, base, FilelistEntry(f, "<api>", 0))
        if libexts:
            for e in libexts:
                _append_unique_str(fl.libexts, e)
        if defines:
            fl.defines.update(defines)
        for t in tops:
            _append_unique_str(fl.tops, t)
        return fl

    def merge(self, other: "Filelist") -> None:
        """Append everything from *other* (order kept, duplicates dropped)."""
        for p in other.sources:
            _append_unique_path(self.sources, p)
        for p in other.library_files:
            _append_unique_path(self.library_files, p)
        for p in other.library_dirs:
            _append_unique_path(self.library_dirs, p)
        for p in other.include_dirs:
            _append_unique_path(self.include_dirs, p)
        for e in other.libexts:
            _append_unique_str(self.libexts, e)
        for k, v in other.defines.items():
            self.defines.setdefault(k, v)
        for t in other.tops:
            _append_unique_str(self.tops, t)
        self.unknown.extend(other.unknown)
        self.missing.extend(other.missing)
        self.warnings.extend(other.warnings)
        for v in other.visited:
            _append_unique_str(self.visited, v)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def effective_libexts(self) -> list[str]:
        return list(self.libexts) if self.libexts else list(DEFAULT_LIBEXTS)

    def all_files(self) -> list[str]:
        """Sources followed by explicit library files, deduped."""
        out: list[str] = []
        for p in self.sources + self.library_files:
            _append_unique_path(out, p)
        return out

    def problems(self) -> list[str]:
        """Human-readable error messages (missing files/dirs)."""
        return [f"{e.where()}: not found: {e.token}" for e in self.missing]

    # ------------------------------------------------------------------
    # Internal adders (shared by the token parser and from_args)
    # ------------------------------------------------------------------

    def _add_source(self, tok: str, base_dir: str, entry: FilelistEntry) -> None:
        path = _resolve(tok, base_dir)
        if _GLOB_CHARS.search(tok):
            hits = sorted(glob.glob(path))
            if not hits:
                self.missing.append(entry)
            for h in hits:
                if os.path.isfile(h):
                    _append_unique_path(self.sources, os.path.normpath(h))
            return
        if not os.path.isfile(path):
            self.missing.append(entry)
            return
        _append_unique_path(self.sources, path)

    def _add_file(self, target: list[str], tok: str, base_dir: str, entry: FilelistEntry) -> None:
        path = _resolve(tok, base_dir)
        if not os.path.isfile(path):
            self.missing.append(entry)
            return
        _append_unique_path(target, path)

    def _add_dir(self, target: list[str], tok: str, base_dir: str, entry: FilelistEntry) -> None:
        path = _resolve(tok, base_dir)
        if not os.path.isdir(path):
            self.missing.append(entry)
        # Keep it anyway: slang tolerates missing include dirs and the user may
        # create it later; ``missing`` carries the diagnostic.
        _append_unique_path(target, path)


# ----------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------

def tokenize_filelist_text(text: str) -> list[tuple[str, int]]:
    """Split filelist *text* into ``(token, line)`` pairs.

    Handles ``//`` and ``#`` line comments (at token boundaries), ``/* */``
    block comments, ``"..."``/``'...'`` quoting, and ``\\``-newline
    continuation.  Environment variables are NOT expanded here.
    """
    tokens: list[tuple[str, int]] = []
    cur: list[str] = []
    cur_line = 1
    line = 1
    i = 0
    n = len(text)

    def flush() -> None:
        if cur:
            tokens.append(("".join(cur), cur_line))
            cur.clear()

    while i < n:
        ch = text[i]
        if ch == "\n":
            flush()
            line += 1
            i += 1
            continue
        if ch in " \t\r\f\v":
            flush()
            i += 1
            continue
        if ch == "\\" and i + 1 < n and text[i + 1] in "\r\n":
            # Line continuation: swallow the newline, keep the token/line going.
            i += 1
            if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            i += 1
            line += 1
            continue
        if not cur and text.startswith("/*", i):
            flush()
            end = text.find("*/", i + 2)
            if end < 0:
                end = n
            line += text.count("\n", i, end)
            i = end + 2
            continue
        if not cur and (ch == "#" or text.startswith("//", i)):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if not cur and ch in "\"'":
            q = ch
            j = i + 1
            while j < n and text[j] != q and text[j] != "\n":
                j += 1
            tok = text[i + 1:j]
            if tok:
                tokens.append((tok, line))
            i = j + 1 if j < n and text[j] == q else j
            continue
        if not cur:
            cur_line = line
        cur.append(ch)
        i += 1
    flush()
    return tokens


# ----------------------------------------------------------------------
# Flag parser
# ----------------------------------------------------------------------

def parse_flag_tokens(
    tokens: list[tuple[str, int]],
    base_dir: str,
    origin: str,
    fl: Optional[Filelist] = None,
    *,
    relative_to: RelativeTo = "cwd",
    cwd: Optional[str] = None,
) -> Filelist:
    """Interpret filelist *tokens* into *fl* (created when ``None``).

    *base_dir* is the directory relative paths are resolved against.  For
    nested ``-f`` files the policy *relative_to* decides whether their
    contents resolve against *cwd* (default) or the nested file's directory;
    ``-F`` always uses the nested file's directory.
    """
    fl = fl if fl is not None else Filelist()
    cwd = cwd or os.getcwd()
    i = 0
    count = len(tokens)
    while i < count:
        raw, line = tokens[i]
        i += 1
        tok = os.path.expandvars(raw)
        entry = FilelistEntry(tok, origin, line)

        if tok in _TWO_ARG_FLAGS:
            if i >= count:
                fl.warnings.append(f"{entry.where()}: flag {tok} is missing its argument")
                break
            arg = os.path.expandvars(tokens[i][0])
            i += 1
            arg_entry = FilelistEntry(f"{tok} {arg}", origin, line)
            if tok in _IGNORED_TWO_ARG:
                continue
            if tok in ("-f", "-F"):
                path = _resolve(arg, base_dir)
                if not os.path.isfile(path):
                    fl.missing.append(arg_entry)
                    continue
                key = os.path.normcase(path)
                if key in fl.visited:
                    fl.warnings.append(f"{entry.where()}: filelist {path} already read (cycle?) - skipped")
                    continue
                if tok == "-F":
                    nested_base = os.path.dirname(path)
                else:
                    nested_base = cwd if relative_to == "cwd" else os.path.dirname(path)
                _parse_file_into(fl, path, nested_base, relative_to=relative_to, cwd=cwd)
            elif tok == "-v":
                fl._add_file(fl.library_files, arg, base_dir, arg_entry)
            elif tok == "-y":
                fl._add_dir(fl.library_dirs, arg, base_dir, arg_entry)
            elif tok == "-I":
                fl._add_dir(fl.include_dirs, arg, base_dir, arg_entry)
            elif tok in ("--top", "-top"):
                _append_unique_str(fl.tops, arg)
            continue

        m = re.match(r"^\+libext\+(.*)$", tok)
        if m:
            for ext in m.group(1).split("+"):
                if ext:
                    _append_unique_str(fl.libexts, ext)
            continue

        m = re.match(r"^\+define\+(.+)$", tok)
        if m:
            for part in m.group(1).split("+"):
                if not part:
                    continue
                name, _, value = part.partition("=")
                fl.defines[name] = value
            continue

        m = re.match(r"^-D(.+)$", tok)
        if m:
            name, _, value = m.group(1).partition("=")
            fl.defines[name] = value
            continue

        m = re.match(r"^\+incdir\+(.+)$", tok)
        if m:
            for d in m.group(1).split("+"):
                if d:
                    fl._add_dir(fl.include_dirs, d, base_dir, FilelistEntry(f"+incdir+{d}", origin, line))
            continue

        m = re.match(r"^-I(.+)$", tok)
        if m:
            fl._add_dir(fl.include_dirs, m.group(1), base_dir, entry)
            continue

        if tok in _IGNORED_FLAGS or tok.startswith(_IGNORED_PREFIXES):
            continue

        if tok.startswith(("-", "+")):
            fl.unknown.append(entry)
            continue

        fl._add_source(tok, base_dir, entry)

    return fl


def _parse_file_into(
    fl: Filelist,
    path: str,
    base_dir: str,
    *,
    relative_to: RelativeTo,
    cwd: str,
) -> None:
    key = os.path.normcase(path)
    _append_unique_str(fl.visited, key)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    parse_flag_tokens(
        tokenize_filelist_text(text), base_dir, path, fl, relative_to=relative_to, cwd=cwd,
    )


def parse_filelist(
    path: str,
    *,
    relative_to: RelativeTo = "cwd",
    fl: Optional[Filelist] = None,
    cwd: Optional[str] = None,
) -> Filelist:
    """Read a ``-f`` style filelist at *path* into a :class:`Filelist`.

    With ``relative_to="cwd"`` (default, matching most simulators) relative
    paths inside the file are resolved against the current directory; with
    ``"filelist"`` they resolve against the filelist's own directory.
    """
    fl = fl if fl is not None else Filelist()
    cwd = cwd or os.getcwd()
    path = os.path.normpath(os.path.join(cwd, os.path.expanduser(os.path.expandvars(path))))
    if not os.path.isfile(path):
        fl.missing.append(FilelistEntry(path, "<argv>", 0))
        return fl
    base_dir = cwd if relative_to == "cwd" else os.path.dirname(path)
    _parse_file_into(fl, path, base_dir, relative_to=relative_to, cwd=cwd)
    return fl


def parse_flags(
    flags: Iterable[str],
    *,
    base_dir: Optional[str] = None,
    fl: Optional[Filelist] = None,
    relative_to: RelativeTo = "cwd",
) -> Filelist:
    """Parse command-line style flag strings (each may hold several tokens)."""
    tokens: list[tuple[str, int]] = []
    for flag in flags:
        tokens.extend(tokenize_filelist_text(flag))
    base = base_dir or os.getcwd()
    return parse_flag_tokens(tokens, base, "<argv>", fl, relative_to=relative_to, cwd=base)


# ----------------------------------------------------------------------
# VerilogConfig bridge
# ----------------------------------------------------------------------

def filelist_to_config(fl: Filelist, base: "VerilogConfig | None" = None) -> "VerilogConfig":
    """Build a :class:`VerilogConfig` whose library search covers *fl*.

    Sources are added to ``library_files`` so that the classic
    ``ModuleDatabase`` lookup can find any module of the design even when
    the design index misses (fallback path).
    """
    from ..config import VerilogConfig

    cfg = deepcopy(base) if base is not None else VerilogConfig()
    dirs: list[str] = list(cfg.library_directories) if base is not None else ["."]
    for d in fl.include_dirs + fl.library_dirs:
        _append_unique_path(dirs, d)
    cfg.library_directories = dirs

    files: list[str] = list(cfg.library_files)
    for f in fl.library_files + fl.sources:
        _append_unique_path(files, f)
    cfg.library_files = files

    if fl.libexts:
        cfg.library_extensions = list(fl.libexts)
    elif base is None:
        cfg.library_extensions = list(DEFAULT_LIBEXTS)

    for k, v in fl.defines.items():
        cfg.defines.setdefault(k, v)
    return cfg


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _resolve(p: str, base_dir: str) -> str:
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(base_dir, p)
    return os.path.normpath(p)


def _append_unique_path(lst: list[str], path: str) -> None:
    key = os.path.normcase(path)
    for existing in lst:
        if os.path.normcase(existing) == key:
            return
    lst.append(path)


def _append_unique_str(lst: list[str], s: str) -> None:
    if s not in lst:
        lst.append(s)
