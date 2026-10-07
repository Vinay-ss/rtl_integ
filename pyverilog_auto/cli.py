"""Command-line interface for pyverilog-auto.

Usage::

    pyverilog-auto expand  -y rtl/ top.v   # expand AUTOs in place
    pyverilog-auto delete  top.v           # strip AUTO sections
    pyverilog-auto inject  top.v           # add AUTO markers
    pyverilog-auto diff    top.v           # preview changes
    pyverilog-auto strip   top.v           # remove AUTO attributes, keep the code
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path


def _port_comment_arg(value: str) -> str:
    """argparse type for ``--inst-port-comment``: normalize to ``"dir width type"``."""
    from .config import port_comment_fields

    try:
        fields = port_comment_fields(value, strict=True)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    if not fields:
        raise argparse.ArgumentTypeError("give at least one of dir, width, type")
    return " ".join(fields)


def _non_negative_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def _add_inst_format_args(parser: argparse.ArgumentParser) -> None:
    """Instance formatting + strip options (expand, diff, integrate, route only)."""
    parser.add_argument(
        "--inst-lineup", action="store_true", default=None,
        help="Align instance pins: .port, (net) and comments in columns (verilog-auto-inst-lineup)",
    )
    parser.add_argument(
        "--inst-port-comment", type=_port_comment_arg, default=None, metavar="FIELDS",
        help="Trailing pin comments with any of dir,width,type (verilog-auto-inst-port-comment)",
    )
    parser.add_argument(
        "--inst-comment-column", type=_non_negative_int, default=None, metavar="N",
        help="Minimum column of the pin comments; 0 = automatic (verilog-auto-inst-comment-column)",
    )
    parser.add_argument(
        "--strip-autos", action="store_true",
        help="After expansion, remove every AUTO attribute (markers, fences, templates, "
             "headers, Local Variables) and keep the generated code",
    )


def apply_inst_format_args(cfg, args: argparse.Namespace):
    """Copy the ``--inst-*`` flags that were given onto *cfg* (returned).

    These set the base config; a file's Local Variables override them.
    """
    if getattr(args, "inst_lineup", None):
        cfg.auto_inst_lineup = True
    port_comment = getattr(args, "inst_port_comment", None)
    if port_comment:
        cfg.auto_inst_port_comment = port_comment
    column = getattr(args, "inst_comment_column", None)
    if column is not None:
        cfg.auto_inst_comment_column = column
    return cfg


def _add_library_args(parser: argparse.ArgumentParser) -> None:
    """Add shared library / file options to a sub-parser."""
    parser.add_argument(
        "-I", dest="incdirs", action="append", default=[],
        metavar="DIR", help="Add include/library directory",
    )
    parser.add_argument(
        "-y", dest="libdirs", action="append", default=[],
        metavar="DIR", help="Add library directory",
    )
    parser.add_argument(
        "-v", dest="libfiles", action="append", default=[],
        metavar="FILE", help="Add library file",
    )
    parser.add_argument(
        "-f", dest="flagfiles", action="append", default=[],
        metavar="FILE", help="Read flags from file",
    )
    parser.add_argument(
        "--libext", dest="libexts", default=None,
        help="Library extensions e.g. .v+.sv",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Print result to stdout instead of overwriting",
    )
    parser.add_argument(
        "--warn-fatal", action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument("files", nargs="+", metavar="FILE")


def _build_config(args: argparse.Namespace):
    """Build a :class:`VerilogConfig` from parsed CLI arguments."""
    from .config import VerilogConfig
    from .library.getopt import VerilogGetopt

    dirs = list(args.libdirs) + list(args.incdirs)
    if "." not in dirs:
        dirs.insert(0, ".")

    exts = None
    if args.libexts:
        exts = args.libexts.split("+")
        exts = [e for e in exts if e]

    cfg = VerilogConfig(
        library_directories=dirs,
        library_files=list(args.libfiles),
        library_extensions=exts or [".v", ".va", ".sv"],
        warn_fatal=args.warn_fatal,
    )

    # Process -f flag files (each file accumulates into the config)
    for ff in args.flagfiles:
        cfg = VerilogGetopt(cfg).parse_flag_file(ff)

    return apply_inst_format_args(cfg, args)


def _add_design_args(parser: argparse.ArgumentParser) -> None:
    """Options shared by the design-level commands (hierarchy/integrate/route)."""
    parser.add_argument(
        "-f", dest="flagfiles", action="append", default=[],
        metavar="FILELIST", help="Read a -f style filelist (may repeat)",
    )
    parser.add_argument(
        "-I", dest="incdirs", action="append", default=[],
        metavar="DIR", help="Add include directory (+incdir+)",
    )
    parser.add_argument(
        "-y", dest="libdirs", action="append", default=[],
        metavar="DIR", help="Add library directory",
    )
    parser.add_argument(
        "-v", dest="libfiles", action="append", default=[],
        metavar="FILE", help="Add library file",
    )
    parser.add_argument(
        "--libext", dest="libexts", default=None,
        help="Library extensions e.g. .v+.sv",
    )
    parser.add_argument(
        "-D", dest="defines", action="append", default=[],
        metavar="NAME[=VAL]", help="Define a macro (+define+)",
    )
    parser.add_argument(
        "--top", dest="tops", action="append", default=[],
        metavar="MODULE", help="Top module (may repeat; default: every uninstantiated module)",
    )
    parser.add_argument(
        "--no-slang", action="store_true",
        help="Force the text backend even if pyslang is installed",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Fail on filelist errors, unreadable files and parse errors",
    )
    parser.add_argument(
        "--lib", dest="lib_patterns", action="append", default=[],
        metavar="REGEX", help="Treat files matching REGEX as read-only library files",
    )
    parser.add_argument(
        "--src", dest="src_patterns", action="append", default=[],
        metavar="REGEX", help="Treat files matching REGEX as source files (expanded)",
    )
    parser.add_argument(
        "--relative-to", choices=("cwd", "filelist"), default="cwd",
        help="Resolve relative paths inside -f files against cwd (default) or the filelist",
    )
    parser.add_argument(
        "--warn-fatal", action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument("files", nargs="*", metavar="FILE")


def _cmd_expand(args: argparse.Namespace) -> int:
    """Run AUTO expansion on each file."""
    from .auto.engine import AutoEngine
    from .buffer import VerilogBuffer

    cfg = _build_config(args)
    engine = AutoEngine(cfg)
    strip = getattr(args, "strip_autos", False)
    if strip:
        from .auto.strip import strip_autos
    written: list[VerilogBuffer] = []

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        buf = VerilogBuffer.from_file(str(path))
        engine.run(buf, cfg)

        if args.no_save:
            text = buf.buffer_string()
            sys.stdout.write(strip_autos(text) if strip else text)
        else:
            buf.write_to_file()
            written.append(buf)

    # Strip once, last: later files may read earlier ones from disk while
    # they expand, so those must still carry their AUTO markers/headers.
    if strip:
        for buf in written:
            text = buf.buffer_string()
            new = strip_autos(text)
            if new != text:
                VerilogBuffer.from_string(new, buf.filepath).write_to_file()

    return 0


def _write_stdout_raw(text: str) -> None:
    """Write *text* to stdout without newline translation (keeps CRLF as is)."""
    out = getattr(sys.stdout, "buffer", None)
    if out is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    out.write(text.encode("utf-8", "surrogateescape"))
    out.flush()


def _cmd_strip(args: argparse.Namespace) -> int:
    """Remove every AUTO attribute and keep the generated code.

    Reads and writes with ``newline=""`` so CRLF files stay CRLF; does not
    use ``VerilogBuffer`` (which normalizes line endings).  Unchanged files
    are not rewritten.
    """
    from .auto.strip import strip_autos

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            text = fh.read()
        new = strip_autos(text)

        if args.no_save:
            _write_stdout_raw(new)
        elif new != text:
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                fh.write(new)

    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    """Delete AUTO-generated sections."""
    from .auto.delete import AutoDeleter
    from .buffer import VerilogBuffer
    from .local_vars import apply_local_vars, parse_local_vars

    cfg = _build_config(args)

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        buf = VerilogBuffer.from_file(str(path))
        local_vars = parse_local_vars(buf)
        if local_vars:
            cfg = apply_local_vars(cfg, local_vars)

        deleter = AutoDeleter(buf, cfg)
        deleter.delete()

        if args.no_save:
            sys.stdout.write(buf.buffer_string())
        else:
            buf.write_to_file()

    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    """Add AUTO markers (inject mode)."""
    from .auto.engine import AutoEngine
    from .buffer import VerilogBuffer

    cfg = _build_config(args)
    engine = AutoEngine(cfg)

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        buf = VerilogBuffer.from_file(str(path))
        engine.run(buf, cfg, inject=True)

        if args.no_save:
            sys.stdout.write(buf.buffer_string())
        else:
            buf.write_to_file()

    return 0


def _cmd_indent(args: argparse.Namespace) -> int:
    """Indent Verilog files."""
    from .buffer import VerilogBuffer
    from .config import VerilogConfig
    from .indent.engine import IndentEngine
    from .indent.align import AlignEngine
    from .local_vars import apply_local_vars, parse_local_vars

    cfg = _build_config(args)

    # Override indent level if specified
    if hasattr(args, 'indent_level') and args.indent_level is not None:
        cfg.indent_level = args.indent_level
        cfg.indent_level_module = args.indent_level
        cfg.indent_level_declaration = args.indent_level
        cfg.indent_level_behavioral = args.indent_level

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        buf = VerilogBuffer.from_file(str(path))
        local_vars = parse_local_vars(buf)
        if local_vars:
            cfg = apply_local_vars(cfg, local_vars)

        engine = IndentEngine(cfg)
        engine.indent_buffer(buf)

        if hasattr(args, 'align_declarations') and args.align_declarations:
            aligner = AlignEngine(cfg)
            aligner.align_declarations(buf)

        if args.no_save:
            sys.stdout.write(buf.buffer_string())
        else:
            buf.write_to_file()

    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Show what expand would change."""
    from .auto.engine import AutoEngine
    from .buffer import VerilogBuffer

    cfg = _build_config(args)
    engine = AutoEngine(cfg)

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            return 1

        original = path.read_text()
        buf = VerilogBuffer.from_file(str(path))
        engine.run(buf, cfg)
        expanded = buf.buffer_string()
        if getattr(args, "strip_autos", False):
            from .auto.strip import strip_autos

            expanded = strip_autos(expanded)

        if original != expanded:
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                expanded.splitlines(keepends=True),
                fromfile=f"a/{filepath}",
                tofile=f"b/{filepath}",
            )
            sys.stdout.writelines(diff)

    return 0


