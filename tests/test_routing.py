"""End-to-end tests of the routing API on tests/integ/routing (pyslang only)."""

from __future__ import annotations

import re
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


# ----------------------------------------------------------------------
# One net name across wrapper instances (mem is instantiated in top and sim_top)
# ----------------------------------------------------------------------

def _mem_pins(root: Path) -> tuple[str, dict[str, list[str]]]:
    text = (root / "rtl" / "mem.sv").read_text()
    pins = {p: re.findall(rf"\.{p}\s+\((\w*)\)", text) for p in ("err", "err_in", "irq_in")}
    return text, pins


def _assert_rerun_is_noop(root: Path, specs) -> None:
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    report = _design(root).apply_routes(specs)
    assert report.edits == [] and report.files_changed == []
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before


@pytest.mark.parametrize("specs,net", [
    # one explicit spec per wrapper instance (what a wrapper //auto_route resolves to)
    ([RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in", create_dst=False),
      RouteSpec(src=r"sim_top\.u_mem\.u_ctrl0:err", dst=r"sim_top\.u_mem\.u_ctrl1:err_in", create_dst=False)], "err"),
    # one regex spec covering both instances: the group only tells the wrappers apart
    ([RouteSpec(src=r"(top|sim_top)\.u_mem\.u_ctrl0:err", dst=r"\1\.u_mem\.u_ctrl1:err_in")], "err"),
    ([RouteSpec(src=r"(?P<t>top|sim_top)\.u_mem\.u_ctrl0:err", dst=r"\g<t>\.u_mem\.u_ctrl1:err_in")], "err"),
    # a fixed net name is no collision: both nets are one connection inside mem
    ([RouteSpec(src=r"(top|sim_top)\.u_mem\.u_ctrl0:err", dst=r"\1\.u_mem\.u_ctrl1:err_in", net="err_link")], "err_link"),
], ids=["explicit", "regex", "regex_named", "regex_net"])
def test_wrapper_instances_share_one_net(tmp_path, specs, net):
    root = _copy(tmp_path)
    report = _design(root).apply_routes(specs)
    assert {Path(p).name for p in report.files_changed} == {"mem.sv"}
    assert not report.warnings
    text, pins = _mem_pins(root)
    assert pins["err"] == [net] and pins["err_in"] == [net]     # connected, one pin each
    assert text.count(f"logic {net};") == 1                      # one declaration
    assert "logic err_in;" not in text and "err_top" not in text
    _assert_rerun_is_noop(root, specs)


def test_wrapper_instances_conflicting_names_merge(tmp_path):
    root = _copy(tmp_path)
    specs = [RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in"),
             RouteSpec(src=r"sim_top\.u_mem\.u_ctrl0:err", dst=r"sim_top\.u_mem\.u_ctrl1:err_in", net="err_link")]
    report = _design(root).apply_routes(specs)
    assert [w.code for w in report.warnings] == ["W_NET_NAME_MERGED"]   # the explicit net name wins
    text, pins = _mem_pins(root)
    assert pins["err"] == ["err_link"] and pins["err_in"] == ["err_link"]
    assert text.count("logic err_link;") == 1 and "logic err;" not in text
    # a net template that only differs per wrapper instance also collapses to one name
    root2 = _copy(tmp_path / "b")
    spec = RouteSpec(src=r"(top|sim_top)\.u_mem\.u_ctrl0:err", dst=r"\1\.u_mem\.u_ctrl1:err_in", net=r"err_\1")
    report2 = _design(root2).apply_routes([spec])
    assert [w.code for w in report2.warnings] == ["W_NET_NAME_MERGED"]
    text2, pins2 = _mem_pins(root2)
    assert pins2["err"] == pins2["err_in"] and pins2["err"][0] in ("err_top", "err_sim_top")
    assert text2.count(f"logic {pins2['err'][0]};") == 1


