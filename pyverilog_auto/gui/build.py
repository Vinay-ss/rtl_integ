"""Build pipeline: templates -> build/gen -> build/integ -> Design.

* every template runs through prepro into ``build/gen/<out>`` together with
  its line map ``<out>.map.json``; plain sources are copied unchanged;
* ``build/gen`` is copied to ``build/integ`` and AUTO-expanded leaf first
  (optionally followed by ``//auto_route`` collection);
* the integrated files give the Design shown in the GUI, the gen files a
  second, scan-only Design used to find statements before expansion.

Nothing outside the build directory is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from ..integ.design import Design, DesignError
from ..prepro import LineMap, PreproError, preprocess_file
from .project import Binding, Project, SourceEntry, TemplateEntry

_STATE = ".state.json"
_FORMAT = 2


@dataclass
class BuildDiag:
    severity: str                  # error | warning | note
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    source: str = "build"          # prepro | design | expand | route | build

    def __str__(self) -> str:
        loc = ""
        if self.file:
            loc = f"{self.file}:{self.line}: " if self.line else f"{self.file}: "
        return f"{loc}{self.severity}: {self.message}"

    def to_json(self) -> dict:
        return {"severity": self.severity, "message": self.message, "file": self.file,
                "line": self.line, "source": self.source}


@dataclass
class OutputInfo:
    """One generated file and where it comes from."""

    out: str                                  # path relative to gen/ and integ/
    gen_path: str
    integ_path: str
    kind: str                                 # template | source | file
    src: str                                  # template / source file (the edit target)
    entry: Optional[Union[TemplateEntry, SourceEntry]] = None
    linemap: Optional[LineMap] = None         # None: identity (plain source)
    extra_defines: list[str] = field(default_factory=list)   # -d lines from bindings

    @property
    def editable(self) -> bool:
        return self.kind in ("template", "source")


@dataclass
class BuildResult:
    project: Project
    outputs: list[OutputInfo] = field(default_factory=list)
    design: Optional[Design] = None
    gen_design: Optional[Design] = None
    diagnostics: list[BuildDiag] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.design is not None and not any(d.severity == "error" and d.source in ("prepro", "build", "expand")
                                                   for d in self.diagnostics)

    def _find(self, attr: str, path: str) -> Optional[OutputInfo]:
        key = os.path.normcase(os.path.abspath(path))
        for o in self.outputs:
            if os.path.normcase(os.path.abspath(getattr(o, attr))) == key:
                return o
        return None

    def by_integ(self, path: str) -> Optional[OutputInfo]:
        return self._find("integ_path", path)

    def by_gen(self, path: str) -> Optional[OutputInfo]:
        return self._find("gen_path", path)

    def by_src(self, path: str) -> Optional[OutputInfo]:
        return self._find("src", path)


def _hash(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, bytes):
            h.update(p)
        else:
            h.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def binding_defines(lang: str, values: dict[str, str]) -> list[str]:
    """``-d`` lines that set captured variables in a template of *lang*."""
    lines = []
    for name, value in values.items():
        if lang == "python":
            lines.append(f"{name.lstrip('$@%')} = {value}")
        else:
            bare = name.lstrip("$@%")
            if name.startswith("@"):
                lines.append(f"@{bare} = @{{{value}}};")
            elif name.startswith("%"):
                lines.append(f"%{bare} = %{{{value}}};")
            else:
                lines.append(f"${bare} = {value};")
    return lines


class Builder:
    def __init__(self, project: Project, *, log: Optional[Callable[[str], None]] = None):
        self.project = project
        self._log_cb = log

    def _log(self, res: BuildResult, msg: str) -> None:
        res.log.append(msg)
        if self._log_cb:
            self._log_cb(msg)

    # ------------------------------------------------------------------

    def build(self, *, force: bool = False, expand: bool = True) -> BuildResult:
        proj = self.project
        res = BuildResult(project=proj)
        if proj.view_only:
            return self._build_view_only(res)
        os.makedirs(proj.gen_dir, exist_ok=True)
        os.makedirs(proj.maps_dir, exist_ok=True)
        state_path = os.path.join(proj.build_dir, _STATE)
        state = self._load_state(state_path)
        new_state: dict = {"format": _FORMAT, "outputs": {}}
        if not self._check_outputs(res):
            return res

        captures = self._capture_requests()
        captured: dict[str, dict[int, list[dict[str, str]]]] = {}
        for entry in self._template_order(res):
            info = self._run_template(entry, res, state, new_state, captures, captured, force)
            if info is not None:
                res.outputs.append(info)
        for s in proj.sources:
            res.outputs.append(self._copy_source(s, res))
        new_state["generated"] = sorted({o.out for o in res.outputs})
        self._remove_stale(state, new_state, res)
        with open(state_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(new_state, fh, indent=1)

        if any(d.severity == "error" for d in res.diagnostics):
            self._log(res, "build stopped: template errors")
            return res
        self._stage_integ(res)
        self._make_designs(res, expand=expand)
        return res

    def _check_outputs(self, res: BuildResult) -> bool:
        """A generated file must never land on a template or source."""
        proj = self.project
        inputs = {os.path.normcase(os.path.abspath(p)) for p in
                  [t.src for t in proj.templates] + [s.path for s in proj.sources]}
        ok = True
        for t in proj.templates:
            gen = os.path.normcase(os.path.abspath(os.path.join(proj.gen_dir, t.out)))
            if gen in inputs:
                res.diagnostics.append(BuildDiag("error", f"output {proj.rel(gen)} would overwrite an input file",
                                                 t.src, source="build"))
                ok = False
        return ok

    def _remove_stale(self, state: dict, new_state: dict, res: BuildResult) -> None:
        """Delete files this tool generated earlier that no entry produces now
        (e.g. the output of a wrapper whose creation was undone)."""
        proj = self.project
        for out in set(state.get("generated", [])) - set(new_state["generated"]):
            for path in (os.path.join(proj.gen_dir, out), os.path.join(proj.maps_dir, out + ".map.json")):
                if os.path.isfile(path):
                    os.remove(path)
                    self._log(res, f"[build] removed {proj.rel(path)} (no longer generated)")

    # -- templates ------------------------------------------------------

    def _capture_requests(self) -> dict[str, dict[int, list[str]]]:
        req: dict[str, dict[int, list[str]]] = {}
        for t in self.project.templates:
            if t.bind:
                key = os.path.normcase(t.bind.src)
                lines = req.setdefault(key, {})
                names = lines.setdefault(t.bind.line, [])
                names.extend(v for v in t.bind.vars if v not in names)
        return req

    def _template_order(self, res: BuildResult) -> list[TemplateEntry]:
        by_src = {os.path.normcase(t.src): t for t in self.project.templates}
        order: list[TemplateEntry] = []
        state: dict[str, int] = {}

        def visit(t: TemplateEntry) -> None:
            key = os.path.normcase(t.src)
            if state.get(key) == 2:
                return
            if state.get(key) == 1:
                res.diagnostics.append(BuildDiag("error", "template bindings form a cycle", t.src, source="build"))
                return
            state[key] = 1
            if t.bind:
                dep = by_src.get(os.path.normcase(t.bind.src))
                if dep is None:
                    res.diagnostics.append(BuildDiag("error", f"bind.from {t.bind.src} is not a template of the project",
                                                     t.src, source="build"))
                else:
                    visit(dep)
            state[key] = 2
            order.append(t)

        for t in self.project.templates:
            visit(t)
        return order

    def _bind_defines(self, t: TemplateEntry, captured: dict[str, dict[int, list[dict[str, str]]]],
                      res: BuildResult) -> Optional[list[str]]:
        if not t.bind:
            return []
        b: Binding = t.bind
        caps = captured.get(os.path.normcase(b.src), {}).get(b.line)
        if not caps:
            res.diagnostics.append(BuildDiag(
                "error", f"bound line {b.line} of {self.project.rel(b.src)} did not print (no values for "
                         f"{', '.join(b.vars)})", t.src, source="build"))
            return None
        values = {}
        for v in b.vars:
            val = caps[0].get(v)
            if val is None or val == "!missing":
                res.diagnostics.append(BuildDiag(
                    "error", f"variable {v} is not set at line {b.line} of {self.project.rel(b.src)}",
                    t.src, source="build"))
                return None
            values[v] = val
        return binding_defines(t.lang, values)

    def _run_template(self, t: TemplateEntry, res: BuildResult, state: dict, new_state: dict,
                      captures: dict[str, dict[int, list[str]]], captured: dict, force: bool) -> Optional[OutputInfo]:
        proj = self.project
        gen_path = os.path.join(proj.gen_dir, t.out)
        map_path = os.path.join(proj.maps_dir, t.out + ".map.json")
        os.makedirs(os.path.dirname(gen_path), exist_ok=True)
        os.makedirs(os.path.dirname(map_path), exist_ok=True)
        info = OutputInfo(t.out, gen_path, os.path.join(proj.integ_dir, t.out), "template", t.src, t)
        extra = self._bind_defines(t, captured, res)
        if extra is None:
            return info
        info.extra_defines = list(extra)
        try:
            with open(t.src, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            res.diagnostics.append(BuildDiag("error", f"cannot read template: {exc}", t.src, source="prepro"))
            return info
        cap = captures.get(os.path.normcase(t.src), {})
        h = _hash(data, t.lang, t.syntax, t.args, t.defines, extra, t.replace, cap, proj.perl_lib, _FORMAT)
        prev = state.get("outputs", {}).get(t.out, {})
        if not force and prev.get("hash") == h and os.path.exists(gen_path) and os.path.exists(map_path):
            try:
                info.linemap = LineMap.load(map_path)
                captured[os.path.normcase(t.src)] = info.linemap.captures
                new_state["outputs"][t.out] = prev
                self._log(res, f"[prepro] {proj.rel(t.src)}: up to date")
                return info
            except (OSError, ValueError, KeyError):
                pass
        try:
            r = preprocess_file(t.src, t.options(extra), output_path=gen_path, linemap_path=map_path,
                                cwd=os.path.dirname(t.src), capture=cap or None, perl=proj.perl,
                                perl_lib=proj.perl_lib or None)
        except PreproError as exc:
            res.diagnostics.append(BuildDiag("error", str(exc).split(": ", 1)[-1], t.src, exc.line, "prepro"))
            for line in (exc.stderr or "").splitlines():
                if line.strip():
                    self._log(res, f"  {line}")
            return info
        info.linemap = r.linemap
        captured[os.path.normcase(t.src)] = r.linemap.captures
        for w in r.warnings:
            res.diagnostics.append(BuildDiag("warning", w, t.src, source="prepro"))
        if r.stderr.strip():
            for line in r.stderr.splitlines():
                self._log(res, f"  {line}")
        new_state["outputs"][t.out] = {"hash": h}
        self._log(res, f"[prepro] {proj.rel(t.src)} -> {os.path.relpath(gen_path, proj.root)}")
        return info

    def _copy_source(self, s: SourceEntry, res: BuildResult) -> OutputInfo:
        proj = self.project
        gen_path = os.path.join(proj.gen_dir, s.out)
        info = OutputInfo(s.out, gen_path, os.path.join(proj.integ_dir, s.out), "source", s.path, s)
        try:
            os.makedirs(os.path.dirname(gen_path), exist_ok=True)
            if os.path.normcase(os.path.abspath(s.path)) != os.path.normcase(os.path.abspath(gen_path)):
                shutil.copyfile(s.path, gen_path)        # (a source may already live in gen_dir)
        except OSError as exc:
            res.diagnostics.append(BuildDiag("error", f"cannot copy source: {exc}", s.path, source="build"))
        return info

    # -- integ ----------------------------------------------------------

    def _filelist_text(self, files: list[str]) -> str:
        """A filelist naming *files* (paths relative to the filelist)."""
        proj = self.project
        lines = ["// generated by rtl-integ-gui; do not edit"]
        for d in proj.include_dirs:
            lines.append(f"+incdir+{d}")
        for k, v in proj.defines.items():
            lines.append(f"+define+{k}={v}" if v != "" else f"+define+{k}")
        for d in proj.library_dirs:
            lines.append(f"-y {d}")
        if proj.libexts:
            lines.append("+libext+" + "+".join(proj.libexts))
        for f in files:
            lines.append(f.replace("\\", "/"))
        return "\n".join(lines) + "\n"

    def _stage_integ(self, res: BuildResult) -> None:
        proj = self.project
        if os.path.isdir(proj.integ_dir):
            shutil.rmtree(proj.integ_dir)
        os.makedirs(proj.integ_dir, exist_ok=True)
        outs = []
        for o in res.outputs:
            os.makedirs(os.path.dirname(o.integ_path), exist_ok=True)
            shutil.copyfile(o.gen_path, o.integ_path)
            outs.append(o.out)
        with open(os.path.join(proj.integ_dir, "design.f"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._filelist_text(outs))
        gen_f = proj.gen_filelist
        rel = [os.path.relpath(os.path.join(proj.gen_dir, o), os.path.dirname(gen_f)) for o in outs]
        with open(gen_f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._filelist_text(rel))

    def _make_designs(self, res: BuildResult, *, expand: bool) -> None:
        proj = self.project
        top = proj.top or None
        try:
            design = Design.from_filelist(os.path.join(proj.integ_dir, "design.f"), relative_to="filelist", top=top)
        except DesignError as exc:
            res.diagnostics.append(BuildDiag("error", str(exc), source="design"))
            return
        if expand:
            report = design.expand_all(log=lambda s: self._log(res, s))
            for r in report.errors():
                res.diagnostics.append(BuildDiag("error", r.error or "expansion failed", r.path, source="expand"))
            if proj.route == "collect":
                self._route(design, res)
        res.design = design
        for d in design.diagnostics:
            res.diagnostics.append(BuildDiag(d.severity, d.message, d.file, d.line, "design"))
        try:
            res.gen_design = Design.from_filelist(proj.gen_filelist, relative_to="filelist", top=top)
        except DesignError as exc:
            res.diagnostics.append(BuildDiag("warning", f"gen design: {exc}", source="design"))

    def _route(self, design: Design, res: BuildResult) -> None:
        from ..integ.route import RouteError

        try:
            collected = design.collect_auto_routes(strict=True)
            if collected.specs:
                design.apply_routes(collected.specs, then_expand=True, log=lambda s: self._log(res, s))
        except RouteError as exc:
            for d in exc.diagnostics:
                res.diagnostics.append(BuildDiag("error", str(d), source="route"))

    # -- view-only -------------------------------------------------------

    def _build_view_only(self, res: BuildResult) -> BuildResult:
        proj = self.project
        assert proj.filelist
        try:
            design = Design.from_filelist(proj.filelist, relative_to="filelist", top=proj.top or None)
        except DesignError as exc:
            res.diagnostics.append(BuildDiag("error", str(exc), source="design"))
            return res
        res.design = design
        for sf in design.files.values():
            res.outputs.append(OutputInfo(os.path.basename(sf.path), sf.path, sf.path, "file", sf.path))
        for d in design.diagnostics:
            res.diagnostics.append(BuildDiag(d.severity, d.message, d.file, d.line, "design"))
        return res

    @staticmethod
    def _load_state(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if data.get("format") == _FORMAT else {}
        except (OSError, ValueError):
            return {}