def build_parser() -> argparse.ArgumentParser:
    """The ``pyverilog-auto`` argument parser (all subcommands)."""
    parser = argparse.ArgumentParser(
        prog="pyverilog-auto",
        description="Verilog AUTO code generation (Python port of verilog-mode)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # expand
    exp = subparsers.add_parser("expand", help="Run AUTO expansion")
    _add_library_args(exp)
    _add_inst_format_args(exp)

    # delete
    dlt = subparsers.add_parser("delete", help="Remove AUTO-generated sections")
    _add_library_args(dlt)

    # inject
    inj = subparsers.add_parser("inject", help="Insert AUTO markers into instances")
    _add_library_args(inj)

    # diff
    dff = subparsers.add_parser("diff", help="Show what expand would change")
    _add_library_args(dff)
    _add_inst_format_args(dff)

    # strip
    stp = subparsers.add_parser(
        "strip", help="Remove every AUTO attribute (markers, fences, templates, headers) and keep the code",
    )
    stp.add_argument(
        "--no-save", action="store_true",
        help="Print result to stdout instead of overwriting",
    )
    stp.add_argument("files", nargs="+", metavar="FILE")

    # indent
    ind = subparsers.add_parser("indent", help="Re-indent Verilog files")
    _add_library_args(ind)
    ind.add_argument(
        "--indent-level", type=int, default=None,
        metavar="N", help="Override indent level (default: 3)",
    )
    ind.add_argument(
        "--align-declarations", action="store_true",
        help="Also align declaration columns",
    )

    # hierarchy (design-level)
    hier = subparsers.add_parser(
        "hierarchy", help="Map a design from a filelist: instance tree, levels, leaf-first order",
    )
    _add_design_args(hier)
    hier.add_argument(
        "--view", choices=("tree", "levels", "order", "all"), default="tree",
        help="What to print (default: tree)",
    )
    hier.add_argument("--json", action="store_true", help="Print the design as JSON")

    # integrate (design-level, leaf-first expansion)
    integ = subparsers.add_parser(
        "integrate", aliases=["expand-all"],
        help="Expand AUTOs in every source file of a design, leaves first",
    )
    _add_design_args(integ)
    _add_inst_format_args(integ)
    integ.add_argument("--dry-run", action="store_true", help="Compute but do not write")
    integ.add_argument("--diff", action="store_true", help="Print unified diffs, do not write")
    integ.add_argument("--no-save", action="store_true", help="Alias of --dry-run")
    integ.add_argument("--only", metavar="REGEX", default=None,
                       help="Only expand files/modules matching REGEX")
    integ.add_argument("--from", dest="from_level", type=int, default=0, metavar="LEVEL",
                       help="Skip levels below LEVEL (0 = leaves)")
    integ.add_argument("--passes", default="auto", metavar="N|auto",
                       help="Number of passes (default auto: 1 + one pass over cycles/multi-module files)")
    integ.add_argument("--quiet", action="store_true", help="No progress output")

    # route (design-level, connects ports between instances by regex path)
    rt = subparsers.add_parser(
        "route", help="Connect a signal/struct/interface port between instances addressed by regex paths",
    )
    _add_design_args(rt)
    _add_inst_format_args(rt)
    rt.add_argument("--routes", metavar="FILE", default=None, help="Routes file (.toml or .json)")
    rt.add_argument("--route", nargs=2, action="append", metavar=("SRC", "DST"), default=[],
                    help="One route: SRC='PATH_REGEX:port' DST='PATH_TEMPLATE[:port]' (may repeat)")
    rt.add_argument("--collect", action="store_true",
                    help="Collect '//auto_route [INSTPATH:]PORT :: to|from :: TARGETS' annotations from the sources and apply them")
    rt.add_argument("--collect-only", action="store_true",
                    help="Collect the annotations and write --routes-out, but do not apply anything")
    rt.add_argument("--routes-out", metavar="FILE", default=None,
                    help="Write the collected annotations as a routes.toml (implies --collect)")
    rt.add_argument("--dry-run", action="store_true", help="Plan and print the diff, write nothing")
    rt.add_argument("--then-expand", action="store_true", help="Run leaf-first AUTO expansion after routing")
    rt.add_argument("--no-comment", action="store_true", help="Do not add '// routed:' comments")
    rt.add_argument("--json-report", metavar="FILE", default=None, help="Write the edit report as JSON")
    rt.add_argument("--quiet", action="store_true", help="No progress output")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """Entry point for ``pyverilog-auto`` CLI."""
    args = build_parser().parse_args(argv)

    if args.command in ("hierarchy", "integrate", "expand-all", "route"):
        from .integ.cli_cmds import cmd_hierarchy, cmd_integrate, cmd_route

        if args.command == "hierarchy":
            return cmd_hierarchy(args)
        if args.command == "route":
            return cmd_route(args)
        return cmd_integrate(args)

    dispatch = {
        "expand": _cmd_expand,
        "delete": _cmd_delete,
        "inject": _cmd_inject,
        "diff": _cmd_diff,
        "indent": _cmd_indent,
        "strip": _cmd_strip,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
