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

"""Command line compatible with the original prepro, plus line-map options.

Usage: prepro [OPTION]... [FILE]

Original options: -pl/--perl, -py/--python, -c/--code ID, -b/--begin ID,
-e/--end ID, -f/--file FILE, -r/--replace ID, -rl/--replace-left ID,
-rr/--replace-right ID, -d/--define LINE, -o/--output FILE, -k/--keep,
-h/--help, ``++ ARGS``.

Additions: --linemap FILE (write the JSON line map), --capture LINE:VAR[,VAR]
(repeatable), --perl-exe PATH, --python-exe PATH, --perl-lib DIR (repeatable,
added to PERL5LIB in the form the perl understands), --syntax NAME (a set of
delimiters: ``prepro``, the default, or ``backtick``: `` ` CODE`` lines,
``[*`` .. ``*]`` code blocks and `` `var` `` for a variable), -rn/--replace-name
ID (like -r, but the ``;`` in ID holds a variable name only, e.g. `` `;` ``).
"""

from __future__ import annotations

import sys
from typing import Optional

from .flavors import NAME_ONLY, PAD_LEFT, PAD_NONE, PAD_RIGHT, SYNTAXES, PreproOptions
from .run import PreproError, preprocess_file, preprocess_text

_TAKES_ARG = {
    "-c": "line", "--code": "line",
    "-b": "begin", "--begin": "begin",
    "-e": "end", "--end": "end",
    "-f": "file", "--file": "file",
    "-r": "r", "--replace": "r",
    "-rl": "rl", "--replace-left": "rl", "--left": "rl",
    "-rr": "rr", "--replace-right": "rr", "--right": "rr",
    "-rn": "rn", "--replace-name": "rn",
    "--syntax": "syntax",
    "-d": "define", "--define": "define",
    "-o": "output", "--output": "output",
    "--linemap": "linemap",
    "--capture": "capture",
    "--perl-exe": "perl",
    "--python-exe": "python",
    "--perl-lib": "perl_lib",
}


def _help() -> str:
    return __doc__ or ""


def parse_args(argv: list[str]) -> dict:
    opts = PreproOptions()
    st: dict = {"infile": "-", "output": "-", "file": None, "keep": False, "linemap": None,
                "capture": {}, "perl": None, "python": None, "perl_lib": [], "help": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-pl", "--perl"):
            opts.language, opts.line, opts.begin, opts.end = "perl", None, None, None
        elif a in ("-py", "--python"):
            opts.language, opts.line, opts.begin, opts.end = "python", None, None, None
        elif a in ("-k", "--keep"):
            st["keep"] = True
        elif a in ("-h", "--help"):
            st["help"] = True
        elif a == "++":
            opts.args.extend(argv[i + 1:])
            break
        elif a in _TAKES_ARG:
            if i + 1 >= len(argv):
                raise SystemExit(f"prepro: option {a} needs an argument")
            val = argv[i + 1]
            key = _TAKES_ARG[a]
            i += 1
            if key in ("line", "begin", "end"):
                setattr(opts, key, val)
            elif key == "r":
                opts.substitutions.append((val, PAD_NONE))
            elif key == "rl":
                opts.substitutions.append((val, PAD_LEFT))
            elif key == "rr":
                opts.substitutions.append((val, PAD_RIGHT))
            elif key == "rn":
                opts.substitutions.append((val, PAD_NONE | NAME_ONLY))
            elif key == "syntax":
                if val not in SYNTAXES:
                    raise SystemExit(f"prepro: unknown syntax {val!r} (expected {', '.join(SYNTAXES)})")
                opts.syntax = val
            elif key == "define":
                opts.defines.append(val)
            elif key == "perl_lib":
                st["perl_lib"].append(val)
            elif key == "capture":
                line, _, names = val.partition(":")
                st["capture"].setdefault(int(line), []).extend(n for n in names.split(",") if n)
            else:
                st[key] = val
        else:
            st["infile"] = a
        i += 1
    st["options"] = opts
    return st


def main(argv: Optional[list[str]] = None) -> int:
    st = parse_args(list(sys.argv[1:] if argv is None else argv))
    if st["help"]:
        print(_help())
        return 0
    opts: PreproOptions = st["options"]
    kw = dict(capture=st["capture"] or None, perl=st["perl"], python=st["python"],
              keep_script=st["keep"], script_path=st["file"], perl_lib=st["perl_lib"] or None)
    try:
        if st["infile"] == "-":
            res = preprocess_text(sys.stdin.read(), opts, template_path="<stdin>", **kw)
            if st["output"] not in (None, "-"):
                with open(st["output"], "w", encoding="utf-8", newline="") as fh:
                    fh.write(res.text)
            if st["linemap"]:
                res.linemap.save(st["linemap"])
        else:
            out = None if st["output"] == "-" else st["output"]
            res = preprocess_file(st["infile"], opts, output_path=out, linemap_path=st["linemap"], **kw)
    except PreproError as e:
        sys.stderr.write(str(e) + "\n")
        if e.stderr:
            sys.stderr.write(e.stderr if e.stderr.endswith("\n") else e.stderr + "\n")
        return 1
    if st["output"] in (None, "-"):
        sys.stdout.write(res.text)
    if res.stderr:
        sys.stderr.write(res.stderr)
    for w in res.warnings:
        sys.stderr.write(f"prepro: warning: {w}\n")
    return 0
