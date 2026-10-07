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

"""Template flavors and substitution tokens (prepro conventions).

prepro (Adrian Lewis, 2006, GPL-2-or-later) turns a text template into a Perl
or Python script: preprocessor lines are copied as code, every other line
becomes a ``print`` of the line with ``#$var`` / ``#[expr]#`` style tokens
replaced by the value of the variable or expression.  The defaults below are
the ones of the original tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PAD_NONE = 0
PAD_LEFT = 1   # pad with trailing spaces to the width of the token text
PAD_RIGHT = 2  # pad with leading spaces to the width of the token text
PAD_MASK = 3
NAME_ONLY = 4  # flag: the ``;`` of the token holds a variable name only (`` `name` ``)

DEFAULT_SUBSTITUTIONS: tuple[tuple[str, int], ...] = (
    ("#<;<#", PAD_LEFT),
    ("#>;>#", PAD_RIGHT),
    ("#[;]#", PAD_NONE),
    ("#$", PAD_NONE),
)


@dataclass(frozen=True)
class Syntax:
    """A named set of template delimiters; ``None`` keeps the flavor default."""

    name: str
    line: Optional[str] = None
    begin: Optional[str] = None
    end: Optional[str] = None
    substitutions: Optional[tuple[tuple[str, int], ...]] = None


SYNTAXES = {
    # the original prepro markers: // py, /* py-begin .. py-end */, #$var
    "prepro": Syntax("prepro"),
    # ` code, [* .. *] code blocks, `var` for a variable; the expression and
    # padding tokens of prepro stay available
    "backtick": Syntax("backtick", line="`", begin="[*", end="*]", substitutions=(
        ("#<;<#", PAD_LEFT),
        ("#>;>#", PAD_RIGHT),
        ("#[;]#", PAD_NONE),
        ("`;`", PAD_NONE | NAME_ONLY),
    )),
}


def syntax_for(name: str) -> Syntax:
    try:
        return SYNTAXES[name]
    except KeyError:
        raise ValueError(f"unknown template syntax {name!r} (expected {' or '.join(map(repr, SYNTAXES))})") from None


@dataclass(frozen=True)
class Flavor:
    """Language-specific defaults (``-pl`` / ``-py`` in prepro)."""

    language: str      # "perl" | "python"
    line: str          # single preprocessor line marker
    begin: str         # start of a multi-line code block
    end: str           # end of a multi-line code block
    block_start: str   # last character of a code line that opens a block
    exp_start: str     # text spliced before an expression substitution
    exp_end: str
    var_start: str     # text spliced before a variable substitution
    var_end: str
    eol: str           # appended to every printed line
    print_open: str
    print_close: str


PERL = Flavor(
    language="perl",
    line="// pl",
    begin="/* pl-begin",
    end="pl-end */",
    block_start="{",
    exp_start=".(",
    exp_end=").",
    var_start=".$",
    var_end=".",
    eol="'.\"\\n\".'",
    print_open="print '",
    print_close="';",
)

PYTHON = Flavor(
    language="python",
    line="// py",
    begin="/* py-begin",
    end="py-end */",
    block_start=":",
    exp_start="+str(",
    exp_end=")+",
    var_start="+str(",
    var_end=")+",
    eol="",
    print_open="print('",
    print_close="');",
)

FLAVORS = {"perl": PERL, "python": PYTHON}


def flavor_for(language: str) -> Flavor:
    try:
        return FLAVORS[language]
    except KeyError:
        raise ValueError(f"unknown template language {language!r} (expected 'perl' or 'python')") from None


@dataclass
class PreproOptions:
    """Everything that changes how a template is translated and run."""

    language: str = "perl"
    line: Optional[str] = None       # -c ID   (None: syntax / flavor default)
    begin: Optional[str] = None      # -b ID
    end: Optional[str] = None        # -e ID
    substitutions: list[tuple[str, int]] = field(default_factory=list)  # -r/-rl/-rr/-rn (empty: defaults)
    defines: list[str] = field(default_factory=list)                   # -d LINE
    args: list[str] = field(default_factory=list)                      # ++ ARGS
    syntax: str = "prepro"           # --syntax NAME: a set of delimiters (SYNTAXES)

    @property
    def flavor(self) -> Flavor:
        return flavor_for(self.language)

    def _syntax(self) -> Syntax:
        return syntax_for(self.syntax)

    def line_id(self) -> str:
        if self.line is not None:
            return self.line
        return self._syntax().line or self.flavor.line

    def begin_id(self) -> str:
        if self.begin is not None:
            return self.begin
        return self._syntax().begin or self.flavor.begin

    def end_id(self) -> str:
        if self.end is not None:
            return self.end
        return self._syntax().end or self.flavor.end

    def effective_substitutions(self) -> list[tuple[str, int]]:
        if self.substitutions:
            return list(self.substitutions)
        return list(self._syntax().substitutions or DEFAULT_SUBSTITUTIONS)
