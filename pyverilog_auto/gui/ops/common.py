"""Shared pieces of the structural operations."""

from __future__ import annotations

import ast
import builtins
import itertools
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from ...prepro import Construct, PreproError, preprocess_text
from ...prepro.linemap import LineMap
from ..build import OutputInfo
from ..project import Project, TemplateEntry
from ..tedits import TemplateEditSet

_PLAN_IDS = itertools.count(1)


class OpError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class OpDiag:
    severity: str        # error | warning | note
    code: str
    message: str

    def to_json(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass
class Question:
    key: str
    prompt: str
    options: list[str]

    def to_json(self) -> dict:
        return {"key": self.key, "prompt": self.prompt, "options": self.options}


@dataclass
class Plan:
    op: str
    title: str
    edits: TemplateEditSet = field(default_factory=TemplateEditSet)
    renames: dict[str, str] = field(default_factory=dict)
    focus: Optional[str] = None
    diagnostics: list[OpDiag] = field(default_factory=list)
    question: Optional[Question] = None
    summary_lines: list[str] = field(default_factory=list)
    then: Optional[dict] = None          # {"method", "params"}: plan this next once applied
    id: int = field(default_factory=lambda: next(_PLAN_IDS))

    @property
    def ok(self) -> bool:
        return self.question is None and not any(d.severity == "error" for d in self.diagnostics)

    def error(self, code: str, message: str) -> "Plan":
        self.diagnostics.append(OpDiag("error", code, message))
        return self

    def warn(self, code: str, message: str) -> None:
        self.diagnostics.append(OpDiag("warning", code, message))

    def note(self, code: str, message: str) -> None:
        self.diagnostics.append(OpDiag("note", code, message))

    def to_json(self, root: Optional[str] = None) -> dict:
        out = {
            "id": self.id,
            "op": self.op,
            "title": self.title,
            "ok": self.ok,
            "summary": "\n".join([self.title] + self.summary_lines),
            "diagnostics": [d.to_json() for d in self.diagnostics],
            "focus": self.focus,
        }
        if self.question is not None:
            out["question"] = self.question.to_json()
        elif self.ok:
            out["diff"] = self.edits.diff(root)
        if self.then is not None:
            out["then"] = self.then
        return out


# ----------------------------------------------------------------------
# Template variables
# ----------------------------------------------------------------------

_PY_IGNORED = set(dir(builtins)) | {"prepro_pad", "sys", "os", "re", "math"}


def _py_names(src: str) -> tuple[set[str], set[str]]:
    """(loaded, stored) names of a Python fragment; block headers
    (``if x:``, ``for i in r:``, ``elif``/``else``) are completed first."""
    s = src.strip()
    variants = [s]
    if s.endswith(":"):
        head = s
        if s.startswith(("elif ", "else")):
            head = "if 1:\n  pass\n" + s
        variants.insert(0, head + "\n  pass")
    for v in variants:
        try:
            tree = ast.parse(v)
        except SyntaxError:
            continue
        loaded: set[str] = set()
        stored: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (stored if isinstance(node.ctx, ast.Store) else loaded).add(node.id)
            elif isinstance(node, ast.arg):
                stored.add(node.arg)
        return loaded, stored
    return set(), set()


_PL_VAR = re.compile(r"(?<![\\\w])([$@%])\{?(\w+)\}?")
_PL_STORE = re.compile(r"(?:\bmy\s+|\bour\s+|\bfor(?:each)?\s+(?:my\s+)?)([$@%])(\w+)|([$@%])(\w+)\s*=(?!=)")
_PL_SPECIAL = {"_", "ARGV", "ENV", "INC", "0", "1", "2", "3", "a", "b"}


def _perl_names(src: str) -> tuple[set[str], set[str]]:
    loaded = {sig + name for sig, name in _PL_VAR.findall(src) if name not in _PL_SPECIAL and not name.isdigit()}
    stored = set()
    for m in _PL_STORE.finditer(src):
        sig, name = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        stored.add(sig + name)
    # $x[0] / $x{k} refer to @x / %x
    fixed = set()
    for v in loaded:
        fixed.add(v)
    return fixed, stored


def free_variables(lang: str, lm: LineMap, first: int, last: int,
                   extra_headers: Optional[list[Construct]] = None) -> list[str]:
    """Template variables used but not set by template lines first..last
    (tokens of printed lines, code of inner blocks, and *extra_headers*)."""
    loaded: set[str] = set()
    stored: set[str] = set()
    order: list[str] = []
    fragments: list[str] = []
    for c in extra_headers or []:
        fragments.append(_code_of(lm, c.start))
    for n in range(first, last + 1):
        t = lm.tpl(n)
        if t.kind == "text":
            fragments.extend(tok.inner if tok.is_expr else (("$" if lang == "perl" else "") + tok.inner)
                             for tok in t.tokens)
        elif t.kind in ("code", "block") or t.code is not None:   # also code next to a block marker
            fragments.append(_code_of(lm, n))
    for frag in fragments:
        ld, st = (_py_names if lang == "python" else _perl_names)(frag)
        stored |= st
        for v in sorted(ld):
            if v not in order:
                order.append(v)
        loaded |= ld
    ignored = _PY_IGNORED if lang == "python" else set()
    return [v for v in order if v not in stored and v not in ignored]


def _code_of(lm: LineMap, lineno: int) -> str:
    t = lm.tpl(lineno)
    if t.code is not None:
        return t.code.strip()
    text = t.text
    if t.kind == "code":                       # line map written before code was recorded
        marker = "// py" if lm.language == "python" else "// pl"
        i = text.find(marker)
        if i >= 0:
            text = text[i + len(marker):]
    return text.strip()


def capture_values(proj: Project, out: OutputInfo, lines: list[int], names: list[str]) -> dict[int, dict[str, str]]:
    """Trial run of the template behind *out*, capturing *names* when each of
    *lines* prints (first emission)."""
    entry = out.entry
    assert isinstance(entry, TemplateEntry)
    with open(entry.src, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    try:
        res = preprocess_text(text, entry.options(out.extra_defines), template_path=entry.src,
                              cwd=os.path.dirname(entry.src), capture={ln: names for ln in lines},
                              perl=proj.perl, perl_lib=proj.perl_lib or None)
    except PreproError as exc:
        raise OpError("E_CAPTURE", f"trial run of {proj.rel(entry.src)} failed: {exc}") from None
    got: dict[int, dict[str, str]] = {}
    for ln in lines:
        caps = res.linemap.captures.get(ln)
        if caps:
            got[ln] = caps[0]
    return got


def literal_safe(lang: str, value: str) -> bool:
    if value == "!missing":
        return False
    if lang == "python":
        try:
            ast.literal_eval(value)
            return True
        except (ValueError, SyntaxError):
            return False
    return "\n" not in value and not value.startswith("\\")


# ----------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------

def indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def template_of_output(out: OutputInfo) -> tuple[str, Optional[LineMap]]:
    return out.src, out.linemap


def unique_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for i in itertools.count(1):
        cand = f"{base}_{i}"
        if cand not in taken:
            return cand
    raise AssertionError  # pragma: no cover


_IDENT = re.compile(r"[A-Za-z_][\w$]*")


def identifiers(text: str) -> list[str]:
    out: list[str] = []
    for m in _IDENT.finditer(text or ""):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out
