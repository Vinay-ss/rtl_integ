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

"""Translate a template into a Perl or Python script (the prepro algorithm).

The translation follows the original line by line:

* ``/* py-begin`` ... ``py-end */``: the lines in between are copied as code
  (as is code after the begin marker or before the end marker on their own
  lines, and a block opened and closed on one line);
* ``// py CODE``: ``CODE`` is copied as code;
* ``// py`` alone (at column 0): resets the indentation of printed lines;
* any other line is printed, with substitution tokens spliced in as
  expressions.  For Python the printed lines are indented like the last code
  line (one level deeper after a line ending in ``:``), which is how a
  template loop body is expressed.

Each template line produces at most one script line, so the translation
also yields an exact script-line <-> template-line table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .flavors import NAME_ONLY, PAD_LEFT, PAD_MASK, PAD_RIGHT, Flavor, PreproOptions

_META = re.compile(r"([\.\^\$\*\+\?\|\{\}\[\]\(\)\\])")
_SLASH_QUOTE = re.compile(r"(['\\])")
_PREPRO_CODE = re.compile(r"(\s*)[^#]*([^\s#])\s*#?.*")
# what a name-only token (`` `name` ``) accepts, per language
_NAME = {"python": r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", "perl": r"[A-Za-z_]\w*"}


def subst_meta(text: str) -> str:
    """Backslash-escape regex metacharacters (prepro's ``subst_meta``)."""
    return _META.sub(r"\\\1", text)


def slash_quote(text: str) -> str:
    """Escape quotes and backslashes for a single-quoted string literal."""
    return _SLASH_QUOTE.sub(r"\\\1", text)


@dataclass
class Token:
    """One substitution found in a printed template line."""

    raw: str       # token text as written in the template, e.g. ``#$width`` or ``#[1+i]#``
    inner: str     # variable name or expression
    pad: int       # PAD_NONE / PAD_LEFT / PAD_RIGHT
    is_expr: bool  # True for ``#[ ]#``-style tokens, False for ``#$var``
    col: int       # character column of ``raw`` in the template line


@dataclass
class TemplateLine:
    """Classification of one template line."""

    lineno: int               # 1-based
    kind: str                 # text | code | reset | begin | end | block
    text: str                 # the line without its end-of-line
    script_line: Optional[int] = None  # 1-based line in the generated script
    tokens: list[Token] = field(default_factory=list)
    indent: str = ""          # indentation the line was printed with (text lines)
    code: Optional[str] = None  # script code of the line (code lines, block lines, begin/end with code)

    @property
    def is_text(self) -> bool:
        return self.kind == "text"

    def literal_segments(self) -> list[str]:
        """Literal pieces between the tokens (``len(tokens) + 1`` items)."""
        out: list[str] = []
        pos = 0
        for t in self.tokens:
            out.append(self.text[pos:t.col])
            pos = t.col + len(t.raw)
        out.append(self.text[pos:])
        return out


@dataclass
class Translation:
    script: str
    header_lines: int                  # script lines before the first template-derived line
    lines: list[TemplateLine]          # one per template line
    script_to_tpl: dict[int, int]      # script line -> template line

    def tpl(self, lineno: int) -> TemplateLine:
        return self.lines[lineno - 1]


class _Sub:
    __slots__ = ("regex", "pad", "is_expr")

    def __init__(self, regex: "re.Pattern[str]", pad: int, is_expr: bool):
        self.regex = regex
        self.pad = pad
        self.is_expr = is_expr


def compile_substitutions(subs: list[tuple[str, int]], language: str = "perl") -> tuple[list[_Sub], list[_Sub]]:
    """Expression tokens (containing ``;``) and variable tokens, as in prepro.

    A token flagged :data:`NAME_ONLY` is a variable token whose ``;`` stands
    for the name, so both delimiters are required (`` `width` ``) and the
    text between them must be a name: SystemVerilog macros such as
    `` `define`` or `` `WIDTH-1`` are left alone.
    """
    code: list[_Sub] = []
    vars_: list[_Sub] = []
    for token, pad in subs:
        s = subst_meta(token)
        if pad & NAME_ONLY:
            if s.count(";") != 1:
                raise ValueError(f"name token {token!r} needs one ';' where the name goes")
            vars_.append(_Sub(re.compile(s.replace(";", "(" + _NAME.get(language, _NAME["perl"]) + ")")),
                              pad & PAD_MASK, False))
        elif ";" in s:
            code.append(_Sub(re.compile(s.replace(";", "(.*?)")), pad, True))
        else:
            vars_.append(_Sub(re.compile(s + r"(\w+)(" + s[0] + "?)"), pad, False))
    return code, vars_


def _subst_segments(subs: list[_Sub], start: str, end: str, segs: list[str], raw: list[object]) -> None:
    """Split even (literal) segments around each token, in place.

    ``segs`` holds script text, ``raw`` holds either the literal text or the
    :class:`Token` for the same position.
    """
    for sub in subs:
        index = 0
        while index < len(segs):
            if index % 2 == 0:
                m = sub.regex.search(segs[index])
                if m:
                    swap = m.group(1)
                    if sub.pad == PAD_LEFT:
                        swap = f"prepro_pad({swap}, {len(m.group(0))})"
                    elif sub.pad == PAD_RIGHT:
                        swap = f"prepro_pad({swap}, -{len(m.group(0))})"
                    seg = segs[index]
                    lit = raw[index]
                    assert isinstance(lit, str)
                    tok = Token(raw=m.group(0), inner=m.group(1), pad=sub.pad, is_expr=sub.is_expr, col=-1)
                    segs[index:index + 1] = [seg[:m.start()], "'" + start + swap + end + "'", seg[m.end():]]
                    raw[index:index + 1] = [lit[:m.start()], tok, lit[m.end():]]
            index += 1


class Translator:
    def __init__(self, options: PreproOptions):
        self.options = options
        self.flavor: Flavor = options.flavor
        self.code_subs, self.var_subs = compile_substitutions(options.effective_substitutions(), options.language)
        self.re_line = re.compile(r"\s*" + subst_meta(options.line_id()) + " (.*)")
        self.re_empty = re.compile(subst_meta(options.line_id()) + "$")
        # the begin marker must stand alone (``[*3]`` in an assertion is no block)
        self.re_begin = re.compile(r"\s*" + subst_meta(options.begin_id()) + r"(?=\s|$)(.*)")
        self.re_end = re.compile(r"\s*" + subst_meta(options.end_id()) + "(.*)")
        self.end_id = options.end_id()
        self._tab = ""
        self._indent = ""

    # -- indentation (prepro calc_indent) ---------------------------------

    def _calc_indent(self, code: str) -> None:
        m = _PREPRO_CODE.match(code)
        if m:
            self._indent = m.group(1)
            if m.group(2) == self.flavor.block_start:
                if self._tab == "":
                    self._tab = self._indent
                if self._tab == "":
                    self._indent = "  "
                else:
                    self._indent = self._indent + self._tab
        else:
            self._indent = ""

    # -- text lines ---------------------------------------------------------

    def text_line(self, text: str) -> tuple[str, list[Token]]:
        f = self.flavor
        segs: list[str] = [text]
        raw: list[object] = [text]
        _subst_segments(self.code_subs, f.exp_start, f.exp_end, segs, raw)
        _subst_segments(self.var_subs, f.var_start, f.var_end, segs, raw)
        for i in range(0, len(segs), 2):
            segs[i] = slash_quote(segs[i])
        tokens: list[Token] = []
        col = 0
        for item in raw:
            if isinstance(item, Token):
                item.col = col
                tokens.append(item)
                col += len(item.raw)
            else:
                col += len(item)
        return self._indent + f.print_open + "".join(segs) + f.eol + f.print_close, tokens

    # -- whole template -----------------------------------------------------

    def _ends_block(self, text: str) -> Optional[str]:
        """Code before an end marker that closes *text* (``x = 1 *]``), or
        None if the line does not end with the marker."""
        s = text.rstrip()
        if not s.endswith(self.end_id):
            return None
        return s[:len(s) - len(self.end_id)].rstrip()

    def translate(self, lines: list[str], header: list[str]) -> Translation:
        out: list[str] = list(header)
        records: list[TemplateLine] = []
        script_to_tpl: dict[int, int] = {}

        def emit(n: int, kind: str, line: str, code: str) -> None:
            out.append(code)
            self._calc_indent(code)
            records.append(TemplateLine(n, kind, line, script_line=len(out), code=code))
            script_to_tpl[len(out)] = n

        code = False
        for n, line in enumerate(lines, 1):
            if not code:
                m = self.re_begin.match(line)
                if m:
                    # code after the marker follows the "// py CODE" rule: one
                    # separating space, then the code with its indentation
                    rest = m.group(1)
                    rest = rest[1:] if rest.startswith(" ") else rest
                    before_end = self._ends_block(rest)
                    if before_end is not None:            # [* code *] on one line
                        emit(n, "code", line, before_end)
                        continue
                    code = True
                    if rest.strip():
                        emit(n, "begin", line, rest.rstrip())
                    else:
                        records.append(TemplateLine(n, "begin", line))
                    continue
                if self.re_empty.match(line):
                    self._indent = ""
                    records.append(TemplateLine(n, "reset", line))
                    continue
                m = self.re_line.match(line)
                if m:
                    emit(n, "code", line, m.group(1))
                    continue
                indent = self._indent
                script, tokens = self.text_line(line)
                out.append(script)
                records.append(TemplateLine(n, "text", line, script_line=len(out), tokens=tokens, indent=indent))
                script_to_tpl[len(out)] = n
            elif self.re_end.match(line):
                code = False
                records.append(TemplateLine(n, "end", line))
            else:
                before_end = self._ends_block(line)
                if before_end is None:
                    emit(n, "block", line, line)
                    continue
                code = False
                if before_end.strip():                       # code *] closing the block
                    emit(n, "end", line, before_end)
                else:
                    records.append(TemplateLine(n, "end", line))
        return Translation("\n".join(out) + "\n", len(header), records, script_to_tpl)


def split_template_lines(text: str) -> tuple[list[str], str]:
    """Lines without end-of-line, and the dominant end-of-line of *text*."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    eol = "\r\n" if crlf > lf else "\n"
    body = text.replace("\r\n", "\n")
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, eol


def translate(text: str, options: PreproOptions, header: Optional[list[str]] = None) -> Translation:
    lines, _eol = split_template_lines(text)
    return Translator(options).translate(lines, header or [])
