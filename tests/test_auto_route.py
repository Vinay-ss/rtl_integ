"""Tests for in-source //auto_route annotations (pyslang only)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.integ import is_slang_available  # noqa: E402
from pyverilog_auto.integ.auto_route import (  # noqa: E402
    collect_auto_routes, resolve_auto_routes, routes_to_toml, scan_auto_routes, write_routes_file,
)
from pyverilog_auto.integ.design import Design  # noqa: E402
from pyverilog_auto.integ.route import RouteError, RouteSpec  # noqa: E402
from pyverilog_auto.integ.routes_file import load_routes  # noqa: E402

pytestmark = pytest.mark.skipif(not is_slang_available(), reason="pyslang backend unavailable")

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE = TESTS_DIR / "integ" / "routing"
RTL_FILES = ["top.v", "cluster.sv", "core.sv", "cctl.v", "mem.sv", "ctrl.sv", "dma.sv", "acc.sv", "timer.sv",
             "axi_if.sv", "req_pkg.sv", "sim_top.sv"]


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "routing"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("expected", "__pycache__"))
    return dst


def _annotate(root: Path, name: str, lines: list[str]) -> None:
    """Insert annotation lines before the module's endmodule."""
    p = root / "rtl" / name
    text = p.read_bytes().decode("utf-8")
    idx = text.rindex("endmodule")
    new = text[:idx] + "".join(f"   {ln}\n" for ln in lines) + text[idx:]
    p.write_bytes(new.encode("utf-8"))      # keep LF (write_text would produce CRLF on Windows)


def _design(root: Path) -> Design:
    return Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="slang")


