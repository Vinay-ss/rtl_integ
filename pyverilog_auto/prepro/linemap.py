# Python 3 port of prepro, the Perl/Python preprocessor for text files.
#
# prepro: Copyright (c) 2006 Adrian Lewis <indproj@yahoo.com>
# Port and changes: Copyright (c) 2026 Vinay S -- rewritten for Python 3,
# Windows and Linux; line maps, template constructs, variable capture and
# the backtick template syntax added.
#
# prepro is free software under the GNU General Public License version 2 or
# (at your option) any later version.  This port is distributed under the
# GNU General Public License version 3 or later; see the LICENSE file.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.

"""Line map: which template line produced each output line.

Besides the per-output-line origin, the map keeps a classification of every
template line (text / code / ...), the substitution tokens of printed lines,
the control constructs of the template (loops, conditionals, code blocks)
and any captured variable values.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from .translate import TemplateLine, Token, Translation

LOOP_KINDS = ("for", "while", "foreach", "until")
COND_KINDS = ("if", "unless")


@dataclass
class OutLine:
    tpl: Optional[int]        # template line (None: prologue / unknown)
    script: int               # script line of the print that started the line


@dataclass
class Construct:
    """A control construct of the template, by template line span.

    ``start`` is the header line (``for``/``if``/``py-begin`` ...) and
    ``end`` the last template line inside it.  ``has_else`` is set for
    conditionals with an ``else``/``elif`` part.
    """

    id: int
    kind: str                  # for | while | if | def | codeblock | other ...
    header: str
    start: int
    end: int
    parent: Optional[int] = None
    has_else: bool = False

    @property
    def is_loop(self) -> bool:
        return self.kind in LOOP_KINDS

    @property
    def is_cond(self) -> bool:
        return self.kind in COND_KINDS

    def contains(self, lineno: int) -> bool:
        return self.start < lineno <= self.end


@dataclass
class LineMap:
    template: str
    output: str
    language: str
    eol: str
    out: list[OutLine]
    lines: list[TemplateLine]
    constructs: list[Construct] = field(default_factory=list)
    captures: dict[int, list[dict[str, str]]] = field(default_factory=dict)

    # -- queries ----------------------------------------------------------

    def tpl(self, lineno: int) -> TemplateLine:
        return self.lines[lineno - 1]

    def origin(self, out_lineno: int) -> Optional[int]:
        """Template line of output line *out_lineno* (1-based)."""
        if 1 <= out_lineno <= len(self.out):
            return self.out[out_lineno - 1].tpl
        return None

    def kind_of_output(self, out_lineno: int) -> str:
        """``literal`` / ``subst`` / ``code`` / ``unknown`` for an output line."""
        n = self.origin(out_lineno)
        if n is None:
            return "unknown"
        t = self.tpl(n)
        if t.kind != "text":
            return "code"
        return "subst" if t.tokens else "literal"

    def emit_count(self, lineno: int) -> int:
        return sum(1 for o in self.out if o.tpl == lineno)

    def emit_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for o in self.out:
            if o.tpl is not None:
                counts[o.tpl] = counts.get(o.tpl, 0) + 1
        return counts

    def outputs_of(self, lineno: int) -> list[int]:
        """Output lines (1-based) produced by template line *lineno*."""
        return [i for i, o in enumerate(self.out, 1) if o.tpl == lineno]

    def enclosing(self, lineno: int) -> list[Construct]:
        """Constructs containing *lineno*, outermost first."""
        found = [c for c in self.constructs if c.contains(lineno)]
        return sorted(found, key=lambda c: (c.start, -c.end))

    def innermost(self, lineno: int) -> Optional[Construct]:
        enc = self.enclosing(lineno)
        return enc[-1] if enc else None

    # -- serialization ------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": 1,
            "template": self.template,
            "output": self.output,
            "language": self.language,
            "eol": self.eol,
            "out": [[o.tpl, o.script] for o in self.out],
            "lines": [
                {
                    "n": t.lineno,
                    "kind": t.kind,
                    "text": t.text,
                    "script": t.script_line,
                    "indent": t.indent,
                    "tokens": [asdict(tok) for tok in t.tokens],
                    "code": t.code,
                }
                for t in self.lines
            ],
            "constructs": [asdict(c) for c in self.constructs],
            "captures": {str(k): v for k, v in self.captures.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> "LineMap":
        lines = [
            TemplateLine(
                lineno=d["n"], kind=d["kind"], text=d["text"], script_line=d.get("script"),
                tokens=[Token(**tok) for tok in d.get("tokens", [])], indent=d.get("indent", ""),
                code=d.get("code"),
            )
            for d in data["lines"]
        ]
        return cls(
            template=data["template"],
            output=data["output"],
            language=data["language"],
            eol=data.get("eol", "\n"),
            out=[OutLine(tpl, script) for tpl, script in data["out"]],
            lines=lines,
            constructs=[Construct(**c) for c in data.get("constructs", [])],
            captures={int(k): v for k, v in data.get("captures", {}).items()},
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.to_json(), fh, indent=1)
            fh.write("\n")

    @classmethod
    def load(cls, path: str) -> "LineMap":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(json.load(fh))


# ----------------------------------------------------------------------
# Constructs
# ----------------------------------------------------------------------

def _tpl_of(tr: Translation, script_line: int) -> Optional[int]:
    return tr.script_to_tpl.get(script_line)


def _last_tpl_upto(tr: Translation, script_end: int, floor: int) -> Optional[int]:
    for s in range(script_end, floor - 1, -1):
        n = tr.script_to_tpl.get(s)
        if n is not None:
            return n
    return None


def _python_constructs(tr: Translation) -> list[Construct]:
    try:
        tree = ast.parse(tr.script)
    except SyntaxError:
        return []
    out: list[Construct] = []
    kinds = {
        ast.For: "for", ast.AsyncFor: "for", ast.While: "while", ast.If: "if",
        ast.With: "with", ast.AsyncWith: "with", ast.Try: "try",
        ast.FunctionDef: "def", ast.AsyncFunctionDef: "def", ast.ClassDef: "class",
    }
    for node in ast.walk(tree):
        kind = kinds.get(type(node))
        if kind is None or node.lineno <= tr.header_lines:
            continue
        start = _tpl_of(tr, node.lineno)
        end_line = getattr(node, "end_lineno", None) or node.lineno
        end = _last_tpl_upto(tr, end_line, node.lineno)
        if start is None or end is None:
            continue
        header = tr.tpl(start).text
        has_else = bool(getattr(node, "orelse", None)) and kind in ("if", "for", "while", "try")
        out.append(Construct(0, kind, header.strip(), start, end, has_else=has_else))
    return out


_PERL_KIND = re.compile(r"^\s*(?:\}\s*)?(foreach|for|while|until|if|elsif|else|unless|sub)\b")


def _strip_perl_noise(code: str) -> str:
    """Remove string literals and comments (roughly) before counting braces."""
    out = []
    i = 0
    quote = None
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "#" and not (i and code[i - 1] == "$"):
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _perl_constructs(tr: Translation) -> list[Construct]:
    script = tr.script.split("\n")
    out: list[Construct] = []
    stack: list[tuple[int, str, str]] = []  # (start tpl line, kind, header)
    for t in tr.lines:
        if t.kind in ("text", "reset") or t.script_line is None:
            continue
        code = script[t.script_line - 1]
        clean = _strip_perl_noise(code)
        for pos, ch in enumerate(clean):
            if ch == "}":
                if stack:
                    start, kind, header = stack.pop()
                    end = t.lineno - 1 if not clean[:pos].strip() else t.lineno
                    out.append(Construct(0, kind, header, start, max(end, start)))
            elif ch == "{":
                stmt = re.split(r"[;}]", clean[:pos])[-1]
                m = _PERL_KIND.match(stmt)
                kind = m.group(1) if m else "other"
                if kind in ("elsif", "else"):
                    prev = out[-1] if out else None
                    if prev is not None and prev.kind in ("if", "unless"):
                        prev.has_else = True
                    kind = "if" if kind == "elsif" else "else"
                stack.append((t.lineno, kind, code.strip()))
    return out


def _codeblock_constructs(tr: Translation) -> list[Construct]:
    out: list[Construct] = []
    start: Optional[int] = None
    for t in tr.lines:
        if t.kind == "begin":
            start = t.lineno
        elif t.kind == "end" and start is not None:
            out.append(Construct(0, "codeblock", tr.tpl(start).text.strip(), start, t.lineno))
            start = None
    return out


def build_constructs(tr: Translation, language: str) -> list[Construct]:
    items = _codeblock_constructs(tr)
    items += _python_constructs(tr) if language == "python" else _perl_constructs(tr)
    items.sort(key=lambda c: (c.start, -c.end))
    for i, c in enumerate(items):
        c.id = i
    for c in items:
        best: Optional[Construct] = None
        for p in items:
            if p is c:
                continue
            if p.start <= c.start and c.end <= p.end and (p.start, -p.end) < (c.start, -c.end):
                if best is None or (p.start, -p.end) > (best.start, -best.end):
                    best = p
        c.parent = best.id if best is not None else None
    return items


def parse_records(text: str, tr: Translation) -> tuple[list[OutLine], dict[int, list[dict[str, str]]]]:
    """Turn the hook's ``L``/``C`` records into output lines and captures."""
    out: list[OutLine] = []
    caps: dict[int, list[dict[str, str]]] = {}
    current: dict[int, dict[str, str]] = {}
    for raw in text.splitlines():
        if not raw:
            continue
        parts = raw.split("\t", 3)
        if parts[0] == "L" and len(parts) >= 2:
            script = int(parts[1])
            out.append(OutLine(tr.script_to_tpl.get(script), script))
            current = {}
        elif parts[0] == "C" and len(parts) == 4:
            script = int(parts[1])
            tpl = tr.script_to_tpl.get(script)
            if tpl is None:
                continue
            if not current:
                caps.setdefault(tpl, []).append(current)
            current[parts[2]] = parts[3]
    return out, caps


def linemap_from(tr: Translation, records: str, *, template: str, output: str, language: str, eol: str) -> LineMap:
    out, caps = parse_records(records, tr)
    return LineMap(
        template=template,
        output=output,
        language=language,
        eol=eol,
        out=out,
        lines=tr.lines,
        constructs=build_constructs(tr, language),
        captures=caps,
    )


def iter_text_lines(lm: LineMap) -> Iterable[TemplateLine]:
    return (t for t in lm.lines if t.kind == "text")
