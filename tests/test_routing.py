"""End-to-end tests of the routing API on tests/integ/routing (pyslang only)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.integ import is_slang_available  # noqa: E402
from pyverilog_auto.integ.design import Design  # noqa: E402
from pyverilog_auto.integ.route import RouteError, RouteSpec  # noqa: E402
from pyverilog_auto.integ.routes_file import load_routes  # noqa: E402

import test_golden  # noqa: E402

pytestmark = pytest.mark.skipif(not is_slang_available(), reason="pyslang backend unavailable")

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE = TESTS_DIR / "integ" / "routing"
EXPECTED = FIXTURE / "expected" / "rtl"
RTL_FILES = ["top.v", "cluster.sv", "core.sv", "cctl.v", "mem.sv", "ctrl.sv", "dma.sv", "acc.sv", "timer.sv",
             "axi_if.sv", "req_pkg.sv", "sim_top.sv"]


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "routing"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _design(root: Path) -> Design:
    return Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="slang")


def _norm(text: str) -> str:
    return test_golden._normalize_ws(test_golden._normalize(text))


def _codes(exc: RouteError) -> set[str]:
    return {d.code for d in exc.diagnostics}


# ----------------------------------------------------------------------
# Golden flow
# ----------------------------------------------------------------------

def test_route_then_expand_matches_goldens(tmp_path):
    root = _copy(tmp_path)
    d = _design(root)
    specs = load_routes(str(root / "routes.toml"))
    report = d.apply_routes(specs, then_expand=True)
    assert not report.errors
    assert report.expanded and report.residual == []
    changed = {Path(p).name for p in report.files_changed}
    assert changed == {"top.v", "cluster.sv", "core.sv", "mem.sv"}
    for name in RTL_FILES:
        actual = (root / "rtl" / name).read_text(encoding="utf-8")
        golden = (EXPECTED / name).read_text(encoding="utf-8")
        assert _norm(actual) == _norm(golden), f"golden mismatch for {name}"
    # AUTO-native propagation: no hand edits in the AUTOARG-style leaf wrapper
    assert "// routed:" not in (root / "rtl" / "cctl.v").read_text()
    top = (root / "rtl" / "top.v").read_text()
    assert ".tick            ()" in top and ".m_axi           (m_axi_1)" in top
    assert "axi_if m_axi_0 (.clk (clk), .rst_n (rst_n));" in top
    mem = (root / "rtl" / "mem.sv").read_text()
    assert "axi_if.slave m_axi_0," in mem and ".s_axi                 (m_axi_1)" in mem
    codes = {w.code for w in report.warnings}
    assert "W_UNROUTED" in codes and "W_BOUNDARY" in codes
    # per-route report content
    by_name = {r.spec.name: r for r in report.results}
    assert any(m == "top" for m, _ in by_name["axi"].created_instances)
    assert any(m == "mem" and "input logic tick" in decl for m, decl in by_name["tick"].created_ports)
    assert any(a.module == "cluster" and a.what == "port" for a in by_name["tick"].auto_native)


def test_idempotent_and_dry_run(tmp_path):
    root = _copy(tmp_path)
    d = _design(root)
    specs = load_routes(str(root / "routes.toml"))
    d.apply_routes(specs, then_expand=True)
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    d2 = _design(root)
    report = d2.apply_routes(specs)
    assert report.edits == [] and report.files_changed == []
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before
    # dry run on a fresh copy leaves files untouched but reports the edits and a diff
    root2 = _copy(tmp_path / "b")
    before2 = {n: (root2 / "rtl" / n).read_bytes() for n in RTL_FILES}
    report2 = _design(root2).apply_routes(specs, dry_run=True)
    assert report2.dry_run and len(report2.edits) > 0 and "+++ b/" in (report2.diff or "")
    assert {n: (root2 / "rtl" / n).read_bytes() for n in RTL_FILES} == before2


def test_strict_refuses_unrouted(tmp_path):
    root = _copy(tmp_path)
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    with pytest.raises(RouteError) as ei:
        _design(root).apply_routes(load_routes(str(root / "routes.toml")), strict=True)
    assert "E_UNROUTED" in _codes(ei.value)
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before


def test_single_route_api_and_plan(tmp_path):
    root = _copy(tmp_path)
    d = _design(root)
    plan = d.plan_routes([r"top\.u_mem\.u_ctrl0:err -> top\.u_mem\.u_ctrl1:err_in"])
    assert len(plan.all_edits) == 3      # net + two pins, all in mem.sv
    assert all(Path(d.files[e.file].path).name == "mem.sv" for e in plan.all_edits)
    res = d.route(RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in", comment=False))
    text = (root / "rtl" / "mem.sv").read_text()
    assert "logic err;" in text and "// routed" not in text
    assert [m for m, _ in res.created_nets] == ["mem"]


# ----------------------------------------------------------------------
# Error cases (nothing is written)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("src,dst,code", [
    (r"top\.u_cluster(\d+)\.u_core\.u_dma:irq", r"top\.u_mem\.u_ctrl0:irq_in", "E_MULTI_DRIVER"),
    (r"top\.u_cluster0\.u_core\.u_dma:irq", r"top\.u_mem\.u_ctrl0:err", "E_DIRECTION_MISMATCH"),
    (r"top\.u_cluster0\.u_core\.u_dma:irq", r"top\.u_mem\.u_ctrl0:req_in", "E_TYPE_MISMATCH"),
    (r"top\.u_cluster0\.u_core\.u_dma:irq", r"top\.u_mem\.u_ctrl0:s_axi", "E_TYPE_MISMATCH"),
    (r"top\.u_cluster0\.u_core\.u_dma:m_axi", r"top\.u_mem\.u_ctrl0:irq_in", "E_IFACE_TYPE_MISMATCH"),
    (r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem\.u_ctrl0:err_in", "E_SAME_INSTANCE"),
    (r"top\.u_mem\.u_ctrl0:err", r"sim_top\.u_mem\.u_ctrl1:err_in", "E_DIFFERENT_TOPS"),
    (r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem\.u_phy:a", "E_BLACKBOX"),
    (r"top\.nope:x", r"top\.u_mem", "E_NO_SRC_MATCH"),
    (r"top\.u_mem\.u_ctrl0:err", r"top\.nope", "E_NO_DST_MATCH"),
    (r"top\.u_mem\.u_ctrl0:nosuchport", r"top\.u_mem\.u_ctrl1:err_in", "E_SRC_PORT_MISSING"),
    (r"top\.u_cluster0\.u_core\.u_dma:req", r"top\.u_cluster0\.u_ctl\.u_timer:req_in", "E_TYPEDEF_REGEXP"),
])
def test_error_cases(tmp_path, src, dst, code):
    root = _copy(tmp_path)
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    with pytest.raises(RouteError) as ei:
        _design(root).apply_routes([RouteSpec(src=src, dst=dst)])
    assert code in _codes(ei.value), ei.value
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before


def test_name_collision_with_existing_signal(tmp_path):
    root = _copy(tmp_path)
    with pytest.raises(RouteError) as ei:
        _design(root).apply_routes([RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in", net="clk")])
    assert "E_NAME_COLLISION" in _codes(ei.value)


def test_param_dependent_width_is_explicit_with_warning(tmp_path):
    root = _copy(tmp_path)
    d = _design(root)
    report = d.apply_routes([RouteSpec(src=r"top\.u_cluster0\.u_core\.u_dma:data", dst=r"top\.u_mem\.u_ctrl0:data_in")])
    assert any(w.code == "W_PARAM_WIDTH" for w in report.warnings)
    ctrl = (root / "rtl" / "ctrl.sv").read_text()
    assert "input logic [31:0] data_in" in ctrl
    core = (root / "rtl" / "core.sv").read_text()
    assert "output logic [31:0] data" in core       # explicit at every level, numeric width
    assert ".data                     (data)" in core


def test_backend_required(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    monkeypatch.setenv("PYVERILOG_AUTO_NO_SLANG", "1")
    d = Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="text")
    with pytest.raises(RouteError) as ei:
        d.apply_routes([r"top\.u_mem\.u_ctrl0:err -> top\.u_mem\.u_ctrl1:err_in"])
    assert "E_BACKEND" in _codes(ei.value)
