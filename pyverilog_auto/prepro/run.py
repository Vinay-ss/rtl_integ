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

"""Run a template through prepro: translate, execute, collect the line map."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from .flavors import PreproOptions
from .linemap import LineMap, linemap_from
from .runtime import MAP_ENV, prologue, prologue_length
from .translate import Translation, Translator, split_template_lines

_WINDOWS_PERLS = (
    r"C:\Program Files\Git\usr\bin\perl.exe",
    r"C:\Program Files (x86)\Git\usr\bin\perl.exe",
    r"C:\Strawberry\perl\bin\perl.exe",
)


class PreproError(RuntimeError):
    """The generated script failed; ``stderr`` has template line numbers."""

    def __init__(self, message: str, *, stderr: str = "", returncode: Optional[int] = None,
                 template: str = "", line: Optional[int] = None):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode
        self.template = template
        self.line = line


@dataclass
class PreproResult:
    text: str                  # output text with the template's end-of-line
    linemap: LineMap
    script: str
    stderr: str = ""
    script_path: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def find_perl(explicit: Optional[str] = None) -> Optional[str]:
    """Perl interpreter: explicit path, ``PREPRO_PERL``, PATH, then common Windows installs."""
    for cand in (explicit, os.environ.get("PREPRO_PERL")):
        if cand:
            return cand
    found = shutil.which("perl")
    if found:
        return found
    if os.name == "nt":
        for cand in _WINDOWS_PERLS:
            if os.path.isfile(cand):
                return cand
    return None


def _slashes(path: str) -> str:
    # MSYS / Cygwin perls accept C:/x/y but mangle backslashes in some places.
    return path.replace("\\", "/") if os.name == "nt" else path


_PERL_OS: dict[str, str] = {}


def perl_osname(perl: str) -> str:
    """``$^O`` of *perl* (``msys``, ``cygwin``, ``MSWin32``, ``linux`` ...), cached."""
    if perl not in _PERL_OS:
        try:
            proc = subprocess.run([perl, "-e", "print $^O"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=30)
            _PERL_OS[perl] = proc.stdout.decode("ascii", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            _PERL_OS[perl] = ""
    return _PERL_OS[perl]


def _posix_perl(perl: str) -> bool:
    return os.name == "nt" and perl_osname(perl) in ("msys", "cygwin")


def perl_lib_sep(perl: str) -> str:
    return ":" if _posix_perl(perl) else os.pathsep


def perl_lib_value(perl: str, dirs: list[str]) -> str:
    """``PERL5LIB`` for *perl*: POSIX ``/c/x`` paths joined by ``:`` for MSYS and
    Cygwin perls on Windows, native paths with ``os.pathsep`` otherwise."""
    if not _posix_perl(perl):
        return os.pathsep.join(os.path.abspath(d) for d in dirs)
    prefix = "/cygdrive/" if perl_osname(perl) == "cygwin" else "/"
    conv = []
    for d in dirs:
        p = os.path.abspath(d).replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            p = prefix + p[0].lower() + p[2:]
        conv.append(p)
    return ":".join(conv)


def _map_errors(stderr: str, script_path: str, tr: Translation, template: str) -> tuple[str, Optional[int]]:
    """Rewrite ``script, line N`` references into template line numbers."""
    first: list[int] = []
    names = {script_path, _slashes(script_path), os.path.basename(script_path)}
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))

    def py_sub(m: "re.Match[str]") -> str:
        n = tr.script_to_tpl.get(int(m.group(2)))
        if n is None:
            return m.group(0) + " (prepro prologue)"
        first.append(n)
        return f'File "{template}", line {n}'

    def pl_sub(m: "re.Match[str]") -> str:
        n = tr.script_to_tpl.get(int(m.group(2)))
        if n is None:
            return m.group(0) + " (prepro prologue)"
        first.append(n)
        return f"at {template} line {n}"

    text = re.sub(r'File "(' + alts + r')", line (\d+)', py_sub, stderr)
    text = re.sub(r"at (" + alts + r") line (\d+)", pl_sub, text)
    return text, (first[-1] if first else None)


def preprocess_text(
    text: str,
    options: PreproOptions,
    *,
    template_path: str = "<template>",
    output_path: str = "-",
    cwd: Optional[str] = None,
    capture: Optional[dict[int, list[str]]] = None,
    script_path: Optional[str] = None,
    keep_script: bool = False,
    perl: Optional[str] = None,
    python: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    perl_lib: Optional[list[str]] = None,
    timeout: Optional[float] = None,
) -> PreproResult:
    """Translate and run template *text*.

    ``capture`` maps template lines to variable names whose values are
    recorded each time that line prints (see :mod:`.runtime`).  ``perl_lib``
    directories are prepended to ``PERL5LIB`` in the form the selected perl
    understands (MSYS perls on Windows need ``/c/...`` paths).
    """
    lines, eol = split_template_lines(text)
    lang = options.language
    n_pro = prologue_length(lang)
    header_len = n_pro + len(options.defines)
    tr = Translator(options).translate(lines, ["" for _ in range(header_len)])

    caps_abs: dict[int, list[str]] = {}
    for tpl_line, names in (capture or {}).items():
        t = tr.lines[tpl_line - 1] if 1 <= tpl_line <= len(tr.lines) else None
        if t is not None and t.script_line is not None:
            caps_abs[t.script_line] = list(names)
    header = prologue(lang, caps_abs) + list(options.defines)
    script_lines = tr.script.split("\n")
    script_lines[:header_len] = header
    script = "\n".join(script_lines)
    tr.script = script

    if lang == "python":
        interp = python or sys.executable
    else:
        interp = find_perl(perl)
        if interp is None:
            raise PreproError("no perl interpreter found (set the perl path in the project or PREPRO_PERL)",
                              template=template_path)

    tmpdir = None
    if script_path is None:
        tmpdir = tempfile.mkdtemp(prefix="prepro_")
        script_path = os.path.join(tmpdir, "prepro_script.py" if lang == "python" else "prepro_script.pl")
    script_path = os.path.abspath(script_path)
    map_path = script_path + ".map"
    run_script = _slashes(script_path) if lang == "perl" else script_path
    try:
        with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        run_env = dict(os.environ if env is None else env)
        run_env[MAP_ENV] = _slashes(map_path) if lang == "perl" else map_path
        run_env.setdefault("PYTHONIOENCODING", "utf-8")
        if lang == "perl" and perl_lib:
            value = perl_lib_value(interp, perl_lib)
            old = run_env.get("PERL5LIB")
            run_env["PERL5LIB"] = value + (perl_lib_sep(interp) + old if old else "")
        proc = subprocess.run(
            [interp, run_script, *options.args],
            cwd=cwd,
            env=run_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        stderr = proc.stderr.decode("utf-8", "replace")
        mapped, line = _map_errors(stderr, run_script, tr, template_path)
        if proc.returncode != 0:
            where = f"{template_path}:{line}: " if line else f"{template_path}: "
            raise PreproError(where + f"template script failed (exit {proc.returncode})",
                              stderr=mapped, returncode=proc.returncode, template=template_path, line=line)
        records = ""
        if os.path.exists(map_path):
            with open(map_path, "r", encoding="utf-8", errors="replace") as fh:
                records = fh.read()
        out_text = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        lm = linemap_from(tr, records, template=template_path, output=output_path, language=lang, eol=eol)
        warnings: list[str] = []
        n_out = out_text.count("\n") + (0 if out_text.endswith("\n") or not out_text else 1)
        if n_out != len(lm.out):
            warnings.append(f"line map has {len(lm.out)} entries for {n_out} output lines "
                            "(output written outside print?)")
        if eol != "\n":
            out_text = out_text.replace("\n", eol)
        return PreproResult(out_text, lm, script, mapped, script_path if keep_script else None, warnings)
    finally:
        if os.path.exists(map_path):
            os.remove(map_path)
        if not keep_script:
            if os.path.exists(script_path):
                os.remove(script_path)
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)


def preprocess_file(
    template_path: str,
    options: PreproOptions,
    *,
    output_path: Optional[str] = None,
    linemap_path: Optional[str] = None,
    **kw,
) -> PreproResult:
    """Run *template_path*; write the output and line map when paths are given."""
    with open(template_path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    res = preprocess_text(text, options, template_path=template_path, output_path=output_path or "-", **kw)
    if output_path and output_path != "-":
        d = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(d, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(res.text)
    if linemap_path:
        d = os.path.dirname(os.path.abspath(linemap_path))
        os.makedirs(d, exist_ok=True)
        res.linemap.save(linemap_path)
    return res
