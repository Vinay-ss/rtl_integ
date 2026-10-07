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

"""Python 3 port of prepro, the Perl/Python text preprocessor.

prepro (Adrian Lewis, 2006, GPL-2-or-later) turns a template into a script
whose output is the generated text.  This port keeps its syntax and options
and runs on Windows and Linux; it also records which template line produced
each output line (:class:`LineMap`), the template's control constructs, and
optionally the values of template variables.

    python -m pyverilog_auto.prepro -py -o out.sv --linemap out.sv.map.json tpl.svp
"""

from __future__ import annotations

from .align import recover_values
from .flavors import (
    DEFAULT_SUBSTITUTIONS,
    NAME_ONLY,
    PAD_LEFT,
    PAD_NONE,
    PAD_RIGHT,
    PERL,
    PYTHON,
    SYNTAXES,
    Flavor,
    PreproOptions,
    Syntax,
    flavor_for,
    syntax_for,
)
from .linemap import Construct, LineMap, OutLine
from .run import PreproError, PreproResult, find_perl, preprocess_file, preprocess_text
from .translate import TemplateLine, Token, Translation, translate

__all__ = [
    "Construct",
    "DEFAULT_SUBSTITUTIONS",
    "Flavor",
    "LineMap",
    "NAME_ONLY",
    "OutLine",
    "PAD_LEFT",
    "PAD_NONE",
    "PAD_RIGHT",
    "PERL",
    "PYTHON",
    "SYNTAXES",
    "PreproError",
    "PreproOptions",
    "PreproResult",
    "Syntax",
    "TemplateLine",
    "Token",
    "Translation",
    "find_perl",
    "flavor_for",
    "preprocess_file",
    "preprocess_text",
    "recover_values",
    "syntax_for",
    "translate",
]
