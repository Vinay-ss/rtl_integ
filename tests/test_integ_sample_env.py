"""Design-flow test over sample_env (committed files are already expanded)."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

from pyverilog_auto.auto.delete import AutoDeleter
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.integ import is_slang_available
from pyverilog_auto.integ.design import Design

import test_golden  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_env"
TOPS = ["uart_wrap.v", "soc_top.v"]


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


def _norm(text: str) -> str:
    return test_golden._normalize_ws(test_golden._normalize(text))


@pytest.mark.parametrize("backend", BACKENDS)
def test_sample_env_roundtrip(tmp_path, backend):
    work = tmp_path / "sample_env"
    shutil.copytree(SAMPLE, work, ignore=shutil.ignore_patterns("__pycache__"))
    # Strip the committed expansions first
    for name in TOPS:
        buf = VerilogBuffer.from_file(str(work / name))
        AutoDeleter(buf, VerilogConfig()).delete()
        buf.write_to_file()
        assert "// Beginning of automatic" not in (work / name).read_text()

    d = Design.from_filelist(str(work / "design.f"), relative_to="filelist", backend=backend)
    assert d.backend == backend
    order = d.order()
    level0 = {Path(d.files[k].path).name: d.files[k].role for k in order.levels[0]}
    assert {n for n, r in level0.items() if r == "library"} == {"gpio_port.v", "spi_master.v", "uart_rx.v", "uart_tx.v"}
    assert level0["auto_reg_demo.v"] == "source"
    assert {Path(d.files[k].path).name for k in order.levels[1]} >= set(TOPS)
    roots = {r.name: r for r in d.hierarchy()}
    assert {c.module_name for c in roots["soc_top"].children} == {"uart_tx", "uart_rx", "spi_master", "gpio_port"}
    assert {c.module_name for c in roots["uart_wrap"].children} == {"uart_tx", "uart_rx"}

    report = d.expand_all()
    assert not report.errors(), [r.error for r in report.errors()]
    assert set(TOPS) <= {Path(p).name for p in report.changed_files()}
    for name in TOPS:
        assert _norm((work / name).read_text()) == _norm((SAMPLE / name).read_text()), name
