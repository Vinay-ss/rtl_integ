#!/usr/bin/env python
"""Run the test suite and report only the changes against the known failures.

Usage::

    python tools/check_baseline.py                    # full suite (~2.5 min)
    python tools/check_baseline.py tests/test_cli.py  # subset (pytest args pass through)

* Runs ``<this interpreter> -m pytest tests/ -q -p no:cacheprovider -rfE``
  from the repository that contains this script (works from a git
  worktree: the worktree is put first on ``PYTHONPATH`` so subprocess-based
  tests import its ``pyverilog_auto`` instead of the editable install).
* Compares the ``FAILED`` / ``ERROR`` node ids with
  ``tests/baseline_failures.txt`` and prints only:
    - NEW failures (not in the baseline);
    - NEWLY PASSING baseline tests (only when the full suite ran);
    - a one-line summary (plus the path of the full pytest log).
* Exit status: 0 = no new failures, 1 = new failures, 2 = pytest itself
  failed to run (usage/internal error, no tests collected, interrupted).

``--update-baseline`` rewrites the baseline file from the current run
(full suite only).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "baseline_failures.txt"
_RESULT_RE = re.compile(r"^(FAILED|ERROR) (.+?)(?: - .*)?$")
_SUMMARY_RE = re.compile(r"^=*\s*(\d+ (?:failed|passed|error|errors|skipped|deselected|xfailed|xpassed|warnings?)\b.*?)\s*=*$")


def read_baseline(path: Path = BASELINE) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def parse_failures(output: str) -> set[str]:
    """Node ids of the ``FAILED`` / ``ERROR`` lines of pytest's short summary."""
    ids: set[str] = set()
    for line in output.splitlines():
        m = _RESULT_RE.match(line.rstrip())
        if m:
            ids.add(m.group(2).strip())
    return ids


def summary_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        m = _SUMMARY_RE.match(line.strip())
        if m:
            return m.group(1)
    return "(no pytest summary line)"


def write_baseline(ids: set[str], path: Path = BASELINE) -> None:
    header = ("# Known failing tests on the untouched tree (pytest node ids, one per line).\n"
              "# Compared by tools/check_baseline.py; regenerate with --update-baseline.\n")
    path.write_text(header + "".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true",
                    help="Rewrite tests/baseline_failures.txt from this run (full suite only)")
    ap.add_argument("--log", metavar="FILE", default=None,
                    help="Where to save the full pytest output (default: a temp file)")
    ns, pytest_args = ap.parse_known_args(argv)

    full = not pytest_args
    cmd = [sys.executable, "-m", "pytest"]
    cmd += pytest_args if pytest_args else ["tests/"]
    cmd += ["-q", "-p", "no:cacheprovider", "-rfE"]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")

    if ns.log:
        log_path = Path(ns.log)
    else:
        fd, name = tempfile.mkstemp(prefix="check_baseline_", suffix=".log")
        os.close(fd)
        log_path = Path(name)
    log_path.write_text(output, encoding="utf-8")

    # 0 = all passed, 1 = some failed; anything else is a pytest-level problem
    if proc.returncode not in (0, 1):
        tail = "\n".join(output.splitlines()[-15:])
        print(tail)
        print(f"pytest exited with status {proc.returncode}; full log: {log_path}")
        return 2

    failed = parse_failures(output)
    baseline = read_baseline()
    new = sorted(failed - baseline)
    fixed = sorted(baseline - failed) if full else []

    if ns.update_baseline:
        if not full:
            print("--update-baseline needs the full suite (no pytest args)")
            return 2
        write_baseline(failed)
        print(f"wrote {len(failed)} id(s) to {BASELINE.relative_to(ROOT)}")

    if new:
        print(f"NEW FAILURES ({len(new)}):")
        for i in new:
            print(f"  {i}")
    if fixed:
        print(f"NEWLY PASSING ({len(fixed)}):")
        for i in fixed:
            print(f"  {i}")
    scope = "full suite" if full else "subset"
    known = len(failed & baseline)
    print(f"{scope}: {summary_line(output)} | known failures {known}/{len(baseline)} | "
          f"new {len(new)} | newly passing {len(fixed) if full else 'n/a'} | log: {log_path}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
