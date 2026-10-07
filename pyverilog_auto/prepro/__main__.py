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

import sys

from .cli import main

sys.exit(main())