def test_scan_and_resolve_to_and_from(tmp_path):
    root = _copy(tmp_path)
    # timer (instantiated twice: under each cluster's u_ctl) sends tick to the nearest u_core's dma port
    _annotate(root, "timer.sv", ["//auto_route tick :: to :: u_dma:tick_in"])
    # ctrl receives err from the nearest u_ctrl0 (ctrl1 only), written as a block comment with spaces
    _annotate(root, "ctrl.sv", ["/* auto_route err_in :: from :: u_ctrl0:err */"])
    d = _design(root)
    comments = scan_auto_routes(d)
    assert [(c.module, c.port, c.direction, c.targets) for c in comments] == [
        ("ctrl", "err_in", "from", ["u_ctrl0:err"]),
        ("timer", "tick", "to", ["u_dma:tick_in"]),
    ]
    res = resolve_auto_routes(d, comments)
    assert not res.errors
    pairs = sorted((r.spec.src, r.spec.dst) for r in res.routes)
    # nearest u_dma for each timer instance is the one in the same cluster
    assert (r"top\.u_cluster0\.u_ctl\.u_timer:tick", r"top\.u_cluster0\.u_core\.u_dma:tick_in") in pairs
    assert (r"top\.u_cluster1\.u_ctl\.u_timer:tick", r"top\.u_cluster1\.u_core\.u_dma:tick_in") in pairs
    # 'from' on ctrl: u_ctrl1 receives from u_ctrl0 in both mem instances (top and sim_top);
    # u_ctrl0 itself is excluded (it would be the same instance)
    assert (r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem\.u_ctrl1:err_in") in pairs
    assert (r"sim_top\.u_mem\.u_ctrl0:err", r"sim_top\.u_mem\.u_ctrl1:err_in") in pairs
    assert not any(src.startswith(r"top\.u_mem\.u_ctrl0") and dst.endswith("u_ctrl0:err_in") for src, dst in pairs)
    # routes.toml round trip
    out = tmp_path / "routes.toml"
    text = write_routes_file(str(out), res, base_dir=str(root))
    assert "auto_route" in text and "[[route]]" in text
    loaded = load_routes(str(out))
    assert sorted((s.src, s.dst) for s in loaded) == pairs
    assert all(s.name for s in loaded)


def test_regex_and_path_targets_and_errors(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "dma.sv", [
        r"//auto_route irq :: to :: re:top\.u_mem\.u_ctrl[01]:irq_in",   # regex target with port override
        "//auto_route busy :: to :: u_mem.u_ctrl1:err_in",               # dotted suffix
        "//auto_route cfg :: from :: nowhere",                            # unresolved -> error
    ])
    d = _design(root)
    res = resolve_auto_routes(d, scan_auto_routes(d))
    assert [e.code for e in res.errors] == ["E_AUTOROUTE_TARGET"]
    dsts = sorted(r.spec.dst for r in res.routes if r.comment.port == "irq")
    assert dsts == [r"top\.u_mem\.u_ctrl0:irq_in", r"top\.u_mem\.u_ctrl1:irq_in"] * 2 or set(dsts) == {
        r"top\.u_mem\.u_ctrl0:irq_in", r"top\.u_mem\.u_ctrl1:irq_in"}
    assert {r.spec.dst for r in res.routes if r.comment.port == "busy"} == {r"top\.u_mem\.u_ctrl1:err_in"}
    with pytest.raises(RouteError):
        collect_auto_routes(d, strict=True)
    assert collect_auto_routes(d, strict=False).errors


def test_collected_routes_equal_explicit_specs(tmp_path):
    """Applying the annotations gives byte-identical files to the explicit specs."""
    explicit_root = _copy(tmp_path / "a")
    annotated_root = _copy(tmp_path / "b")
    spec = RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in", name="u_ctrl0.err->ctrl.err_in")
    _design(explicit_root).apply_routes([spec], then_expand=True)
    _annotate(annotated_root, "ctrl.sv", ["//auto_route err_in :: from :: u_ctrl0:err"])
    d = _design(annotated_root)
    collected = d.collect_auto_routes()
    # sim_top's mem instance is annotated too; keep only the routes under 'top' for the comparison
    specs = [s for s in collected.specs if s.src.startswith("top")]
    assert len(specs) == 1 and specs[0].name == spec.name
    d.apply_routes(specs, then_expand=True)
    for name in RTL_FILES:
        a = (explicit_root / "rtl" / name).read_bytes()
        b = (annotated_root / "rtl" / name).read_bytes()
        if name == "ctrl.sv":
            assert b.replace(b"   //auto_route err_in :: from :: u_ctrl0:err\n", b"") == a
        else:
            assert a == b, name


def test_cli_collect_only_and_apply(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "ctrl.sv", ["//auto_route err_in :: from :: u_ctrl0:err"])
    out = tmp_path / "collected.toml"
    cmd = [sys.executable, "-m", "pyverilog_auto", "route", "-f", str(root / "design.f"), "--relative-to", "filelist",
           "--collect-only", "--routes-out", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists() and "collected" in r.stdout
    assert "logic err;" not in (root / "rtl" / "mem.sv").read_text()
    cmd = [sys.executable, "-m", "pyverilog_auto", "route", "-f", str(root / "design.f"), "--relative-to", "filelist",
           "--routes", str(out), "--quiet"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "logic err;" in (root / "rtl" / "mem.sv").read_text()


def test_routes_to_toml_options():
    s = RouteSpec(src="a:x", dst="b", name="n", net="net_\\1", dst_modport="slave", iface_conn={"clk": "clk"},
                  modport_policy="plain", check_types=False, comment=False)
    text = routes_to_toml([s])
    for needle in ("name = 'n'", "src  = 'a:x'", "net = 'net_\\1'", "dst_modport = 'slave'",
                   "iface_conn = { clk = 'clk' }", "modport_policy = 'plain'", "check_types = false", "comment = false"):
        assert needle in text


def _strip_annotations(data: bytes) -> bytes:
    return b"\n".join(ln for ln in data.split(b"\n") if b"//auto_route" not in ln)


def test_keywords_and_placeholders(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "ctrl.sv", ["//auto_route err :: to :: $top:err_{n}",
                                "//auto_route irq_in :: from :: $parent:irq_{inst}",
                                "//auto_route tick_in :: from :: $top:t_{path}"])
    d = _design(root)
    assert d.modules["top"].ansi is False          # (/*AUTOARG*/) is non-ANSI
    assert d.modules["cctl"].ansi is False
    assert d.modules["core"].ansi is True          # (/*AUTOINPUT*/ /*AUTOOUTPUT*/) is ANSI
    res = resolve_auto_routes(d, scan_auto_routes(d))
    assert not res.errors and not res.warnings
    got = {(r.spec.src, r.spec.dst, r.spec.net) for r in res.routes}
    # $top resolves per tree; {n} counts instances within that tree; net follows the far-end name
    assert (r"top\.u_mem\.u_ctrl0:err", "top:err_0", "err_0") in got
    assert (r"top\.u_mem\.u_ctrl1:err", "top:err_1", "err_1") in got
    assert (r"sim_top\.u_mem\.u_ctrl0:err", "sim_top:err_0", "err_0") in got
    # $parent and {inst}: 'from' keeps net=None (the far end is the source)
    assert (r"top\.u_mem:irq_u_ctrl0", r"top\.u_mem\.u_ctrl0:irq_in", None) in got
    assert (r"top\.u_mem:irq_u_ctrl1", r"top\.u_mem\.u_ctrl1:irq_in", None) in got
    # {path}: path below the root with dots replaced
    assert (r"top:t_u_mem_u_ctrl1", r"top\.u_mem\.u_ctrl1:tick_in", None) in got


def test_route_to_autoarg_top_end_to_end(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "ctrl.sv", ["//auto_route err :: to :: top:err_{n}"])     # through hand-written mem to AUTOARG top
    _annotate(root, "dma.sv", ["//auto_route m_axi :: to :: $parent"])         # interface to the ANSI parent boundary
    d = _design(root)
    res = d.collect_auto_routes()
    assert [w.code for w in res.warnings] == ["W_AUTOROUTE_UNREACHABLE"] * 2   # sim_top's ctrl instances cannot reach 'top'
    rep = d.apply_routes(res.specs, then_expand=True)
    assert not rep.residual
    top = (root / "rtl" / "top.v").read_text()
    mem = (root / "rtl" / "mem.sv").read_text()
    core = (root / "rtl" / "core.sv").read_text()
    assert "output err_0;" in top and "output err_1;" in top            # body declarations (non-ANSI)
    assert "err_0, err_1" in top.split("// Inputs")[0]                    # listed by AUTOARG
    assert ".err_0                    (err_0)" in top                     # connected by AUTOINST at top
    assert "output logic err_0" in mem and ".err                   (err_0)" in mem
    assert "output logic err_1" in mem and ".err                   (err_1)" in mem
    assert "axi_if m_axi," in core                                        # boundary interface port on core
    assert {w.code for w in rep.warnings} == {"W_UNROUTED", "W_BOUNDARY"}
    rep2 = d.apply_routes(d.collect_auto_routes().specs, then_expand=True)
    assert rep2.edits == []


def test_interface_to_nonansi_top_is_rejected_before_writing(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "dma.sv", ["//auto_route m_axi :: to :: top:m_axi_{n}"])
    d = _design(root)
    before = {p.name: p.read_bytes() for p in (root / "rtl").iterdir()}
    with pytest.raises(RouteError) as ei:
        d.apply_routes(d.collect_auto_routes().specs, then_expand=True)
    assert {x.code for x in ei.value.diagnostics} == {"E_NONANSI_IFACE"}
    assert "ANSI" in str(ei.value)
    assert {p.name: p.read_bytes() for p in (root / "rtl").iterdir()} == before


def test_bad_placeholder_port_name_is_an_error(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "ctrl.sv", ["//auto_route err :: to :: top:err-{n}"])
    d = _design(root)
    res = resolve_auto_routes(d, scan_auto_routes(d))
    assert [e.code for e in res.errors] == ["E_AUTOROUTE_PORT"] * 2


def test_wildcard_and_regex_targets(tmp_path):
    """Targets with regex metacharacters are regexes full-matched from the top (no implicit anchoring)."""
    root = _copy(tmp_path)
    _annotate(root, "dma.sv", [
        "//auto_route irq :: to :: top.*u_ctrl1:irq_in",           # '.*' wildcard, written from the top
        r"//auto_route busy :: to :: top\.u_mem\.u_ctrl[01]:err_in",  # character class -> both controllers (fan-out)
        "//auto_route cfg :: to :: .*ctrl0:cfg_o",                  # '.*' may also cover the top itself
        "//auto_route req :: to :: u_ctrl[:req_in",                 # invalid pattern -> error
        "//auto_route data :: to :: u_mem.*u_ctrl1:tick_in",        # no 'top' prefix: no match (no tail anchoring)
        "//auto_route irq :: to :: mem.u_ctrl0:err_in",             # plain dotted suffix: 'mem' is not a component -> no match
    ])
    d = _design(root)
    res = resolve_auto_routes(d, scan_auto_routes(d))
    assert sorted(e.code for e in res.errors) == ["E_AUTOROUTE_TARGET"] * 3
    assert any("invalid pattern" in e.message for e in res.errors)
    assert any("no instance matches target 'u_mem.*u_ctrl1'" in e.message for e in res.errors)
    assert any("no instance matches target 'mem.u_ctrl0'" in e.message for e in res.errors)
    by_port = {}
    for r in res.routes:
        by_port.setdefault(r.comment.port, set()).add(r.spec.dst)
    assert by_port["irq"] == {r"top\.u_mem\.u_ctrl1:irq_in"}
    assert by_port["busy"] == {r"top\.u_mem\.u_ctrl0:err_in", r"top\.u_mem\.u_ctrl1:err_in"}
    assert by_port["cfg"] == {r"top\.u_mem\.u_ctrl0:cfg_o"}
    assert "data" not in by_port
