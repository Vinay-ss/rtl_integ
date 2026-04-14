#!/usr/bin/env python3
"""Profile AUTO expansion against AUTOINST test files.

Usage::

    python tools/profile_run.py [--all] [--top N]

Without ``--all``, profiles the first 20 ``autoinst_*.v`` files.
With ``--all``, profiles all 422+ golden test files.
"""

from __future__ import annotations

import cProfile
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.local_vars import apply_local_vars, parse_local_vars

TESTS_DIR = ROOT / "tests"
TESTS_OK_DIR = ROOT / "tests_ok"

import re


def _strip_trailing_ws(text: str) -> str:
    return re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)


def expand_one(filepath: Path, tmp_dir: Path) -> None:
    """Expand AUTOs on a single file."""
    work = tmp_dir / filepath.name
    if not work.exists():
        shutil.copy(filepath, work)

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
    except Exception:
        pass  # don't abort profiling on errors


def profile_autoinst(tmp_dir: Path, limit: int = 20) -> None:
    """Profile the first *limit* autoinst test files."""
    files = sorted(TESTS_DIR.glob("autoinst_*.v"))[:limit]
    for f in files:
        expand_one(f, tmp_dir)


def profile_all(tmp_dir: Path) -> None:
    """Profile all golden test files."""
    all_cases = sorted([
        f for f in TESTS_DIR.glob("*.v")
        if (TESTS_OK_DIR / f.name).exists()
    ])
    for f in all_cases:
        expand_one(f, tmp_dir)


def main():
    use_all = "--all" in sys.argv
    top_n = 20
    for i, arg in enumerate(sys.argv):
        if arg == "--top" and i + 1 < len(sys.argv):
            top_n = int(sys.argv[i + 1])

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Copy all .v files to temp dir once
        for sibling in TESTS_DIR.glob("*.v"):
            dest = tmp_path / sibling.name
            if not dest.exists():
                shutil.copy(sibling, dest)

        if use_all:
            print(f"Profiling ALL golden test files...")
            target = lambda: profile_all(tmp_path)
        else:
            print(f"Profiling first {top_n} autoinst_*.v files...")
            target = lambda: profile_autoinst(tmp_path, top_n)

        # Time the run
        start = time.perf_counter()
        cProfile.run("target()", "profile.out")
        elapsed = time.perf_counter() - start

        print(f"\nTotal time: {elapsed:.2f}s\n")

        stats = pstats.Stats("profile.out")
        stats.sort_stats("cumulative")
        print(f"{'='*60}")
        print(f"Top {top_n} functions by cumulative time:")
        print(f"{'='*60}")
        stats.print_stats(top_n)

        print(f"\n{'='*60}")
        print(f"Top {top_n} functions by total time:")
        print(f"{'='*60}")
        stats.sort_stats("tottime")
        stats.print_stats(top_n)

    # Clean up profile output
    Path("profile.out").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
