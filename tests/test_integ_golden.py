"""End-to-end test of leaf-first expansion on the hierarchical fixture.

Fixture: ``tests/integ`` (3 levels, generate loop, interface, -y/-v
libraries, black box).  Goldens: ``tests/integ/expected`` (same layout
as ``tests/integ/src``), generated with the text backend and reviewed.
"""

from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.integ import is_slang_available
from pyverilog_auto.integ.design import Design

import test_golden  # noqa: E402  (tests/ is on sys.path under pytest)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE = TESTS_DIR / "integ"
EXPECTED = FIXTURE / "expected"

SOURCE_FILES = [
    "src/top.v",
    "src/core_wrap.sv",
    "src/mem_wrap.v",
    "src/if/bus_if.sv",
    "src/leaf/alu_leaf.v",
    "src/leaf/regfile_leaf.v",
    "src/leaf/sram_leaf.v",
]
CHANGED_FILES = {"top.v", "core_wrap.sv", "mem_wrap.v", "regfile_leaf.v"}


def _slang_ok() -> bool:
    if not is_slang_available():
        return False
    try:
        importlib.import_module("pyverilog_auto.integ.frontend_slang")
    except Exception:
        return False
    return True


BACKENDS = [
    "text",
    pytest.param("slang", marks=pytest.mark.skipif(not _slang_ok(), reason="pyslang backend unavailable")),
]


def _copy_fixture(tmp_path: Path) -> Path:
    dst = tmp_path / "integ"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _norm(text: str) -> str:
    return test_golden._normalize_ws(test_golden._normalize(text))


def _design(root: Path, backend: str) -> Design:
    return Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend=backend)


def _names(design: Design, keys) -> list[str]:
    return [Path(design.files[k].path).name for k in keys]


@pytest.mark.parametrize("backend", BACKENDS)
def test_order_is_leaf_first(tmp_path, backend):
    root = _copy_fixture(tmp_path)
    d = _design(root, backend)
    assert d.backend == backend
    order = d.order()
    levels = [set(_names(d, lvl)) for lvl in order.levels]
    assert levels[0] == {"bus_if.sv", "alu_leaf.v", "regfile_leaf.v", "sram_leaf.v", "misc_lib.v", "clk_gate.v"}
    assert levels[1] == {"core_wrap.sv", "mem_wrap.v"}
    assert levels[2] == {"top.v"}
    assert order.cycles == []
    assert "pad_cell" in d.unresolved
    assert d.instance("top.u_mem.u_pad").is_blackbox
    assert d.files[d.modules["clk_gate"].file].role == "library"
    assert d.files[d.modules["sync_ff"].file].role == "library"


@pytest.mark.parametrize("backend", BACKENDS)
def test_expand_all_matches_goldens(tmp_path, backend):
    root = _copy_fixture(tmp_path)
    d = _design(root, backend)
    report = d.expand_all()
    assert not report.errors(), [r.error for r in report.errors()]
    assert {Path(p).name for p in report.changed_files()} == CHANGED_FILES
    for relpath in SOURCE_FILES:
        actual = (root / relpath).read_text(encoding="utf-8")
        golden = (EXPECTED / relpath).read_text(encoding="utf-8")
        assert _norm(actual) == _norm(golden), f"golden mismatch for {relpath}"
    # library files are never touched
    assert (root / "ylib" / "clk_gate.v").read_text() == (FIXTURE / "ylib" / "clk_gate.v").read_text()
    assert (root / "lib" / "misc_lib.v").read_text() == (FIXTURE / "lib" / "misc_lib.v").read_text()


@pytest.mark.parametrize("backend", BACKENDS)
def test_second_run_is_a_noop(tmp_path, backend):
    root = _copy_fixture(tmp_path)
    d = _design(root, backend)
    d.expand_all()
    before = {p: (root / p).read_bytes() for p in SOURCE_FILES}
    report = Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend=backend).expand_all()
    assert report.changed_files() == []
    assert {p: (root / p).read_bytes() for p in SOURCE_FILES} == before


@pytest.mark.parametrize("backend", BACKENDS)
def test_dry_run_and_diff_do_not_write(tmp_path, backend):
    root = _copy_fixture(tmp_path)
    before = {p: (root / p).read_bytes() for p in SOURCE_FILES}
    d = _design(root, backend)
    report = d.expand_all(dry_run=True)
    assert {Path(p).name for p in report.changed_files()} == CHANGED_FILES
    assert not any(r.written for r in report.results)
    assert {p: (root / p).read_bytes() for p in SOURCE_FILES} == before
    # overlay carries the expansion even though nothing was written
    assert "// Beginning of automatic" in d.text_of(str(root / "src" / "top.v"))
    d2 = _design(root, backend)
    report2 = d2.expand_all(diff=True)
    assert all(r.diff for r in report2.results if r.changed)
    assert {p: (root / p).read_bytes() for p in SOURCE_FILES} == before


@pytest.mark.parametrize("backend", BACKENDS)
def test_from_level_and_only(tmp_path, backend):
    root = _copy_fixture(tmp_path)
    d = _design(root, backend)
    report = d.expand_all(from_level=1)
    names = {Path(r.path).name for r in report.results if not r.skipped}
    assert "regfile_leaf.v" not in names and "top.v" in names
    root2 = _copy_fixture(tmp_path / "b")
    d2 = _design(root2, backend)
    report2 = d2.expand_all(only="mem_wrap")
    assert {Path(p).name for p in report2.changed_files()} == {"mem_wrap.v"}


def test_reverse_order_gives_a_different_top():
    """Expanding parents before children (the classic per-file flow) leaves
    ``top.v`` incomplete: it never sees the AUTOOUTPUT-derived ports of the
    wrappers.  Running the design flow afterwards converges to the golden."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = _copy_fixture(Path(td))
        d = _design(root, "text")
        order = d.order()
        for key in reversed(order.files):
            sf = d.files[key]
            if sf.role != "source":
                continue
            cfg = d.make_config(sf.path)
            buf = VerilogBuffer.from_file(sf.path)
            AutoEngine(cfg).run(buf, cfg)
            buf.write_to_file()
        top = (root / "src" / "top.v").read_text(encoding="utf-8")
        golden = (EXPECTED / "src" / "top.v").read_text(encoding="utf-8")
        assert _norm(top) != _norm(golden)
        # the ordered flow repairs it
        report = _design(root, "text").expand_all()
        assert "top.v" in {Path(p).name for p in report.changed_files()}
        top = (root / "src" / "top.v").read_text(encoding="utf-8")
        assert _norm(top) == _norm(golden)


@pytest.mark.skipif(not _slang_ok(), reason="pyslang backend unavailable")
def test_backends_produce_identical_output(tmp_path):
    a = _copy_fixture(tmp_path / "a")
    b = _copy_fixture(tmp_path / "b")
    _design(a, "text").expand_all()
    _design(b, "slang").expand_all()
    for relpath in SOURCE_FILES:
        assert (a / relpath).read_bytes() == (b / relpath).read_bytes(), relpath