def test_wrapper_instances_through_auto_modules(tmp_path):
    # cluster is instantiated twice; the route runs through core (AUTO) and cctl (AUTOARG)
    root = _copy(tmp_path)
    specs = [RouteSpec(src=rf"top\.u_cluster{i}\.u_core\.u_dma:irq", dst=rf"top\.u_cluster{i}\.u_ctl\.u_timer:irq_in")
             for i in (0, 1)]
    report = _design(root).apply_routes(specs, then_expand=True)
    assert not report.warnings and report.residual == []
    cluster = (root / "rtl" / "cluster.sv").read_text()
    assert len(re.findall(r"\.irq\s+\(irq\)", cluster)) == 2 and "irq_in" not in cluster
    cctl = (root / "rtl" / "cctl.v").read_text()
    assert re.search(r"^\s*input irq;", cctl, re.M) and re.findall(r"\.irq_in\s+\((\w+)\)", cctl) == ["irq"]
    _assert_rerun_is_noop(root, specs)


def test_shared_host_uses_one_name_for_both_sides(tmp_path):
    # different connections in the two mem instances share ONE driver pin in
    # mem's text, so they share one net; the user-written name wins
    root = _copy(tmp_path)
    specs = [RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in"),
             RouteSpec(src=r"sim_top\.u_mem\.u_ctrl0:err", dst=r"sim_top\.u_mem\.u_ctrl1:irq_in", net="other")]
    report = _design(root).apply_routes(specs)
    assert [w.code for w in report.warnings] == ["W_NET_NAME_MERGED"]
    text, pins = _mem_pins(root)
    assert pins == {"err": ["other"], "err_in": ["other"], "irq_in": ["other"]}
    assert text.count("logic other;") == 1 and "logic err;" not in text
    _assert_rerun_is_noop(root, specs)


# ----------------------------------------------------------------------
# Different drivers never share a name inside one module
# ----------------------------------------------------------------------

def _inst_pins(text: str, inst: str) -> dict[str, str]:
    m = re.search(rf"\b{inst}\s*\((.*?)\);", text, re.S)
    assert m, inst
    return dict(re.findall(r"\.(\w+)\s*\((\w*)\)", m.group(1)))


def _cross_coupled(tops, net):
    return [RouteSpec(src=rf"{t}\.u_mem\.u_ctrl{a}:err", dst=rf"{t}\.u_mem\.u_ctrl{b}:err_in", net=net)
            for t in tops for a, b in ((0, 1), (1, 0))]


def _assert_cross_coupled_connected(root: Path) -> None:
    text = (root / "rtl" / "mem.sv").read_text()
    c0, c1 = _inst_pins(text, "u_ctrl0"), _inst_pins(text, "u_ctrl1")
    assert (c0["err"], c0["err_in"]) == ("u_ctrl0_err", "u_ctrl1_err")
    assert (c1["err"], c1["err_in"]) == ("u_ctrl1_err", "u_ctrl0_err")
    assert text.count("logic u_ctrl0_err;") == 1 and text.count("logic u_ctrl1_err;") == 1


@pytest.mark.parametrize("tops,net", [
    (["top"], None), (["top"], "err_in"),                 # 'err_in' = what a //auto_route 'to' writes
    (["top", "sim_top"], None), (["top", "sim_top"], "err_in"),
], ids=["one_wrapper", "one_wrapper_far_end_net", "two_wrappers", "two_wrappers_far_end_net"])
def test_cross_coupled_routes_get_distinct_nets(tmp_path, tops, net):
    root = _copy(tmp_path)
    specs = _cross_coupled(tops, net)
    report = _design(root).apply_routes(specs)
    assert not report.warnings and {Path(p).name for p in report.files_changed} == {"mem.sv"}
    _assert_cross_coupled_connected(root)
    _assert_rerun_is_noop(root, specs)


def test_cross_coupled_annotations_get_distinct_nets(tmp_path):
    root = _copy(tmp_path)
    p = root / "rtl" / "mem.sv"
    text = p.read_bytes().decode("utf-8")
    i = text.rindex("endmodule")
    p.write_bytes((text[:i] + "   //auto_route u_ctrl0:err :: to :: u_ctrl1:err_in\n"
                   "   //auto_route u_ctrl1:err :: to :: u_ctrl0:err_in\n" + text[i:]).encode("utf-8"))
    col = _design(root).collect_auto_routes()
    specs = [r.spec for r in col.routes]
    assert len(specs) == 4 and {s.net for s in specs} == {"err_in"}      # one per mem instance and annotation
    _design(root).apply_routes(specs)
    _assert_cross_coupled_connected(root)
    _assert_rerun_is_noop(root, [r.spec for r in _design(root).collect_auto_routes().routes])


