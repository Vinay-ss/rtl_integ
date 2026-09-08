"""The sample_env/route_demo walk-through must keep working (pyslang only)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.integ import is_slang_available  # noqa: E402

pytestmark = pytest.mark.skipif(not is_slang_available(), reason="pyslang backend unavailable")

DEMO_SRC = Path(__file__).resolve().parent.parent / "sample_env" / "route_demo" / "src"


def _run(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pyverilog_auto", *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}"
    return r


def test_route_demo_flow(tmp_path):
    work = tmp_path / "work"
    shutil.copytree(DEMO_SRC, work)
    f = ["-f", str(work / "design.f"), "--relative-to", "filelist"]

    # 1. hierarchy shows the tree from the diagram
    out = _run("hierarchy", *f, "--view", "tree").stdout
    for line in ("top : top", "coreA : core_a", "instC : leaf_c", "instD : leaf_d", "coreB : core_b",
                 "instE : leaf_e", "instF : leaf_f"):
        assert line in out

    # 2. collect the annotations (nothing applied)
    routes = work / "routes.toml"
    before = {p.name: p.read_bytes() for p in work.iterdir()}
    out = _run("route", *f, "--collect-only", "--routes-out", str(routes)).stdout
    assert "wrote 4 route(s)" in out
    text = routes.read_text()
    assert text.count("[[route]]") == 4
    assert "src  = 'top\\.coreA\\.instC:data_ch'" in text and "dst  = 'top\\.coreB\\.instF:data_ch'" in text
    assert "src  = 'top\\.coreB\\.instE:irq'" in text and "dst  = 'top\\.coreA\\.instC:ctrl_ch'" in text
    assert "dst  = 'top:status_d'" in text and "net = 'status_d'" in text
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.name != "routes.toml"} == before

    # 3. dry run changes nothing
    out = _run("route", *f, "--routes", str(routes), "--dry-run").stdout
    assert "(dry run)" in out and "+   axi_if data_ch (.clk (clk), .rst_n (rst_n));" in out
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.name != "routes.toml"} == before

    # 4. apply + expand
    out = _run("route", *f, "--routes", str(routes), "--then-expand").stdout
    assert "residual edits: 0" in out and "0 error(s)" in out
    top = (work / "top.v").read_text()
    core_a = (work / "core_a.sv").read_text()
    core_b = (work / "core_b.sv").read_text()
    assert "axi_if data_ch (.clk (clk), .rst_n (rst_n));" in top          # interface instance at the LCA
    assert "output status_d;" in top and "status_d," in top.split("// Inputs")[0]   # boundary port, AUTOARG-listed
    assert ".data_ch               (data_ch.master)" in top and ".data_ch               (data_ch.slave)" in top
    assert "logic                irq;" in top                                # AUTOLOGIC net for the renamed signal
    assert "axi_if.master data_ch," in core_a and ".ctrl_ch               (irq)," in core_a
    assert "input logic irq," in core_a and ".d_status              (status_d)," in core_a
    assert "axi_if.slave data_ch," in core_b
    assert core_b.count("(data_ch.slave)") == 2                              # instE and instF, both by AUTOINST
    assert "output logic         irq" in core_b                              # exported by AUTOOUTPUT
    assert "// routed:" not in core_b.split("leaf_e instE")[1]              # no hand-written pins on that side

    # 5. idempotent
    after = {p.name: p.read_bytes() for p in work.iterdir()}
    out = _run("route", *f, "--routes", str(routes), "--then-expand").stdout
    assert "0 edit(s)" in out
    assert {p.name: p.read_bytes() for p in work.iterdir()} == after
