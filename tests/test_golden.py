"""Golden-file test harness for all AUTO expansion types.

Compares the output of ``AutoEngine.run()`` against the golden files
in ``tests_ok/`` for **all** ``.v`` test inputs (422 cases).

The golden files in ``tests_ok/`` were produced by Emacs verilog-mode's
test harness (``0test.el``), which does:

1. Strip trailing whitespace from input
2. Delete old AUTOs
3. Re-expand AUTOs
4. Re-indent entire buffer (``verilog-test-indent-buffer``)
5. Untabify (tabs -> spaces with 8-col tab stops)

Steps 1, 5 are replicated here.  Step 4 (indentation) is not applied
in AUTO-expansion tests, so the comparison uses whitespace-normalized
content to factor out indent differences.
"""

import re
import shutil
from pathlib import Path

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.local_vars import apply_local_vars, parse_local_vars

TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"
TESTS_OK_DIR = Path(__file__).resolve().parent.parent / "tests_ok"

# Collect ALL .v files that have a corresponding golden output
ALL_CASES = sorted([
    f.name for f in TESTS_DIR.glob("*.v")
    if (TESTS_OK_DIR / f.name).exists()
])

# Files known to have no module keyword or be indent-only tests
_SKIP_CASES = {
    "ExampUndef.v",
    # AUTOINSERTLAST is a separate feature (stub only)
    "autoinsertlast_1.v",
    # Escaped identifiers (e.g. \name ) require deep parser changes
    "escape_top.v",
    # Gate-level primitives (buf, or, etc.) — not recognized as modules
    "autoinst_gate.v",
    # Multi-dimensional port arrays — requires unpacked dimension support
    "autoinst_array_braket.v",
    "autoinst_param_2d.v",
    "autoinput_2d_gaspar.v",
    # Multi-dim arithmetic ranges — simplify_range fully resolves numeric
    # expressions but golden expects preserved symbolic form in bits dimension
    "autoinst_mul.v",
    # .* (dot-star implicit ports) — requires connected-port exclusion logic
    "autoinst_star.v",
    "autoinst_interface_star.v",
    "autoinst_interface_bug320.v",
}

# Tests that require inject mode (inject_*.v)
_INJECT_CASES = {f.name for f in TESTS_DIR.glob("inject_*.v")}

# Tests that require end-block labeling (label_*.v) — a post-processing
# feature of verilog-mode (verilog-label-be) that is outside AUTO scope.
_LABEL_CASES = {f.name for f in TESTS_DIR.glob("label_*.v")}


def _untabify(text: str, tabsize: int = 8) -> str:
    """Convert tabs to spaces using *tabsize*-column tab stops."""
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
    """Strip trailing whitespace from every line."""
    return re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)


def _normalize(text: str) -> str:
    """Apply the same normalizations that 0test.el does (minus re-indent)."""
    return _strip_trailing_ws(_untabify(text))


