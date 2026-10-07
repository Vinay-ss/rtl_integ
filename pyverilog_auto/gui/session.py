"""GUI session: the open project, its last build, and the queries the UI makes.

Transport-free so it can be driven from tests; :mod:`.server` exposes the
public methods over JSON lines.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from typing import Callable, Optional

from ..integ.model import Instance
from .build import Builder, BuildResult
from .project import MANIFEST_NAME, Project, load_project, view_only_project
from .srcmap import SourceMap


def _file_hash(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


class SessionError(RuntimeError):
    def __init__(self, message: str, code: str = "E_SESSION"):
        super().__init__(message)
        self.code = code


Notify = Callable[[str, dict], None]


class Session:
    def __init__(self, notify: Optional[Notify] = None):
        self.notify: Notify = notify or (lambda method, params: None)
        self.project: Optional[Project] = None
        self.result: Optional[BuildResult] = None
        self.srcmap: Optional[SourceMap] = None
        self.ops = None   # set by the ops layer (plans, journal)
        self.inputs: dict[str, Optional[str]] = {}   # file -> hash the last build read

    # -- logging ----------------------------------------------------------

    def log(self, text: str, level: str = "info") -> None:
        self.notify("log", {"text": text, "level": level})

    # -- project / build ----------------------------------------------------

    def open(self, project: Optional[str] = None, filelist: Optional[str] = None,
             top: Optional[str] = None) -> dict:
        if project:
            path = project
            if os.path.isdir(path):
                path = os.path.join(path, MANIFEST_NAME)
            self.project = load_project(path)
            if top:
                self.project.top = [top]
        elif filelist:
            self.project = view_only_project(filelist, top=top)
        else:
            found = _find_manifest(os.getcwd())
            if found is None:
                raise SessionError(f"no {MANIFEST_NAME} here or above; pass a project or a filelist", "E_NO_PROJECT")
            self.project = load_project(found)
        self.log(f"project: {self.project.path or self.project.filelist}")
        return self.build()

    def _need_project(self) -> Project:
        if self.project is None:
            raise SessionError("no project is open", "E_NO_PROJECT")
        return self.project

    def _need_build(self) -> BuildResult:
        if self.result is None or self.result.design is None:
            raise SessionError("the design is not built (see the console for errors)", "E_NOT_BUILT")
        return self.result

    def reload_project(self) -> None:
        """Re-read the manifest (after an operation added or removed entries)."""
        proj = self._need_project()
        if proj.path is None:
            return
        top = proj.top
        self.project = load_project(proj.path)
        if top and not self.project.top:
            self.project.top = top

    def build(self, force: bool = False, reload: bool = False) -> dict:
        if reload:
            self.reload_project()
        proj = self._need_project()
        self.notify("progress", {"stage": "build", "state": "start"})
        self.inputs = self._snapshot_inputs()
        res = Builder(proj, log=self.log).build(force=force)
        self.result = res
        self.srcmap = SourceMap(res) if res.design is not None else None
        if res.design is not None:
            try:
                res.design.hierarchy()
            except Exception as exc:  # pragma: no cover - defensive
                self.log(f"elaboration failed: {exc}", "error")
        for d in res.diagnostics:
            if d.severity in ("error", "warning"):
                self.log(str(d), d.severity)
        summary = self.summary()
        self.notify("built", summary)
        return summary

    def _input_files(self) -> list[str]:
        proj = self._need_project()
        files = [t.src for t in proj.templates] + [s.path for s in proj.sources]
        if proj.path:
            files.append(proj.path)
        return files

    def _snapshot_inputs(self) -> dict[str, Optional[str]]:
        return {f: _file_hash(f) for f in self._input_files()}

    def changed_inputs(self) -> list[str]:
        """Project files (templates, sources, manifest) whose contents differ
        from what the last build read."""
        if self.project is None or self.project.view_only:
            return []
        return [f for f, h in self.inputs.items() if _file_hash(f) != h]

    def summary(self) -> dict:
        proj = self._need_project()
        res = self.result
        return {
            "project": proj.path,
            "root": proj.root,
            "view_only": proj.view_only,
            "ok": bool(res and res.ok),
            "top": [r.path for r in res.design.hierarchy()] if res and res.design else [],
            "errors": sum(1 for d in (res.diagnostics if res else []) if d.severity == "error"),
            "warnings": sum(1 for d in (res.diagnostics if res else []) if d.severity == "warning"),
            "templates": [{"src": t.src, "lang": t.lang, "syntax": t.syntax} for t in proj.templates],
        }

    def diagnostics(self) -> list[dict]:
        res = self.result
        return [d.to_json() for d in res.diagnostics] if res else []

    # -- tree -----------------------------------------------------------------

    def _wrapper_modules(self) -> set[str]:
        res = self._need_build()
        names: set[str] = set()
        for o in res.outputs:
            if o.kind in ("template", "source") and getattr(o.entry, "created_by_gui", False):
                for m in res.design.modules.values():
                    if os.path.normcase(res.design.files[m.file].path) == os.path.normcase(o.integ_path):
                        names.add(m.name)
        return names

    def _module_files(self, name: str, cache: dict[str, dict]) -> dict:
        """Project-relative file defining module *name* in each view."""
        if name not in cache:
            assert self.srcmap is not None
            proj = self._need_project()
            cache[name] = {v: proj.rel(loc.path) for v, loc in self.srcmap.module_locs(name).items()}
        return cache[name]

    def _node(self, inst: Instance, wrappers: set[str], files: dict[str, dict]) -> dict:
        assert self.srcmap is not None
        st = self.srcmap.statement_of(inst) if inst.parent is not None else None
        return {
            "path": inst.path,
            "name": inst.name,
            "module": inst.module_name,
            "files": self._module_files(inst.module_name, files),
            "class": st.cls if st else "TOP",
            "tag": st.tag() if st else "",
            "reason": st.reason if st else "",
            "blackbox": inst.is_blackbox,
            "iface": inst.is_interface,
            "wrapper": inst.module_name in wrappers,
            "children": [self._node(c, wrappers, files) for c in inst.children],
        }

    def tree(self) -> dict:
        res = self._need_build()
        wrappers = self._wrapper_modules()
        files: dict[str, dict] = {}
        return {"roots": [self._node(r, wrappers, files) for r in res.design.hierarchy()]}

    def instance(self, path: str) -> Instance:
        res = self._need_build()
        inst = res.design.instance(path)
        if inst is None:
            raise SessionError(f"no instance {path}", "E_NO_INSTANCE")
        return inst

    def node_info(self, path: str) -> dict:
        inst = self.instance(path)
        assert self.srcmap is not None
        sm = self.srcmap
        m = inst.module
        st = sm.statement_of(inst) if inst.parent is not None else None
        ports = []
        if m is not None:
            for p in m.ports:
                ports.append({"name": p.name, "dir": p.direction, "type": p.type_text, "dims": p.packed_dims,
                              "iface": p.iface_type, "modport": p.modport})
        return {
            "path": inst.path,
            "name": inst.name,
            "module": inst.module_name,
            "parent": inst.parent.path if inst.parent else None,
            "blackbox": inst.is_blackbox,
            "statement": st.to_json() if st else None,
            "inst_locs": {k: v.to_json() for k, v in sm.instance_locs(inst).items()} if inst.parent else {},
            "module_locs": {k: v.to_json() for k, v in sm.module_locs(inst.module_name).items()},
            "ports": ports,
            "params": list(m.params) if m else [],
            "children": [c.path for c in inst.children],
        }

    def locate(self, path: str, what: str = "module", view: str = "template") -> dict:
        """File and line to show for *path*: its module definition or its
        instantiation statement, in the requested view (falling back to the
        nearest available one)."""
        inst = self.instance(path)
        assert self.srcmap is not None
        if what == "inst" and inst.parent is not None:
            locs = self.srcmap.instance_locs(inst)
        else:
            locs = self.srcmap.module_locs(inst.module_name)
        order = {"template": ["template", "integ", "gen"], "gen": ["gen", "integ", "template"],
                 "integ": ["integ", "gen", "template"]}.get(view, ["template", "integ", "gen"])
        for v in order:
            if v in locs:
                out = locs[v].to_json()
                out["fallback"] = v != view
                return out
        raise SessionError(f"no source location for {path}", "E_NO_LOCATION")

    # -- console --------------------------------------------------------------

    def console_cmd(self, text: str) -> dict:
        """Reserved console hook: a few read-only commands for now."""
        try:
            argv = shlex.split(text, posix=True)
        except ValueError as exc:
            return {"output": f"error: {exc}"}
        if not argv:
            return {"output": ""}
        cmd, args = argv[0], argv[1:]
        if cmd in ("help", "?"):
            return {"output": "commands: help | build | diag | find REGEX | info PATH\n"
                              "  find: instance paths full-matched from the top, e.g. top.*u_lane.*"}
        if cmd == "build":
            s = self.build(force="-f" in args)
            return {"output": f"build {'ok' if s['ok'] else 'FAILED'}: {s['errors']} error(s), "
                              f"{s['warnings']} warning(s)", "refresh": True}
        if cmd == "diag":
            lines = [f"{d['severity']}: {d['file'] or ''}{':' + str(d['line']) if d['line'] else ''} {d['message']}"
                     for d in self.diagnostics()]
            return {"output": "\n".join(lines) or "no diagnostics"}
        if cmd == "find":
            if not args:
                return {"output": "usage: find REGEX"}
            try:
                rx = re.compile(args[0])
            except re.error as exc:
                return {"output": f"bad regex: {exc}"}
            res = self._need_build()
            hits = [i.path for i in res.design.all_instances() if rx.fullmatch(i.path)]
            return {"output": "\n".join(hits) or "no match", "paths": hits}
        if cmd == "info":
            if not args:
                return {"output": "usage: info PATH"}
            info = self.node_info(args[0])
            st = info["statement"] or {}
            lines = [f"{info['path']} : {info['module']}",
                     f"  class: {st.get('class', 'TOP')} {st.get('reason', '')}".rstrip()]
            for p in info["ports"]:
                lines.append(f"  {p['dir'] or 'iface':6} {p['dims'] or ''} {p['name']}")
            return {"output": "\n".join(lines)}
        return {"output": f"unknown command {cmd!r} (try help)"}


def _find_manifest(start: str) -> Optional[str]:
    d = os.path.abspath(start)
    while True:
        cand = os.path.join(d, MANIFEST_NAME)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
