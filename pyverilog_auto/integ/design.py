"""Design — the facade over files, modules, instances, order and expansion."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Optional, Union

from ..config import VerilogConfig
from .filelist import Filelist, filelist_to_config, parse_filelist
from .frontend_text import FrontendOptions, TextFrontend, build_static_tree
from .graph import Order, compute_order
from .model import Diag, Instance, ModuleDef, ModuleRef
from .sources import SourceFile

if TYPE_CHECKING:
    from ..library.module_db import ModuleDatabase
    from ..signal import ModDecls

Backend = Literal["auto", "slang", "text"]
InstanceLike = Union[Instance, str]


class DesignError(RuntimeError):
    pass


class Design:
    """A set of RTL files with their module definitions and instance tree."""

    def __init__(
        self,
        fl: Filelist,
        *,
        backend: Backend = "auto",
        top: Optional[Union[str, Iterable[str]]] = None,
        strict: bool = False,
        directive_policy: str = "text",
        lib_patterns: Iterable[str] = (),
        src_patterns: Iterable[str] = (),
        typedef_regexp: Optional[str] = None,
        base_config: Optional[VerilogConfig] = None,
    ) -> None:
        self.filelist = fl
        self.strict = strict
        self.directive_policy = directive_policy
        self.typedef_regexp = typedef_regexp
        tops: list[str] = []
        if isinstance(top, str):
            tops = [top]
        elif top:
            tops = list(top)
        self.tops_requested: list[str] = tops or list(fl.tops)
        self.lib_patterns = [re.compile(p) for p in lib_patterns]
        self.src_patterns = [re.compile(p) for p in src_patterns]
        self.base_config: VerilogConfig = filelist_to_config(fl, base_config)
        self.diagnostics: list[Diag] = []
        self.files: dict[str, SourceFile] = {}
        self.file_order: list[str] = []
        self.modules: dict[str, ModuleDef] = {}
        self.duplicates: dict[str, list[str]] = {}
        self.unresolved: dict[str, list[ModuleRef]] = {}
        self._graph: dict[str, set[str]] = {}
        self._order: Optional[Order] = None
        self._roots: Optional[list[Instance]] = None
        self._instances: Optional[dict[str, Instance]] = None
        self._decls_cache: dict[tuple, "ModDecls"] = {}   # (file, version, module, typedef_regexp)
        self.backend: str = "text"
        self.frontend = self._make_frontend(backend)
        self._opts = FrontendOptions(
            directive_policy=directive_policy, strict=strict, libexts=fl.effective_libexts,
            defines=dict(fl.defines), include_dirs=list(fl.include_dirs), typedef_regexp=typedef_regexp,
        )
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_filelist(cls, path: str, *, relative_to: str = "cwd", extra_files: Iterable[str] = (), **kw) -> "Design":
        fl = parse_filelist(path, relative_to=relative_to)  # type: ignore[arg-type]
        if extra_files:
            fl.merge(Filelist.from_args(list(extra_files)))
        return cls.from_filelist_obj(fl, **kw)

    @classmethod
    def from_files(
        cls,
        files: Iterable[str],
        *,
        library_dirs: Iterable[str] = (),
        library_files: Iterable[str] = (),
        include_dirs: Iterable[str] = (),
        defines: Optional[dict[str, str]] = None,
        libexts: Optional[Iterable[str]] = None,
        base_dir: Optional[str] = None,
        **kw,
    ) -> "Design":
        fl = Filelist.from_args(list(files), include_dirs=include_dirs, library_dirs=library_dirs,
                                library_files=library_files, libexts=libexts, defines=defines, base_dir=base_dir)
        return cls.from_filelist_obj(fl, **kw)

    @classmethod
    def from_filelist_obj(cls, fl: Filelist, **kw) -> "Design":
        return cls(fl, **kw)

    def _make_frontend(self, backend: Backend):
        from . import is_slang_available

        if backend in ("auto", "slang") and is_slang_available():
            try:
                from .frontend_slang import SlangFrontend

                fe = SlangFrontend()
                self.backend = "slang"
                return fe
            except Exception as exc:  # pragma: no cover - depends on environment
                if backend == "slang":
                    raise DesignError(f"pyslang backend unavailable: {exc}") from exc
                self.diagnostics.append(Diag("warning", f"pyslang backend unavailable ({exc}); using text backend"))
        elif backend == "slang":
            raise DesignError("pyslang is not installed (pip install pyverilog-auto[integ])")
        self.backend = "text"
        return TextFrontend()

    def _build(self) -> None:
        fl = self.filelist
        for e in fl.missing:
            self.diagnostics.append(Diag("error", f"not found: {e.token}", e.origin, e.line or None, "filelist"))
        for e in fl.unknown:
            self.diagnostics.append(Diag("warning", f"ignored unknown flag {e.token}", e.origin, e.line or None, "filelist"))
        for w in fl.warnings:
            self.diagnostics.append(Diag("warning", w, None, None, "filelist"))
        self.files = self.frontend.load(fl, self._opts)
        self.diagnostics.extend(self.frontend.diagnostics)
        self.frontend.diagnostics.clear()
        for sf in self.files.values():
            self._apply_role_overrides(sf)
        self.file_order = list(self.files.keys())
        for key in list(self.file_order):
            self._scan_file(self.files[key])
        self._discover_libraries()
        self._resolve_refs()
        self._rebuild_graph()

    def _apply_role_overrides(self, sf: SourceFile) -> None:
        for pat in self.src_patterns:
            if pat.search(sf.path):
                sf.role = "source"
                return
        for pat in self.lib_patterns:
            if pat.search(sf.path):
                sf.role = "library"
                return

    def _scan_file(self, sf: SourceFile) -> list[ModuleDef]:
        defs = self.frontend.scan_definitions(sf, self._opts)
        for d in defs:
            d.is_library = sf.role == "library"
            if d.name in self.modules and self.modules[d.name].file != sf.key:
                self.duplicates.setdefault(d.name, [self.modules[d.name].file]).append(sf.key)
                self.diagnostics.append(Diag(
                    "warning", f"duplicate definition of {d.name} (first in {self.modules[d.name].file})",
                    sf.path, d.keyword_range.line, "design"))
                continue
            self.modules[d.name] = d
        return defs

    def _discover_libraries(self) -> None:
        """Resolve missing module names through -y dirs x libexts (also for AUTO-marker refs)."""
        fl = self.filelist
        if not fl.library_dirs:
            return
        exts = fl.effective_libexts
        while True:
            missing = sorted({ref.module for mod in self.modules.values() for ref in mod.refs
                              if ref.module not in self.modules and not ref.module.startswith("`")})
            found_any = False
            for name in missing:
                for d in fl.library_dirs:
                    hit = None
                    for ext in exts:
                        cand = os.path.join(d, name + ext)
                        if os.path.isfile(cand):
                            hit = cand
                            break
                    if hit is None:
                        continue
                    key = os.path.normcase(os.path.normpath(os.path.abspath(hit)))
                    if key in self.files:
                        break
                    sf = self.frontend.load_extra(self.files, hit, "library")
                    if sf is None:
                        break
                    self._apply_role_overrides(sf)
                    self.file_order.append(sf.key)
                    self._scan_file(sf)
                    found_any = True
                    break
            if not found_any:
                break

    def _resolve_refs(self) -> None:
        self.unresolved = {}
        defines = self.filelist.defines
        for mod in self.modules.values():
            for ref in mod.refs:
                name = ref.module
                if name.startswith("`"):
                    name = defines.get(name[1:], name)
                    ref.module = name
                target = self.modules.get(name)
                ref.resolved = target
                if target is None:
                    self.unresolved.setdefault(name, []).append(ref)

    def _rebuild_graph(self) -> None:
        graph: dict[str, set[str]] = {k: set() for k in self.file_order}
        for mod in self.modules.values():
            for ref in mod.refs:
                if ref.resolved is not None and ref.resolved.file != mod.file:
                    graph.setdefault(mod.file, set()).add(ref.resolved.file)
        for k in self.files:
            graph.setdefault(k, set())
        self._graph = graph
        self._order = compute_order({k: sorted(v, key=self._rank) for k, v in graph.items()},
                                    list(self.file_order))

    def _rank(self, key: str) -> int:
        try:
            return self.file_order.index(key)
        except ValueError:
            return len(self.file_order)

    # ------------------------------------------------------------------
    # Files and modules
    # ------------------------------------------------------------------

    def file(self, path_or_key: str) -> SourceFile:
        key = os.path.normcase(os.path.normpath(os.path.abspath(path_or_key)))
        try:
            return self.files[key]
        except KeyError:
            raise DesignError(f"{path_or_key} is not part of the design") from None

    def text_of(self, path_or_key: str) -> str:
        return self.file(path_or_key).text

    def file_bytes(self, path_or_key: str) -> bytes:
        return self.file(path_or_key).data

    def file_stamp(self, path_or_key: str) -> tuple[int, int]:
        return self.file(path_or_key).stamp()

    def source_files(self) -> list[SourceFile]:
        return [sf for sf in self.files.values() if sf.role == "source"]

    def modules_in(self, path_or_key: str) -> list[ModuleDef]:
        key = self.file(path_or_key).key
        return [m for m in self.modules.values() if m.file == key]

    def module_at(self, path_or_key: str, offset: int) -> Optional[ModuleDef]:
        for m in self.modules_in(path_or_key):
            if m.keyword_range.start <= offset < m.end_range.end:
                return m
        return None

    def dependencies(self, path_or_key: str) -> list[str]:
        return sorted(self._graph.get(self.file(path_or_key).key, ()))

    # ------------------------------------------------------------------
    # Order
    # ------------------------------------------------------------------

    def order(self) -> Order:
        assert self._order is not None
        return self._order

    def levels(self) -> list[list[str]]:
        return self.order().levels

    # ------------------------------------------------------------------
    # Hierarchy
    # ------------------------------------------------------------------

    def hierarchy(self, top: Optional[str] = None) -> list[Instance]:
        tops = [top] if top else (self.tops_requested or None)
        if self._roots is None or top:
            diags: list[Diag] = []
            try:
                roots = self.frontend.elaborate(self, tops)
            except Exception as exc:  # pragma: no cover - defensive
                self.diagnostics.append(Diag("warning", f"elaboration failed ({exc}); using static tree", None, None, "elab"))
                roots = build_static_tree(self, tops, diags)
            self.diagnostics.extend(diags)
            self.diagnostics.extend(self.frontend.diagnostics)
            self.frontend.diagnostics.clear()
            if top:
                return roots
            self._roots = roots
            self._instances = {i.path: i for r in roots for i in r.walk()}
        return self._roots

    def all_instances(self) -> list[Instance]:
        self.hierarchy()
        assert self._instances is not None
        return list(self._instances.values())

    def instance(self, path: str) -> Optional[Instance]:
        self.hierarchy()
        assert self._instances is not None
        return self._instances.get(path)

    def find_instances(self, pattern: str, *, mode: str = "search") -> list[Instance]:
        rx = re.compile(pattern)
        match = rx.fullmatch if mode == "fullmatch" else rx.search
        return [i for i in self.all_instances() if match(i.path)]

    def find_instance_matches(self, pattern: str) -> list[tuple[Instance, "re.Match[str]"]]:
        """Full-match *pattern* against every instance path; return matches with groups."""
        rx = re.compile(pattern)
        out = []
        for i in self.all_instances():
            m = rx.fullmatch(i.path)
            if m:
                out.append((i, m))
        return out

    def instances_of(self, module_name: str) -> list[Instance]:
        self.hierarchy()
        mod = self.modules.get(module_name)
        return list(mod.instances) if mod else []

    def _inst(self, x: InstanceLike) -> Instance:
        if isinstance(x, Instance):
            return x
        inst = self.instance(x)
        if inst is None:
            raise DesignError(f"no instance {x!r}")
        return inst

    def lca(self, a: InstanceLike, b: InstanceLike) -> Optional[Instance]:
        ia, ib = self._inst(a), self._inst(b)
        pa = ia.ancestors() + [ia]
        pb = ib.ancestors() + [ib]
        lca: Optional[Instance] = None
        for x, y in zip(pa, pb):
            if x is y:
                lca = x
            else:
                break
        return lca

    def chain(self, ancestor: InstanceLike, descendant: InstanceLike) -> list[Instance]:
        anc, desc = self._inst(ancestor), self._inst(descendant)
        path = desc.ancestors() + [desc]
        try:
            i = next(k for k, n in enumerate(path) if n is anc)
        except StopIteration:
            raise DesignError(f"{anc.path} is not an ancestor of {desc.path}") from None
        return path[i:]

    def parents_using_autoinst(self, module_name: str) -> list[ModuleRef]:
        return [ref for mod in self.modules.values() for ref in mod.refs
                if ref.kind == "inst" and ref.module == module_name and ref.uses_autoinst]

    # ------------------------------------------------------------------
    # Config and module database
    # ------------------------------------------------------------------

    def make_config(self, path_or_key: Optional[str] = None) -> VerilogConfig:
        cfg = deepcopy(self.base_config)
        if path_or_key:
            sf = self.file(path_or_key)
            d = os.path.dirname(sf.path)
            if d not in cfg.library_directories:
                cfg.library_directories = [d] + list(cfg.library_directories)
        return cfg

    def effective_config(self, path_or_key: str) -> VerilogConfig:
        from ..buffer import VerilogBuffer
        from ..local_vars import apply_local_vars, parse_local_vars

        sf = self.file(path_or_key)
        cfg = self.make_config(path_or_key)
        buf = VerilogBuffer.from_string(sf.text, sf.path)
        lv = parse_local_vars(buf)
        if lv:
            cfg = apply_local_vars(cfg, lv)
        return cfg

    @property
    def reader(self):
        return self.frontend.reader(self)

    def module_db_factory(self) -> Callable[[VerilogConfig, str], "ModuleDatabase"]:
        from .database import DesignModuleDatabase

        reader = self.reader

        def factory(cfg: VerilogConfig, current_file: str) -> "ModuleDatabase":
            return DesignModuleDatabase(cfg, current_file, self, reader)

        return factory

    # ------------------------------------------------------------------
    # Refresh after edits / expansion
    # ------------------------------------------------------------------

    def refresh(self, paths: Iterable[str]) -> None:
        keys = [self.file(p).key for p in paths]
        if not keys:
            return
        for key in keys:
            sf = self.files[key]
            self.frontend.refresh(sf)
            for name in [n for n, m in self.modules.items() if m.file == key]:
                del self.modules[name]
            self._scan_file(sf)
            self._decls_cache = {k: v for k, v in self._decls_cache.items() if k[0] != key}
        self._resolve_refs()
        self._roots = None
        self._instances = None
        self.frontend.invalidate() if hasattr(self.frontend, "invalidate") else None

    # ------------------------------------------------------------------
    # Expansion / routing (implemented in sibling modules)
    # ------------------------------------------------------------------

    def expand_all(self, **kw):
        """Leaf-first AUTO expansion of every source file (see ``Integrator``).

        ``strip_autos=True`` runs :func:`~pyverilog_auto.auto.strip.strip_autos`
        once, after all passes, over every processed ``source`` file (also the
        ones the expansion left unchanged, so a re-run still strips).  Files
        whose expansion failed are not stripped.  With ``dry_run``/``diff``
        nothing is written and the strip diffs are added to the report as
        results of pass ``report.passes + 1``.
        """
        from .orchestrator import Integrator

        strip = bool(kw.pop("strip_autos", False))
        report = Integrator(self, **kw).run()
        if strip:
            self._strip_after_expand(report, dry_run=bool(kw.get("dry_run") or kw.get("diff")),
                                     log=kw.get("log"))
        return report

    def strip_autos(self, files: Optional[Iterable[Union[str, "os.PathLike[str]"]]] = None, *,
                    dry_run: bool = False) -> list[Path]:
        """Remove every AUTO attribute from *files* (default: all source files).

        Files are rewritten in place byte-exactly apart from the stripped text
        (CRLF stays CRLF); unchanged files are not touched.  With *dry_run*
        only the in-memory overlay is updated.  Returns the changed paths.
        """
        if files is None:
            keys = [sf.key for sf in self.source_files()]
        else:
            keys = []
            for f in files:
                k = self.file(os.fspath(f)).key
                if k not in keys:
                    keys.append(k)
        return [Path(sf.path) for sf, _old in self._strip_keys(keys, dry_run=dry_run)]

    def _strip_keys(self, keys: Iterable[str], *, dry_run: bool) -> list[tuple[SourceFile, str]]:
        """Strip the files *keys*; return ``(file, text before)`` for the changed ones."""
        from ..auto.strip import strip_autos

        changed: list[tuple[SourceFile, str]] = []
        for key in keys:
            sf = self.files[key]
            raw = sf.data.decode("utf-8", "surrogateescape")
            new = strip_autos(raw)
            if new == raw:
                continue
            data = new.encode("utf-8", "surrogateescape")
            old_text = sf.text
            if not dry_run:
                with open(sf.path, "wb") as fh:
                    fh.write(data)
            sf.update(data.decode("utf-8", "replace"))
            if not dry_run:
                sf.refresh_stamp()
            changed.append((sf, old_text))
        if changed:
            self.refresh([sf.path for sf, _old in changed])
        return changed

    def _strip_after_expand(self, report, *, dry_run: bool, log=None) -> None:
        import difflib

        from .orchestrator import FileResult

        log = log or (lambda s: None)
        errored = {r.key for r in report.errors()}
        keys: list[str] = []
        for r in report.results:
            if r.skipped or r.key in errored or r.key in keys or self.files[r.key].role != "source":
                continue
            keys.append(r.key)
        before = {sf.key: old for sf, old in self._strip_keys(keys, dry_run=dry_run)}
        pass_no = report.passes + 1
        level_of = report.order.level_of if report.order is not None else {}
        for key in keys:
            sf = self.files[key]
            res = FileResult(sf.path, key, level_of.get(key, 0), pass_no)
            if key in before:
                res.changed = True
                res.written = not dry_run
                if dry_run:
                    res.diff = "".join(difflib.unified_diff(
                        before[key].splitlines(keepends=True), sf.text.splitlines(keepends=True),
                        fromfile=f"a/{sf.path}", tofile=f"b/{sf.path}"))
            report.results.append(res)
            state = ("stripped" + ("" if dry_run else ", written")) if res.changed else "unchanged"
            log(f"[strip] {sf.path}: {state}")

    def port_at(self, instance: InstanceLike, port: str):
        fn = getattr(self.frontend, "port_at", None)
        if fn is None:
            raise DesignError("port_at requires the pyslang backend")
        return fn(self, self._inst(instance), port)

    def plan_routes(self, specs, *, strict: bool = False):
        """Validate route specs and return the planned edits (no side effects)."""
        from .route_plan import RoutePlanner

        return RoutePlanner(self, list(specs), strict=strict).plan()

    def apply_routes(self, specs, *, dry_run: bool = False, strict: bool = False, then_expand: bool = False, log=None):
        """Plan, validate and apply routes; optionally run leaf-first expansion afterwards."""
        from .route_plan import apply_routes

        return apply_routes(self, list(specs), dry_run=dry_run, strict=strict, then_expand=then_expand, log=log)

    def route(self, spec, *, dry_run: bool = False, strict: bool = False, then_expand: bool = False):
        """Apply one route (``RouteSpec``, mapping, or ``"SRC -> DST"``)."""
        report = self.apply_routes([spec], dry_run=dry_run, strict=strict, then_expand=then_expand)
        return report.results[0] if report.results else None

    def collect_auto_routes(self, *, strict: bool = True):
        """Collect ``//auto_route [INSTPATH:]PORT :: to|from :: TARGETS`` annotations into route specs."""
        from .auto_route import collect_auto_routes

        return collect_auto_routes(self, strict=strict)

    def auto_route(self, module: str, left: str, direction: str, targets, *, create_dst: Optional[bool] = None):
        """Resolve ``//auto_route LEFT :: DIRECTION :: TARGETS`` as if written in *module*.

        Same resolver as the in-source annotations, without editing any file:
        ``d.auto_route("core_b", "instE:e_busy", "to", "instF:f_hold")``.
        *left* is ``PORT``, ``INSTPATH:PORT`` or ``re:REGEX:PORT``; *targets* a
        list or a comma-separated string.  *create_dst* overrides the mode
        default (False for ``INSTPATH:PORT`` unless the far end is an
        ancestor).  Returns a ``CollectResult``; errors are in ``.errors``,
        the specs (``.specs``) go to :meth:`apply_routes`.
        """
        from .auto_route import auto_route

        self.hierarchy()
        if module not in self.modules:
            raise DesignError(f"no module {module!r}")
        return auto_route(self, module, left, direction, targets, create_dst=create_dst)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        order = self.order()
        files = []
        for key in order.files:
            sf = self.files[key]
            files.append({
                "path": sf.path, "role": sf.role, "level": order.level_of.get(key, 0),
                "modules": sf.modules, "deps": [self.files[d].path for d in sorted(self._graph.get(key, ()))],
            })
        modules = [{
            "name": m.name, "kind": m.kind, "file": self.files[m.file].path, "ansi": m.ansi,
            "is_library": m.is_library, "needs_text_parser": m.needs_text_parser,
            "ports": [p.name for p in m.ports],
            "refs": [{"module": r.module, "kind": r.kind, "inst": r.inst_name, "resolved": r.resolved is not None}
                     for r in m.refs],
        } for m in self.modules.values()]
        instances = [{
            "path": i.path, "module": i.module_name, "file": self.files[i.file].path if i.file else None,
            "parent": i.parent.path if i.parent else None, "is_blackbox": i.is_blackbox,
            "is_interface": i.is_interface,
        } for i in self.all_instances()]
        return {
            "backend": self.backend,
            "files": files,
            "modules": modules,
            "instances": instances,
            "order": [self.files[k].path for k in order.files],
            "levels": [[self.files[k].path for k in lvl] for lvl in order.levels],
            "cycles": [[self.files[k].path for k in c] for c in order.cycles],
            "unresolved": sorted(self.unresolved),
            "diagnostics": [str(d) for d in self.diagnostics],
        }