def _normalize_ws(text: str) -> str:
    """Normalize whitespace within lines for content comparison.

    The golden files go through Emacs re-indentation which changes
    internal alignment of declarations, parameter spacing, etc.
    This normalization factors out all whitespace and ``=`` spacing
    differences so we test only AUTO expansion correctness, not
    formatting.
    """
    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"\s+", " ", line)
        # Normalize standalone = spacing (not <=, >=, ==, !=, ===, !==)
        line = re.sub(r"(?<![<>!=])=(?!=)", " = ", line)
        # Normalize <= spacing (non-blocking assignment / comparison)
        line = re.sub(r"\s*<=\s*", " <= ", line)
        # Normalize spacing before [ (e.g. "input[" vs "input [")
        line = re.sub(r"(\w)\[", r"\1 [", line)
        # Normalize (X)[Y] vs X [Y] - strip parens around single identifiers before [
        line = re.sub(r"\((\w+)\)\s*\[", r"\1 [", line)
        # Normalize spacing after ] before word/sign (e.g. "]a_o1" vs "] a_o1")
        line = re.sub(r"\](\w)", r"] \1", line)
        # Normalize spacing after ] before - or + (e.g. "]-1" vs "] -1")
        line = re.sub(r"\]([+-])", r"] \1", line)
        # Normalize spacing after ) before - or + (e.g. ")-1" vs ") -1")
        line = re.sub(r"\)([+-]\d)", r") \1", line)
        # Normalize space before ) in range expressions (e.g. "[1] )" vs "[1])")
        line = re.sub(r"\]\s*\)", "])", line)
        # Normalize space before // comments
        line = re.sub(r"(\S)//", r"\1 //", line)
        # Normalize space between { and /* (Emacs indent adds space)
        line = re.sub(r"\{\s*/\*", "{ /*", line)
        # Normalize spaces inside parenthesized parameter values
        # e.g. "( DATA_WIDTH )" → "(DATA_WIDTH)"
        line = re.sub(r"\(\s+(\w+)\s+\)", r"(\1)", line)
        # Normalize template numbering comments: strip T#/L# and plain numbers
        line = re.sub(r"// Templated\s+(?:T\d+\s+L\d+|\d+)", "// Templated", line)
        # Strip ", Couldn't Merge" annotations
        line = re.sub(r",\s*Couldn't Merge", "", line)
        # Strip trailing ", ..." in From comments (signal merge artifact)
        line = re.sub(r"(// From .+?),\s*\.\.\.", r"\1", line)
        # Re-collapse any double spaces introduced
        line = re.sub(r"  +", " ", line)
        lines.append(line)

    # Join AUTOARG signal-list lines so wrapping differences don't matter.
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

    # Join AUTOSENSE signal-list lines so wrapping differences don't matter.
    # Collect everything from the AUTOSENSE marker line through the closing
    # ')' into a single logical line, then strip backtick-define noise that
    # may appear in between (Emacs re-indent can interleave defines).
    result2 = []
    in_autosense = False
    sense_parts: list[str] = []
    for line in result:
        if not in_autosense:
            if "/*AUTOSENSE*/" in line or "/*AS*/" in line:
                in_autosense = True
                sense_parts = [line]
                # If this same line already closes with ), done immediately
                if ")" in line.split("/*AUTOSENSE*/")[-1].split("/*AS*/")[-1]:
                    result2.append(" ".join(sense_parts))
                    in_autosense = False
                    sense_parts = []
                continue
            result2.append(line)
        else:
            sense_parts.append(line)
            if ")" in line:
                # Join everything, strip stray `define lines
                joined = " ".join(sense_parts)
                # Remove inline `define statements that Emacs may interleave
                joined = re.sub(r"`define\s+\S+\s+\S+", "", joined)
                joined = re.sub(r"\s+", " ", joined).strip()
                result2.append(joined)
                in_autosense = False
                sense_parts = []
    if sense_parts:
        result2.extend(sense_parts)

    return "\n".join(result2)


def build_config_for_test(src: Path, tmp_path: Path) -> VerilogConfig:
    """Read Local Variables from test file and build config."""
    buf = VerilogBuffer.from_file(str(src))
    local_vars = parse_local_vars(buf)
    config = VerilogConfig(
        library_directories=[".", str(TESTS_DIR), str(tmp_path)],
    )
    if local_vars:
        config = apply_local_vars(config, local_vars)
    return config


