"""CLI handlers for the design-level commands (hierarchy, integrate, route)."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from typing import Optional, TextIO

from .design import Design, DesignError
from .filelist import Filelist, parse_filelist
from .model import Instance


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

def rel(path: str) -> str:
    """Path relative to cwd when it is inside it, else unchanged."""
    try:
        r = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return path
    return path if r.startswith("..") else r


def build_filelist(args: argparse.Namespace) -> Filelist:
    fl = Filelist()
    for ff in args.flagfiles:
        parse_filelist(ff, relative_to=args.relative_to, fl=fl)
    defines: dict[str, str] = {}
    for d in getattr(args, "defines", []) or []:
        name, _, value = d.partition("=")
        defines[name] = value
    exts = None
    if getattr(args, "libexts", None):
        exts = [e for e in args.libexts.split("+") if e]
    fl.merge(Filelist.from_args(
        list(args.files), include_dirs=args.incdirs, library_dirs=args.libdirs,
        library_files=args.libfiles, libexts=exts, defines=defines, tops=getattr(args, "tops", []) or [],
    ))
    return fl


def build_base_config(args: argparse.Namespace):
    """Design base config from the CLI flags (``--inst-*``).

    Starts from the defaults, so without flags the design behaves exactly as
    with no base config; per-file Local Variables override these values.
    """
    from ..cli import apply_inst_format_args
    from ..config import VerilogConfig

    return apply_inst_format_args(VerilogConfig(), args)


def build_design(args: argparse.Namespace) -> Optional[Design]:
    fl = build_filelist(args)
    problems = fl.problems()
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if problems and args.strict:
        return None
    if not fl.sources and not fl.library_files:
        print("error: no input files (give FILE... or -f FILELIST)", file=sys.stderr)
        return None
    backend = "text" if getattr(args, "no_slang", False) else "auto"
    try:
        return Design.from_filelist_obj(
            fl, backend=backend, top=(fl.tops or None), strict=args.strict,
            lib_patterns=getattr(args, "lib_patterns", []) or [],
            src_patterns=getattr(args, "src_patterns", []) or [],
            base_config=build_base_config(args),
        )
    except DesignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def report_diagnostics(design: Design, args: argparse.Namespace, out: TextIO = sys.stderr) -> int:
    """Print diagnostics; return the number that count as errors."""
    errors = 0
    seen: set[str] = set()
    for d in design.diagnostics:
        s = str(d)
        if s in seen:
            continue
        seen.add(s)
        if d.severity == "error" or (getattr(args, "warn_fatal", False) and d.severity == "warning"):
            errors += 1
        print(s, file=out)
    return errors


# ----------------------------------------------------------------------
# hierarchy
# ----------------------------------------------------------------------

def _inst_tag(design: Design, inst: Instance) -> str:
    tags = []
    if inst.is_blackbox:
        tags.append("blackbox")
    if inst.is_interface:
        tags.append("interface")
    if inst.module is not None and inst.module.is_library:
        tags.append("lib")
    where = rel(design.files[inst.file].path) if inst.file else "?"
    t = f" [{', '.join(tags)}]" if tags else ""
    return f" : {inst.module_name}{t}  ({where})"


def format_tree(design: Design, roots: list[Instance]) -> list[str]:
    lines: list[str] = []

    def walk(inst: Instance, prefix: str, is_last: bool, is_root: bool) -> None:
        if is_root:
            lines.append(f"{inst.name}{_inst_tag(design, inst)}")
            child_prefix = ""
        else:
            conn = "`-- " if is_last else "|-- "
            lines.append(f"{prefix}{conn}{inst.name}{_inst_tag(design, inst)}")
            child_prefix = prefix + ("    " if is_last else "|   ")
        for i, c in enumerate(inst.children):
            walk(c, child_prefix, i == len(inst.children) - 1, False)

    for r in roots:
        walk(r, "", True, True)
    return lines


def format_levels(design: Design) -> list[str]:
    order = design.order()
    lines: list[str] = []
    for lvl, keys in enumerate(order.levels):
        label = "level 0 (leaves):" if lvl == 0 else f"level {lvl}:"
        lines.append(label)
        for k in keys:
            sf = design.files[k]
            mods = ", ".join(sf.modules) if sf.modules else "-"
            lines.append(f"  {rel(sf.path):<40} [{sf.role}]  {mods}")
    if order.cycles:
        lines.append("cycles (files depending on each other; processed twice):")
        for comp in order.cycles:
            lines.append("  " + " <-> ".join(rel(design.files[k].path) for k in comp))
    if design.unresolved:
        lines.append("unresolved (black boxes): " + ", ".join(sorted(design.unresolved)))
    return lines


def format_order(design: Design) -> list[str]:
    order = design.order()
    lines: list[str] = []
    for i, k in enumerate(order.files, 1):
        sf = design.files[k]
        lines.append(f"{i:3d}. L{order.level_of.get(k, 0)} {rel(sf.path):<40} [{sf.role}]")
    return lines


def cmd_hierarchy(args: argparse.Namespace) -> int:
    design = build_design(args)
    if design is None:
        return 1
    roots = design.hierarchy()
    if args.json:
        json.dump(design.to_json(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        view = args.view
        out: list[str] = []
        if view in ("tree", "all"):
            out.extend(format_tree(design, roots))
        if view in ("levels", "all"):
            if out:
                out.append("")
            out.extend(format_levels(design))
        if view in ("order", "all"):
            if out:
                out.append("")
            out.extend(format_order(design))
        sys.stdout.write("\n".join(out) + "\n")
    errors = report_diagnostics(design, args)
    if args.strict and (errors or design.unresolved):
        return 2
    return 0


# ----------------------------------------------------------------------
# integrate
# ----------------------------------------------------------------------

def cmd_route(args: argparse.Namespace) -> int:
    from .route import RouteError, RouteSpec
    from .routes_file import load_routes

    specs: list = []
    try:
        if args.routes:
            specs.extend(load_routes(args.routes))
        for src, dst in args.route or []:
            specs.append(RouteSpec(src=src, dst=dst, comment=not args.no_comment))
    except RouteError as exc:
        for d in exc.diagnostics:
            print(str(d), file=sys.stderr)
        return 2
    collect = bool(args.collect or args.collect_only or args.routes_out)
    if not specs and not collect:
        print("error: no routes given (--routes FILE, --route SRC DST, or --collect)", file=sys.stderr)
        return 1
    if getattr(args, "no_slang", False):
        print("error: route needs the pyslang backend (do not pass --no-slang)", file=sys.stderr)
        return 1
    design = build_design(args)
    if design is None:
        return 1
    if design.backend != "slang":
        print("error: route needs the pyslang backend (pip install pyverilog-auto[integ])", file=sys.stderr)
        return 1
    quiet = getattr(args, "quiet", False)
    log = (lambda s: None) if quiet else (lambda s: print(s))

    if collect:
        from .auto_route import write_routes_file

        try:
            collected = design.collect_auto_routes(strict=True)
        except RouteError as exc:
            for d in exc.diagnostics:
                print(str(d), file=sys.stderr)
            print("error: could not resolve every //auto_route annotation; nothing written", file=sys.stderr)
            return 2
        for w in collected.warnings:
            print(str(w), file=sys.stderr)
        if not quiet:
            for r in collected.routes:
                print(f"  collected {r.comment.file}:{r.comment.line}: {r.comment.text}  =>  {r.spec.src} -> {r.spec.dst}")
        if args.routes_out:
            write_routes_file(args.routes_out, collected, base_dir=os.getcwd())
            if not quiet:
                print(f"wrote {len(collected.routes)} route(s) to {args.routes_out}")
        if args.collect_only:
            return 0
        specs.extend(collected.specs)
        if not specs:
            print("error: no //auto_route annotations found and no other routes given", file=sys.stderr)
            return 1
    if args.no_comment:
        from dataclasses import replace

        specs = [replace(s, comment=False) for s in specs]
    try:
        report = design.apply_routes(specs, dry_run=args.dry_run, strict=args.strict,
                                     then_expand=args.then_expand, log=log)
    except RouteError as exc:
        for d in exc.diagnostics:
            print(str(d), file=sys.stderr)
        print(f"error: routing failed with {len(exc.diagnostics)} error(s); nothing written", file=sys.stderr)
        return 2
    if args.dry_run and report.diff:
        sys.stdout.write(report.diff)
    stripped: Optional[list] = None
    if getattr(args, "strip_autos", False):
        # last step: after the route edits, --then-expand and the residual re-plan
        before = {sf.key: sf.text for sf in design.source_files()}
        stripped = design.strip_autos(dry_run=args.dry_run)
        if args.dry_run:
            for p in stripped:
                sf = design.file(str(p))
                sys.stdout.writelines(difflib.unified_diff(
                    before.get(sf.key, "").splitlines(keepends=True), sf.text.splitlines(keepends=True),
                    fromfile=f"a/{sf.path}", tofile=f"b/{sf.path}"))
    if not quiet:
        for r in report.results:
            for mod, decl in r.created_ports:
                print(f"  port      {mod}: {decl}")
            for mod, decl in r.created_nets:
                print(f"  net       {mod}: {decl}")
            for mod, decl in r.created_instances:
                print(f"  instance  {mod}: {decl}")
            for item in r.auto_native:
                print(f"  auto      {item.module}: {item.what} {item.net} via {item.via}")
        print(report.summary())
        if report.expand_report is not None:
            print(report.expand_report.summary())
        if stripped is not None:
            mode = " (dry run)" if args.dry_run else ""
            print(f"strip: {len(stripped)} file(s) changed{mode}")
    for w in report.warnings:
        print(str(w), file=sys.stderr)
    if args.json_report:
        payload = {
            "edits": [{"file": design.files[e.file].path, "start": e.start, "end": e.end, "kind": e.kind,
                       "module": e.module, "route": e.route, "text": e.text} for e in report.edits],
            "files_changed": report.files_changed,
            "warnings": [str(w) for w in report.warnings],
            "residual": len(report.residual),
        }
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    errors = report_diagnostics(design, args)
    if args.warn_fatal and report.warnings:
        return 3
    if args.strict and errors:
        return 2
    return 0


def cmd_integrate(args: argparse.Namespace) -> int:
    design = build_design(args)
    if design is None:
        return 1
    if args.strict:
        errors = sum(1 for d in design.diagnostics if d.severity == "error")
        if errors:
            report_diagnostics(design, args)
            return 2
    passes: object = "auto" if args.passes == "auto" else int(args.passes)
    quiet = args.quiet
    log = (lambda s: None) if quiet else (lambda s: print(s))
    dry_run = args.dry_run or args.no_save
    report = design.expand_all(dry_run=dry_run, diff=args.diff, only=args.only,
                               from_level=args.from_level, passes=passes, log=log,
                               strip_autos=getattr(args, "strip_autos", False))
    if args.diff:
        for r in report.results:
            if r.diff:
                sys.stdout.write(r.diff)
    if not quiet:
        print(report.summary())
        if dry_run and not args.diff:
            for p in report.changed_files():
                print(f"  would change: {rel(p)}")
    errors = report_diagnostics(design, args)
    if report.errors():
        return 1
    if args.strict and errors:
        return 2
    return 0
