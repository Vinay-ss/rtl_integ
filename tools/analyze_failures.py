#!/usr/bin/env python3
"""Analyze golden-test failures, grouped by AUTO type.

Usage::

    python tools/analyze_failures.py [--verbose]

Runs all golden tests, groups failures by the dominant AUTO type in
each test file, and prints a ranked summary table.
"""

from __future__ import annotations

import difflib
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path
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


def _normalize_ws(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"\s+", " ", line)
        line = re.sub(r"(?<![<>!=])=(?!=)", " = ", line)
        line = re.sub(r"\s*<=\s*", " <= ", line)
        line = re.sub(r"(\w)\[", r"\1 [", line)
        line = re.sub(r"\](\w)", r"] \1", line)
        line = re.sub(r"(\S)//", r"\1 //", line)
        line = re.sub(r"\{\s*/\*", "{ /*", line)
        line = re.sub(r"// Templated\s+(?:T\d+\s+L\d+|\d+)", "// Templated", line)
        line = re.sub(r",\s*Couldn't Merge", "", line)
        line = re.sub(r"  +", " ", line)
        lines.append(line)

    result = []
    in_autoarg = False
    autoarg_buf: list[str] = []
    for line in lines:
        if "/*AUTOARG*/" in line:
            in_autoarg = True
            result.append(line)
            continue
        if in_autoarg:
            if re.match(r"^// (?:Outputs|Inputs|Inouts)$", line):
                if autoarg_buf:
                    result.append(" ".join(autoarg_buf))
                    autoarg_buf = []
                result.append(line)
                continue
            if line in (")", ") ;", ");"):
                if autoarg_buf:
                    result.append(" ".join(autoarg_buf))
                    autoarg_buf = []
                result.append(line)
                in_autoarg = False
                continue
            autoarg_buf.append(line)
        else:
            result.append(line)
    if autoarg_buf:
        result.extend(autoarg_buf)
    return "\n".join(result)


def classify_auto_type(filename: str) -> str:
    """Determine the dominant AUTO type from the filename prefix."""
    name = filename.lower()
    if name.startswith("autoinst"):
        return "autoinst"
    if name.startswith("autoarg"):
        return "autoarg"
    if name.startswith("autoinoutmod"):
        return "autoinoutmodule"
    if name.startswith("autoinoutcomp"):
        return "autoinoutcomp"
    if name.startswith("autoinoutin"):
        return "autoinoutin"
    if name.startswith("autoinoutparam"):
        return "autoinoutparam"
    if name.startswith("autoinout"):
        return "autoinout"
    if name.startswith("autoinput"):
        return "autoinput"
    if name.startswith("autooutput"):
        return "autooutput"
    if name.startswith("autoreg"):
        return "autoreg"
    if name.startswith("autosense"):
        return "autosense"
    if name.startswith("autoreset"):
        return "autoreset"
    if name.startswith("autotieoff"):
        return "autotieoff"
    if name.startswith("autounused"):
        return "autounused"
    if name.startswith("autoascii"):
        return "autoasciienum"
    if name.startswith("autoundef"):
        return "autoundef"
    if name.startswith("automodport"):
        return "automodport"
    if name.startswith("autowire"):
        return "autowire"
    if name.startswith("autologic"):
        return "autologic"
    if name.startswith("indent"):
        return "indent"
    if name.startswith("inject"):
        return "inject"
    if name.startswith("examp"):
        return "example"
    if name.startswith("label"):
        return "label"
    return "other"


def run_one_test(filename: str, tmp_dir: Path) -> tuple[bool, str, str]:
    """Run a single golden test. Returns (passed, first_diff_line, error)."""
    src = TESTS_DIR / filename
    expected_path = TESTS_OK_DIR / filename
    if not expected_path.exists():
        return True, "", "no golden"  # skip

    # Copy all sibling .v files
    for sibling in TESTS_DIR.glob("*.v"):
        dest = tmp_dir / sibling.name
        if not dest.exists():
            shutil.copy(sibling, dest)

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

    try:
        engine = AutoEngine(config)
        engine.run(buf, config)
    except Exception as exc:
        return False, "", f"Exception: {type(exc).__name__}: {exc}"

    actual = _normalize_ws(_normalize(buf.buffer_string()))
    golden = _normalize_ws(_normalize(expected_path.read_text()))

    if actual == golden:
        return True, "", ""

    # Find first differing line
    actual_lines = actual.splitlines()
    golden_lines = golden.splitlines()
    first_diff = ""
    for i, (a, g) in enumerate(zip(actual_lines, golden_lines)):
        if a != g:
            first_diff = f"L{i+1}: expected={g!r}  actual={a!r}"
            break
    else:
        if len(actual_lines) != len(golden_lines):
            first_diff = f"Line count: expected={len(golden_lines)} actual={len(actual_lines)}"

    return False, first_diff, ""


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    all_cases = sorted([
        f.name for f in TESTS_DIR.glob("*.v")
        if (TESTS_OK_DIR / f.name).exists()
    ])

    # Group by AUTO type
    groups: dict[str, list[str]] = {}
    for f in all_cases:
        atype = classify_auto_type(f)
        groups.setdefault(atype, []).append(f)

    results: dict[str, dict] = {}
    failures_detail: list[tuple[str, str, str]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        for i, filename in enumerate(all_cases):
            if filename == "ExampUndef.v":
                continue  # known skip

            # Each file gets its own subdir to avoid cross-contamination
            test_tmp = tmp_path / f"test_{i}"
            test_tmp.mkdir(exist_ok=True)

            passed, first_diff, error = run_one_test(filename, test_tmp)
            atype = classify_auto_type(filename)

            if atype not in results:
                results[atype] = {"total": 0, "pass": 0, "fail": 0}
            results[atype]["total"] += 1
            if passed:
                results[atype]["pass"] += 1
            else:
                results[atype]["fail"] += 1
                failures_detail.append((filename, first_diff, error))

            # Progress indicator
            sys.stdout.write(f"\r  [{i+1}/{len(all_cases)}] {filename:<50}")
            sys.stdout.flush()

    print("\n")

    # Print summary table
    print(f"{'AUTO type':<22} {'Total':>5} {'Pass':>6} {'Fail':>6} {'Pass%':>7}")
    print("-" * 50)
    total_all = 0
    pass_all = 0
    fail_all = 0
    for atype in sorted(results, key=lambda k: -results[k]["fail"]):
        r = results[atype]
        pct = r["pass"] / r["total"] * 100 if r["total"] > 0 else 0
        print(f"{atype:<22} {r['total']:>5} {r['pass']:>6} {r['fail']:>6} {pct:>6.1f}%")
        total_all += r["total"]
        pass_all += r["pass"]
        fail_all += r["fail"]

    print("-" * 50)
    pct_all = pass_all / total_all * 100 if total_all > 0 else 0
    print(f"{'TOTAL':<22} {total_all:>5} {pass_all:>6} {fail_all:>6} {pct_all:>6.1f}%")

    if failures_detail:
        print(f"\n--- Failure Details ({len(failures_detail)} failures) ---\n")
        for filename, first_diff, error in sorted(failures_detail):
            if error:
                print(f"  {filename}: {error}")
            elif verbose:
                print(f"  {filename}: {first_diff}")
            else:
                # Truncate long diff lines
                diff_short = first_diff[:120] + "..." if len(first_diff) > 120 else first_diff
                print(f"  {filename}: {diff_short}")

    return 0 if fail_all == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
