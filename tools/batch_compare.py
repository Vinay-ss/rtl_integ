#!/usr/bin/env python3
"""Batch comparison tool for golden tests.

Matches the behavior of the original ``batch_test.pl``.

Usage::

    python tools/batch_compare.py [--update] [--filter PATTERN]

Without ``--update``: compare against ``tests_ok/``, report pass/fail.
With ``--update``: overwrite ``tests_ok/`` with current output.
``--filter``: run only tests matching the glob pattern (e.g. ``autoinst_*``).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.local_vars import apply_local_vars, parse_local_vars

TESTS_DIR = ROOT / "tests"
TESTS_OK_DIR = ROOT / "tests_ok"


def _untabify(text: str, tabsize: int = 8) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        result = []
        col = 0
        for ch in line:
            if ch == "\t":
                spaces = tabsize - (col % tabsize)
                result.append(" " * spaces)
                col += spaces
            else:
                result.append(ch)
                col += 1
        out.append("".join(result))
    return "\n".join(out)


def _strip_trailing_ws(text: str) -> str:
    return re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)


def _normalize(text: str) -> str:
    return _strip_trailing_ws(_untabify(text))


def expand_file(filename: str, tmp_dir: Path) -> str:
    """Expand AUTOs in *filename* and return the result text."""
    work = tmp_dir / filename
    raw = work.read_text()
    work.write_text(_strip_trailing_ws(raw))

    config = VerilogConfig(
        library_directories=[".", str(TESTS_DIR), str(tmp_dir)],
    )
    buf = VerilogBuffer.from_file(str(work))
    local_vars = parse_local_vars(buf)
    if local_vars:
        config = apply_local_vars(config, local_vars)

    engine = AutoEngine(config)
    try:
        engine.run(buf, config)
    except Exception as exc:
        print(f"  WARNING: {filename}: {type(exc).__name__}: {exc}", file=sys.stderr)

    return _normalize(buf.buffer_string())


def main():
    parser = argparse.ArgumentParser(
        description="Batch compare/update golden test files"
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Overwrite tests_ok/ with current output"
    )
    parser.add_argument(
        "--filter", default="*.v",
        help="Glob pattern for test files (default: *.v)"
    )
    args = parser.parse_args()

    all_cases = sorted([
        f.name for f in TESTS_DIR.glob(args.filter)
        if (TESTS_OK_DIR / f.name).exists() or args.update
    ])

    if not all_cases:
        print(f"No test files matching '{args.filter}' found.")
        return 1

    passed = 0
    failed = 0
    errors = 0
    updated = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Copy all .v files to temp dir
        for sibling in TESTS_DIR.glob("*.v"):
            dest = tmp_path / sibling.name
            if not dest.exists():
                shutil.copy(sibling, dest)

        for i, filename in enumerate(all_cases):
            sys.stdout.write(f"\r  [{i+1}/{len(all_cases)}] {filename:<50}")
            sys.stdout.flush()

            try:
                actual = expand_file(filename, tmp_path)
            except Exception as exc:
                print(f"\n  ERROR: {filename}: {exc}")
                errors += 1
                continue

            if args.update:
                # Write current output as golden
                (TESTS_OK_DIR / filename).write_text(actual)
                updated += 1
            else:
                golden_path = TESTS_OK_DIR / filename
                if not golden_path.exists():
                    continue

                golden = _normalize(golden_path.read_text())
                if actual == golden:
                    passed += 1
                else:
                    failed += 1
                    # Show first few differing lines
                    actual_lines = actual.splitlines()
                    golden_lines = golden.splitlines()
                    for j, (a, g) in enumerate(zip(actual_lines, golden_lines)):
                        if a != g:
                            print(f"\n  FAIL: {filename} (line {j+1})")
                            break
                    else:
                        if len(actual_lines) != len(golden_lines):
                            print(f"\n  FAIL: {filename} (line count: {len(golden_lines)} vs {len(actual_lines)})")

    print()
    if args.update:
        print(f"\nUpdated {updated} golden files in tests_ok/")
    else:
        total = passed + failed + errors
        print(f"\nResults: {passed} passed, {failed} failed, {errors} errors out of {total} total")
        if total > 0:
            print(f"Pass rate: {passed/total*100:.1f}%")

    return 0 if failed == 0 and errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