def test_cross_coupled_explicit_net_name_is_an_error(tmp_path):
    root = _copy(tmp_path)
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    with pytest.raises(RouteError) as ei:
        _design(root).apply_routes(_cross_coupled(["top"], "link"))
    assert _codes(ei.value) == {"E_NET_NAME_COLLISION"}
    assert "top.u_mem.u_ctrl0:err" in str(ei.value) and "top.u_mem.u_ctrl1:err" in str(ei.value)
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before


def test_two_drivers_through_one_intermediate_stay_apart(tmp_path):
    # both 'err' outputs of mem feed children of u_cluster0: mem, top and cluster
    # would otherwise carry them on one 'err' port/net
    root = _copy(tmp_path)
    specs = [RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_cluster0\.u_ctl\.u_timer:in_a"),
             RouteSpec(src=r"top\.u_mem\.u_ctrl1:err", dst=r"top\.u_cluster0\.u_core\.u_dma:in_b")]
    report = _design(root).apply_routes(specs, then_expand=True)
    assert report.residual == [] and {w.code for w in report.warnings} == {"W_UNROUTED"}   # u_cluster1
    mem = (root / "rtl" / "mem.sv").read_text()
    assert _inst_pins(mem, "u_ctrl0")["err"] == "u_ctrl0_err" and _inst_pins(mem, "u_ctrl1")["err"] == "u_ctrl1_err"
    top = (root / "rtl" / "top.v").read_text()
    assert _inst_pins(top, "u_mem")["u_ctrl0_err"] == "u_mem_u_ctrl0_err"
    assert _inst_pins(top, "u_cluster0")["u_mem_u_ctrl1_err"] == "u_mem_u_ctrl1_err"
    cluster = (root / "rtl" / "cluster.sv").read_text()
    assert _inst_pins(cluster, "u_ctl")["err"] == "u_mem_u_ctrl0_err"
    assert _inst_pins(cluster, "u_core")["err"] == "u_mem_u_ctrl1_err"
    _assert_rerun_is_noop(root, specs)


# ----------------------------------------------------------------------
# create_dst = false
# ----------------------------------------------------------------------

def test_create_dst_false_missing_port_is_an_error(tmp_path):
    root = _copy(tmp_path)
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    spec = RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_inn", create_dst=False)
    with pytest.raises(RouteError) as ei:
        _design(root).apply_routes([spec])
    assert _codes(ei.value) == {"E_DST_PORT_MISSING"}
    assert "err_in" in str(ei.value)                     # closest-name hint
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before
    # the default still creates the port
    plan = _design(root).plan_routes([RouteSpec(src=spec.src, dst=spec.dst)])
    assert any(e.kind == "ansi_port" and "err_inn" in e.description for e in plan.all_edits)


def test_create_dst_false_accepts_port_in_auto_fence(tmp_path):
    root = _copy(tmp_path)
    _design(root).expand_all()                           # cluster's ports now live in AUTO fences
    d = _design(root)
    assert d.modules["cluster"].port("busy").in_auto_fence
    spec = RouteSpec(src=r"top\.u_cluster0\.u_core\.u_dma:busy", dst=r"top\.u_cluster0:busy", create_dst=False)
    plan = d.plan_routes([spec])
    assert plan.all_edits


def test_backend_required(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    monkeypatch.setenv("PYVERILOG_AUTO_NO_SLANG", "1")
    d = Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="text")
    with pytest.raises(RouteError) as ei:
        d.apply_routes([r"top\.u_mem\.u_ctrl0:err -> top\.u_mem\.u_ctrl1:err_in"])
    assert "E_BACKEND" in _codes(ei.value)