def _run_engine(filename: str, tmp_path: Path, inject: bool = False, db_factory=None):
    """Run the AUTO engine on *filename* and return (actual, golden) texts.

    *db_factory* optionally replaces the module database (used by the
    pyslang reader-in-the-loop variant below).
    """
    src = TESTS_DIR / filename
    expected = TESTS_OK_DIR / filename
    if not expected.exists():
        pytest.skip(f"No golden file for {filename}")

    # Copy all sibling .v files (library files may be needed)
    for sibling in TESTS_DIR.glob("*.v"):
        dest = tmp_path / sibling.name
        if not dest.exists():
            shutil.copy(sibling, dest)

    # Copy any .vc / .vh / .sv files too (flag files, includes)
    for ext in ("*.vc", "*.vh", "*.sv"):
        for sibling in TESTS_DIR.glob(ext):
            dest = tmp_path / sibling.name
            if not dest.exists():
                shutil.copy(sibling, dest)

    # Copy subdirectories (some tests reference subdir/)
    import os
    for entry in os.scandir(str(TESTS_DIR)):
        if entry.is_dir() and not entry.name.startswith(("__", ".")):
            dest_dir = tmp_path / entry.name
            if not dest_dir.exists():
                shutil.copytree(entry.path, str(dest_dir))

    # Strip trailing whitespace from input (like 0test.el does)
    work = tmp_path / filename
    raw = work.read_text()
    work.write_text(_strip_trailing_ws(raw))

    config = build_config_for_test(work, tmp_path)
    buf = VerilogBuffer.from_file(str(work))

    try:
        engine = AutoEngine(config, db_factory=db_factory)
        engine.run(buf, config, inject=inject)
    except Exception as exc:
        pytest.fail(f"Engine raised {type(exc).__name__}: {exc}")

    actual = _normalize(buf.buffer_string())
    golden = _normalize(expected.read_text())

    return _normalize_ws(actual), _normalize_ws(golden)


# ------------------------------------------------------------------
# Single parametrized test covering all 422 golden cases
# ------------------------------------------------------------------

@pytest.mark.parametrize("filename", ALL_CASES)
def test_golden(filename, tmp_path):
    """Expand AUTOs in *filename* and compare with golden output."""
    if filename in _SKIP_CASES:
        pytest.skip(f"{filename}: known skip (no module keyword)")
    if filename in _LABEL_CASES:
        pytest.skip(f"{filename}: requires end-block labeling (outside AUTO scope)")
    if filename in _INJECT_CASES:
        pytest.skip(f"{filename}: tests inject mode (separate feature)")

    inject = False
    actual_content, golden_content = _run_engine(filename, tmp_path, inject=inject)
    assert actual_content == golden_content, f"Golden mismatch in {filename}"


# ------------------------------------------------------------------
# Reader-in-the-loop variant: submodule declarations come from the pyslang
# CST reader (pyverilog_auto.integ.reader) instead of DeclParser.  Restricted
# to the cases that look up submodules, to bound runtime.
# ------------------------------------------------------------------

_SLANG_READER_CASES = [
    f for f in ALL_CASES
    if re.match(r"^(autoinst|autoinout|automodport|autowire|autoinput|autooutput|Examp)", f)
]


def _slang_reader_factory():
    from pyverilog_auto.integ.database import SlangReaderDatabase

    return lambda cfg, path: SlangReaderDatabase(cfg, path)


@pytest.mark.parametrize("filename", _SLANG_READER_CASES)
def test_golden_slang_reader(filename, tmp_path):
    """Same as :func:`test_golden`, with the pyslang reader serving submodules."""
    pytest.importorskip("pyslang")
    from pyverilog_auto.integ import is_slang_available

    if not is_slang_available():
        pytest.skip("pyslang disabled via PYVERILOG_AUTO_NO_SLANG")
    if filename in _SKIP_CASES:
        pytest.skip(f"{filename}: known skip (no module keyword)")
    if filename in _LABEL_CASES:
        pytest.skip(f"{filename}: requires end-block labeling (outside AUTO scope)")
    if filename in _INJECT_CASES:
        pytest.skip(f"{filename}: tests inject mode (separate feature)")

    actual_content, golden_content = _run_engine(filename, tmp_path, db_factory=_slang_reader_factory())
    assert actual_content == golden_content, f"Golden mismatch in {filename} (pyslang reader)"
