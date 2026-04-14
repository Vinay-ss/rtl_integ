"""Golden-file test harness for AUTOINST expansion.

Compares the output of ``AutoEngine.run()`` against the golden files
in ``tests_ok/`` for all ``autoinst_*.v`` test inputs.

The golden files in ``tests_ok/`` were produced by Emacs verilog-mode's
test harness (``0test.el``), which does:

1. Strip trailing whitespace from input
2. Delete old AUTOs
3. Re-expand AUTOs
4. Re-indent entire buffer (``verilog-test-indent-buffer``)
5. Untabify (tabs → spaces with 8-col tab stops)

Steps 1, 5 are replicated here.  Step 4 (indentation) is not yet
implemented, so indentation-only differences are expected.
"""

import re
import shutil
from pathlib import Path

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig

TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"
TESTS_OK_DIR = Path(__file__).resolve().parent.parent / "tests_ok"


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


def _strip_leading_ws_per_line(text: str) -> str:
    """Strip leading whitespace from each line for content comparison."""
    return "\n".join(line.lstrip() for line in text.splitlines())


def _normalize_ws(text: str) -> str:
    """Normalize whitespace within lines for content comparison.

    The golden files go through Emacs re-indentation (Phase 5) which
    changes internal alignment of declarations (``input  a;`` →
    ``input a;``), parameter spacing (``P=1`` → ``P = 1``), etc.
    This normalization factors out all whitespace and ``=`` spacing
    differences so we test only AUTOINST expansion correctness, not
    formatting.
    """
    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"\s+", " ", line)
        # Normalize standalone = spacing (not <=, >=, ==, !=, ===, !==)
        line = re.sub(r"(?<![<>!=])=(?!=)", " = ", line)
        # Normalize spacing before [ (e.g. "input[" vs "input [")
        line = re.sub(r"(\w)\[", r"\1 [", line)
        # Normalize spacing after ] before word (e.g. "]a_o1" vs "] a_o1")
        line = re.sub(r"\](\w)", r"] \1", line)
        # Normalize space before // comments
        line = re.sub(r"(\S)//", r"\1 //", line)
        # Normalize space between { and /* (Emacs indent adds space)
        line = re.sub(r"\{\s*/\*", "{ /*", line)
        # Normalize template numbering comments (line numbers vary)
        line = re.sub(r"// Templated\s+(?:T\d+\s+L\d+|\d+)", "// Templated", line)
        # Strip ", Couldn't Merge" annotations (merge conflict warning)
        line = re.sub(r",\s*Couldn't Merge", "", line)
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

    return "\n".join(result)


def autoinst_cases():
    """Collect all autoinst_*.v test cases."""
    return sorted(f.name for f in TESTS_DIR.glob("autoinst_*.v"))


@pytest.mark.parametrize("filename", autoinst_cases())
def test_autoinst_golden(filename, tmp_path):
    """Expand AUTOs in *filename* and compare with golden output."""
    src = TESTS_DIR / filename
    expected = TESTS_OK_DIR / filename
    if not expected.exists():
        pytest.skip(f"No golden file for {filename}")

    # Copy all sibling .v files first (library files may be needed)
    for sibling in TESTS_DIR.glob("*.v"):
        dest = tmp_path / sibling.name
        if not dest.exists():
            shutil.copy(sibling, dest)

    # Strip trailing whitespace from input (like 0test.el does)
    work = tmp_path / filename
    raw = work.read_text()
    work.write_text(_strip_trailing_ws(raw))

    from pyverilog_auto.local_vars import apply_local_vars, parse_local_vars

    config = VerilogConfig(
        library_directories=[".", str(TESTS_DIR), str(tmp_path)],
    )
    buf = VerilogBuffer.from_file(str(work))
    local_vars = parse_local_vars(buf)
    if local_vars:
        config = apply_local_vars(config, local_vars)
    engine = AutoEngine(config)

    try:
        engine.run(buf, config)
    except Exception as exc:
        pytest.fail(f"Engine raised {type(exc).__name__}: {exc}")

    actual = _normalize(buf.buffer_string())
    golden = _normalize(expected.read_text())

    # Compare with normalized whitespace — the golden files include
    # Emacs re-indentation (Phase 5) which we don't have yet.
    # This tests AUTOINST expansion content independently of formatting.
    actual_content = _normalize_ws(actual)
    golden_content = _normalize_ws(golden)
    assert actual_content == golden_content, f"Content mismatch in {filename}"
