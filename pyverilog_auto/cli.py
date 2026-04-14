"""Command-line interface for pyverilog-auto.

Usage::

    pyverilog-auto expand  -y rtl/ top.v   # expand AUTOs in place
    pyverilog-auto delete  top.v           # strip AUTO sections
    pyverilog-auto inject  top.v           # add AUTO markers
    pyverilog-auto diff    top.v           # preview changes
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path


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

    # Process -f flag files
    if args.flagfiles:
        getopt = VerilogGetopt(cfg)
        for ff in args.flagfiles:
            cfg = getopt.parse_flag_file(ff)

    return cfg


def _cmd_expand(args: argparse.Namespace) -> int:
    """Run AUTO expansion on each file."""
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
        engine.run(buf, cfg)

        if args.no_save:
            sys.stdout.write(buf.buffer_string())
        else:
            buf.write_to_file()

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

        if original != expanded:
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                expanded.splitlines(keepends=True),
                fromfile=f"a/{filepath}",
                tofile=f"b/{filepath}",
            )
            sys.stdout.writelines(diff)

    return 0


def main() -> int:
    """Entry point for ``pyverilog-auto`` CLI."""
    parser = argparse.ArgumentParser(
        prog="pyverilog-auto",
        description="Verilog AUTO code generation (Python port of verilog-mode)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # expand
    exp = subparsers.add_parser("expand", help="Run AUTO expansion")
    _add_library_args(exp)

    # delete
    dlt = subparsers.add_parser("delete", help="Remove AUTO-generated sections")
    _add_library_args(dlt)

    # inject
    inj = subparsers.add_parser("inject", help="Insert AUTO markers into instances")
    _add_library_args(inj)

    # diff
    dff = subparsers.add_parser("diff", help="Show what expand would change")
    _add_library_args(dff)

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

    args = parser.parse_args()

    dispatch = {
        "expand": _cmd_expand,
        "delete": _cmd_delete,
        "inject": _cmd_inject,
        "diff": _cmd_diff,
        "indent": _cmd_indent,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
