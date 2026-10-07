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

"""Recover substituted values by aligning a template line with its output."""

from __future__ import annotations

import re
from typing import Optional

from .flavors import PAD_NONE
from .translate import TemplateLine


def recover_values(tline: TemplateLine, out_text: str, *, strip_pad: bool = True) -> Optional[list[str]]:
    """Rendered value of each token of *tline* in *out_text*, or None when the
    literal parts of the template line do not line up with the output."""
    if not tline.tokens:
        return [] if tline.text == out_text else None
    lits = tline.literal_segments()
    pattern = "^" + "(.*?)".join(re.escape(lit) for lit in lits) + "$"
    m = re.match(pattern, out_text, re.DOTALL)
    if m is None:
        return None
    vals = list(m.groups())
    if strip_pad:
        vals = [v.strip(" ") if tok.pad != PAD_NONE else v for v, tok in zip(vals, tline.tokens)]
    return vals
