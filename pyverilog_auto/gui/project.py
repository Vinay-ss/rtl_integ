"""Project manifest (``rtl_integ_project.toml``).

A project lists the templates that generate RTL (run through prepro), the
plain RTL sources used as they are, and how the generated files are
integrated.  Relative paths are relative to the manifest's directory.

Example::

    [project]
    top = "top"
    build_dir = "build"
    include_dirs = ["inc"]
    defines = { SYNTH = "1" }
    perl = ""                    # empty: PATH / Git-for-Windows perl
    perl_lib = ["tools/perl"]
    route = "none"               # "collect": apply //auto_route annotations
    syntax = "prepro"            # template delimiters, default for every template:
                                 #   "prepro":   // py CODE, /* py-begin .. py-end */, #$var
                                 #   "backtick": ` CODE, [* .. *], `var`

    [[template]]
    src = "tpl/top.svp"
    out = "top.sv"
    lang = "python"              # or "perl"
    syntax = "backtick"          # this template only (default: project.syntax)
    args = []                    # passed after ++
    defines = []                 # -d lines
    replace = []                 # [["#$", "none"], ["<#;>", "right"], ["`;`", "name"]]; empty: the syntax's

    [[source]]
    path = "rtl/stage.sv"

    [gui.wrap]
    port_naming = "net"          # or "inst_port"
    wrapper_dir = "tpl"
    style = "explicit"           # or "auto"
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..prepro import NAME_ONLY, PAD_LEFT, PAD_NONE, PAD_RIGHT, SYNTAXES, PreproOptions

MANIFEST_NAME = "rtl_integ_project.toml"

_PADS = {"none": PAD_NONE, "left": PAD_LEFT, "right": PAD_RIGHT, "name": PAD_NONE | NAME_ONLY}
_PAD_NAMES = {v: k for k, v in _PADS.items()}


class ProjectError(RuntimeError):
    pass


def _load_toml(path: str) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            raise ProjectError("reading the project file needs Python >= 3.11 or the 'tomli' package") from None
    with open(path, "rb") as fh:
        try:
            return tomllib.load(fh)
        except Exception as exc:
            raise ProjectError(f"{path}: {exc}") from None


@dataclass
class Binding:
    """Template variables taken from another template's run.

    ``vars`` are captured when line ``line`` of template ``src`` prints (its
    first emission); their values become ``-d`` lines of this template.
    """

    src: str
    line: int
    vars: list[str]


@dataclass
class TemplateEntry:
    src: str                         # absolute template path
    out: str                         # output path relative to the gen dir
    lang: str = "python"
    args: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    replace: list[tuple[str, int]] = field(default_factory=list)
    bind: Optional[Binding] = None
    created_by_gui: bool = False
    syntax: str = "prepro"           # delimiter set (prepro.SYNTAXES)

    def options(self, extra_defines: Optional[list[str]] = None) -> PreproOptions:
        return PreproOptions(
            language=self.lang,
            syntax=self.syntax,
            substitutions=list(self.replace),
            defines=list(self.defines) + list(extra_defines or []),
            args=list(self.args),
        )


@dataclass
class SourceEntry:
    path: str                        # absolute path
    out: str                         # path relative to the gen dir
    created_by_gui: bool = False


@dataclass
class WrapSettings:
    port_naming: str = "net"
    wrapper_dir: str = ""            # absolute; "" = next to the parent template
    style: str = "explicit"


@dataclass
class Project:
    path: Optional[str]              # manifest path (None: view-only project)
    root: str
    top: list[str] = field(default_factory=list)
    build_dir: str = ""
    include_dirs: list[str] = field(default_factory=list)
    library_dirs: list[str] = field(default_factory=list)
    libexts: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    perl: Optional[str] = None
    perl_lib: list[str] = field(default_factory=list)
    route: str = "none"
    syntax: str = "prepro"           # default delimiter set of the templates
    templates: list[TemplateEntry] = field(default_factory=list)
    sources: list[SourceEntry] = field(default_factory=list)
    filelist: Optional[str] = None   # view-only: the user's filelist
    wrap: WrapSettings = field(default_factory=WrapSettings)

    # -- paths ----------------------------------------------------------

    @property
    def gen_dir(self) -> str:
        return os.path.join(self.build_dir, "gen")

    @property
    def integ_dir(self) -> str:
        return os.path.join(self.build_dir, "integ")

    @property
    def state_dir(self) -> str:
        return os.path.join(self.root, ".rtl_integ_gui")

    @property
    def view_only(self) -> bool:
        return self.path is None

    def abs(self, p: str) -> str:
        return os.path.normpath(p if os.path.isabs(p) else os.path.join(self.root, p))

    def rel(self, p: str) -> str:
        try:
            return os.path.relpath(p, self.root).replace("\\", "/")
        except ValueError:
            return p

    def entry_for_output(self, out_rel: str):
        key = os.path.normcase(os.path.normpath(out_rel))
        for t in self.templates:
            if os.path.normcase(os.path.normpath(t.out)) == key:
                return t
        for s in self.sources:
            if os.path.normcase(os.path.normpath(s.out)) == key:
                return s
        return None

    def template_for_src(self, src: str) -> Optional[TemplateEntry]:
        key = os.path.normcase(os.path.abspath(src))
        for t in self.templates:
            if os.path.normcase(t.src) == key:
                return t
        return None

    def outputs(self) -> list[str]:
        """Output paths (relative to the gen dir) in manifest order."""
        return [t.out for t in self.templates] + [s.out for s in self.sources]


def _str_list(v: Any, what: str) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ProjectError(f"{what} must be a list of strings")
    return list(v)


def _out_name(src: str, lang: str) -> str:
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    if ext.lower() in (".sv", ".v", ".svh", ".vh"):
        return base
    # tpl.svp / tpl.sv.py / tpl.plv -> .sv / .v
    if ext.lower() in (".py", ".pl"):
        inner_stem, inner_ext = os.path.splitext(stem)
        if inner_ext.lower() in (".sv", ".v"):
            return stem
    if ext.lower() in (".plv", ".pyv", ".vp"):
        return stem + ".v"
    return stem + ".sv"


def load_project(path: str) -> Project:
    """Read a manifest; *path* may be the file or its directory."""
    if os.path.isdir(path):
        path = os.path.join(path, MANIFEST_NAME)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ProjectError(f"no project file {path}")
    data = _load_toml(path)
    root = os.path.dirname(path)
    p = data.get("project", {}) or {}
    proj = Project(path=path, root=root)
    top = p.get("top")
    proj.top = _str_list(top, "project.top")
    proj.build_dir = os.path.normpath(os.path.join(root, p.get("build_dir", "build")))
    proj.include_dirs = [proj.abs(d) for d in _str_list(p.get("include_dirs"), "project.include_dirs")]
    proj.library_dirs = [proj.abs(d) for d in _str_list(p.get("library_dirs"), "project.library_dirs")]
    proj.libexts = _str_list(p.get("libexts"), "project.libexts")
    defs = p.get("defines") or {}
    if not isinstance(defs, dict):
        raise ProjectError("project.defines must be a table")
    proj.defines = {str(k): str(v) for k, v in defs.items()}
    proj.perl = p.get("perl") or None
    proj.perl_lib = [proj.abs(d) for d in _str_list(p.get("perl_lib"), "project.perl_lib")]
    proj.route = p.get("route", "none")
    if proj.route not in ("none", "collect"):
        raise ProjectError("project.route must be 'none' or 'collect'")
    proj.syntax = p.get("syntax", "prepro")
    if proj.syntax not in SYNTAXES:
        raise ProjectError(f"project.syntax must be one of {', '.join(SYNTAXES)}")

    seen_out: set[str] = set()
    for i, t in enumerate(data.get("template", []) or [], 1):
        if "src" not in t:
            raise ProjectError(f"template #{i}: missing 'src'")
        lang = t.get("lang", "python")
        if lang not in ("python", "perl"):
            raise ProjectError(f"template #{i}: lang must be 'python' or 'perl'")
        src = proj.abs(t["src"])
        out = t.get("out") or _out_name(src, lang)
        syntax = t.get("syntax", proj.syntax)
        if syntax not in SYNTAXES:
            raise ProjectError(f"template #{i}: syntax must be one of {', '.join(SYNTAXES)}")
        replace: list[tuple[str, int]] = []
        for r in t.get("replace", []) or []:
            if isinstance(r, str):
                replace.append((r, PAD_NONE))
            elif isinstance(r, list) and len(r) == 2 and r[1] in _PADS:
                replace.append((str(r[0]), _PADS[r[1]]))
            else:
                raise ProjectError(f"template #{i}: replace entries are TOKEN or [TOKEN, none|left|right|name]")
        bind = None
        b = t.get("bind")
        if b:
            try:
                bind = Binding(src=proj.abs(b["from"]), line=int(b["line"]), vars=_str_list(b["vars"], "bind.vars"))
            except (KeyError, ValueError, TypeError):
                raise ProjectError(f"template #{i}: bind needs from, line and vars") from None
        entry = TemplateEntry(
            src=src, out=out, lang=lang,
            args=_str_list(t.get("args"), "template.args"),
            defines=_str_list(t.get("defines"), "template.defines"),
            replace=replace, bind=bind, created_by_gui=bool(t.get("created_by_gui", False)), syntax=syntax,
        )
        key = os.path.normcase(os.path.normpath(out))
        if key in seen_out:
            raise ProjectError(f"two entries write {out}")
        seen_out.add(key)
        proj.templates.append(entry)
    for i, s in enumerate(data.get("source", []) or [], 1):
        if "path" not in s:
            raise ProjectError(f"source #{i}: missing 'path'")
        sp = proj.abs(s["path"])
        out = s.get("out") or os.path.basename(sp)
        key = os.path.normcase(os.path.normpath(out))
        if key in seen_out:
            raise ProjectError(f"two entries write {out}")
        seen_out.add(key)
        proj.sources.append(SourceEntry(path=sp, out=out, created_by_gui=bool(s.get("created_by_gui", False))))

    gw = (data.get("gui", {}) or {}).get("wrap", {}) or {}
    proj.wrap = WrapSettings(
        port_naming=gw.get("port_naming", "net"),
        wrapper_dir=proj.abs(gw["wrapper_dir"]) if gw.get("wrapper_dir") else "",
        style=gw.get("style", "explicit"),
    )
    if proj.wrap.port_naming not in ("net", "inst_port"):
        raise ProjectError("gui.wrap.port_naming must be 'net' or 'inst_port'")
    if proj.wrap.style not in ("explicit", "auto"):
        raise ProjectError("gui.wrap.style must be 'explicit' or 'auto'")
    return proj


def view_only_project(filelist: str, *, top: Optional[str] = None) -> Project:
    """A project over an existing filelist: browse only, no template edits."""
    filelist = os.path.abspath(filelist)
    root = os.path.dirname(filelist)
    return Project(path=None, root=root, top=[top] if top else [], build_dir="", filelist=filelist)


# ----------------------------------------------------------------------
# Writing manifest entries (appended as text, so user formatting stays)
# ----------------------------------------------------------------------

def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _qlist(items: list[str]) -> str:
    return "[" + ", ".join(_q(x) for x in items) + "]"


def template_entry_toml(proj: Project, entry: TemplateEntry) -> str:
    lines = ["", "[[template]]", f"src = {_q(proj.rel(entry.src))}", f"out = {_q(entry.out.replace(os.sep, '/'))}",
             f"lang = {_q(entry.lang)}"]
    if entry.syntax != proj.syntax:
        lines.append(f"syntax = {_q(entry.syntax)}")
    if entry.args:
        lines.append(f"args = {_qlist(entry.args)}")
    if entry.defines:
        lines.append(f"defines = {_qlist(entry.defines)}")
    if entry.replace:
        reps = ", ".join(f"[{_q(tok)}, {_q(_PAD_NAMES[pad])}]" for tok, pad in entry.replace)
        lines.append(f"replace = [{reps}]")
    if entry.bind:
        b = entry.bind
        lines.append(f"bind = {{ from = {_q(proj.rel(b.src))}, line = {b.line}, vars = {_qlist(b.vars)} }}")
    if entry.created_by_gui:
        lines.append("created_by_gui = true")
    return "\n".join(lines) + "\n"


def source_entry_toml(proj: Project, entry: SourceEntry) -> str:
    lines = ["", "[[source]]", f"path = {_q(proj.rel(entry.path))}", f"out = {_q(entry.out.replace(os.sep, '/'))}"]
    if entry.created_by_gui:
        lines.append("created_by_gui = true")
    return "\n".join(lines) + "\n"


def remove_entry_text(manifest_text: str, proj: Project, path: str) -> Optional[str]:
    """Manifest text without the ``[[template]]``/``[[source]]`` table whose
    ``src``/``path`` is *path*; None when there is no such table."""
    key = os.path.normcase(os.path.abspath(path))
    lines = manifest_text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s in ("[[template]]", "[[source]]"):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("["):
                j += 1
            block = "".join(lines[i:j])
            m = re.search(r'^\s*(?:src|path)\s*=\s*"((?:[^"\\]|\\.)*)"', block, re.M)
            if m and os.path.normcase(proj.abs(m.group(1).replace('\\\\', '\\'))) == key:
                start = i
                while start > 0 and not lines[start - 1].strip():
                    start -= 1
                end = j
                return "".join(lines[:start]) + ("\n" if start and end < len(lines) else "") + "".join(lines[end:])
            i = j
        else:
            i += 1
    return None


def append_template_entry_text(manifest_text: str, proj: Project, entry) -> str:
    """Manifest text with a ``[[template]]`` (or ``[[source]]``) table for
    *entry* appended.

    The new table is inserted before the first non-array table that follows
    the last ``[[template]]``/``[[source]]`` table (e.g. ``[gui.wrap]``), or
    at the end, so the entries stay grouped.
    """
    block = template_entry_toml(proj, entry) if isinstance(entry, TemplateEntry) else source_entry_toml(proj, entry)
    lines = manifest_text.splitlines(keepends=True)
    insert_at = len(lines)
    seen_array = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[[template]]") or s.startswith("[[source]]"):
            seen_array = True
            insert_at = len(lines)
        elif seen_array and s.startswith("[") and not s.startswith("[["):
            insert_at = i
            break
    head = "".join(lines[:insert_at]).rstrip("\n")
    head = head + "\n" if head else ""
    tail = "".join(lines[insert_at:])
    return head + block + ("\n" + tail if tail else "")
