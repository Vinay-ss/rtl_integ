"""Tests for in-source //auto_route annotations (pyslang only)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from pyverilog_auto.integ import is_slang_available  # noqa: E402
from pyverilog_auto.integ.auto_route import (  # noqa: E402
    collect_auto_routes, parse_auto_route, resolve_auto_routes, routes_to_toml, scan_auto_routes, write_routes_file,
)
from pyverilog_auto.integ.design import Design, DesignError  # noqa: E402
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


def _inst_pins(text: str, inst: str) -> dict[str, str]:
    m = re.search(rf"\b{inst}\s*\((.*?)\);", text, re.S)
    assert m, inst
    return dict(re.findall(r"\.(\w+)\s*\((\w*)\)", m.group(1)))


def _assert_mem_err_connected(root: Path) -> None:
    """u_ctrl0.err -> u_ctrl1.err_in inside mem.sv, i.e. in both mem instances (top and sim_top)."""
    mem = (root / "rtl" / "mem.sv").read_text()
    assert _inst_pins(mem, "u_ctrl0")["err"] == "err" and _inst_pins(mem, "u_ctrl1")["err_in"] == "err"
    assert "err" not in _inst_pins(mem, "u_ctrl1") and "err_in" not in _inst_pins(mem, "u_ctrl0")
    assert mem.count("logic err;") == 1 and "err_in;" not in mem


def test_collected_routes_equal_explicit_specs(tmp_path):
    """Applying the annotations gives byte-identical files to the explicit specs."""
    explicit_root = _copy(tmp_path / "a")
    annotated_root = _copy(tmp_path / "b")
    # mem is instantiated in top and sim_top: the leaf annotation gives one spec per instance
    explicit = [RouteSpec(src=rf"{t}\.u_mem\.u_ctrl0:err", dst=rf"{t}\.u_mem\.u_ctrl1:err_in",
                          name="u_ctrl0.err->ctrl.err_in") for t in ("top", "sim_top")]
    _design(explicit_root).apply_routes(explicit, then_expand=True)
    _annotate(annotated_root, "ctrl.sv", ["//auto_route err_in :: from :: u_ctrl0:err"])
    d = _design(annotated_root)
    specs = d.collect_auto_routes().specs
    assert sorted(specs, key=lambda s: s.src) == sorted(explicit, key=lambda s: s.src)
    d.apply_routes(specs, then_expand=True)
    _assert_mem_err_connected(annotated_root)
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
    text = out.read_text()
    assert text.count("[[route]]") == 2                  # one per mem instance
    for t in ("top", "sim_top"):
        assert f"src  = '{t}\\.u_mem\\.u_ctrl0:err'" in text and f"dst  = '{t}\\.u_mem\\.u_ctrl1:err_in'" in text
    assert "logic err;" not in (root / "rtl" / "mem.sv").read_text()
    cmd = [sys.executable, "-m", "pyverilog_auto", "route", "-f", str(root / "design.f"), "--relative-to", "filelist",
           "--routes", str(out), "--quiet"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    _assert_mem_err_connected(root)
    # a second apply changes nothing
    before = {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES}
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert {n: (root / "rtl" / n).read_bytes() for n in RTL_FILES} == before


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


# ----------------------------------------------------------------------
# Wrapper-level annotations: LEFT = INSTPATH:PORT | re:REGEX:PORT
# ----------------------------------------------------------------------

def _left(c):
    return (c.src_inst, c.port, c.direction, c.targets, c.error)


def test_parse_left_forms():
    kw = dict(file="w.sv", line=1)
    assert _left(parse_auto_route("w", "err", "to", "a, b:x", **kw)) == (None, "err", "to", ["a", "b:x"], None)
    assert _left(parse_auto_route("w", "instC:c_busy", "TO", ["instD:d_hold"], **kw)) == (
        "instC", "c_busy", "to", ["instD:d_hold"], None)
    assert _left(parse_auto_route("w", "u_sub.u_leaf:x", "from", "instF", **kw))[:2] == ("u_sub.u_leaf", "x")
    # re: keeps its prefix; the port is after the LAST colon, so (?:...) groups survive
    assert _left(parse_auto_route("w", r"re:top\.(?:a|b)\.u_x:p", "to", "y", **kw))[:2] == (r"re:top\.(?:a|b)\.u_x", "p")
    assert _left(parse_auto_route("w", "top.*instE:p", "to", "y", **kw))[:2] == ("top.*instE", "p")
    for bad_left, bad_dir, bad_targets in [("a.b", "to", "x"),             # no ':PORT'
                                           ("re:u_x", "to", "x"),          # re: without ':PORT'
                                           ("u-a:p", "to", "x"),           # not a dotted path
                                           ("instC:p-q", "to", "x"),       # port is not an identifier
                                           (":p", "to", "x"),              # empty INSTPATH
                                           ("instC:p", "sideways", "x"),   # direction
                                           ("instC:p", "to", " , ")]:      # no targets
        assert parse_auto_route("w", bad_left, bad_dir, bad_targets, **kw).error, (bad_left, bad_dir, bad_targets)


def test_scan_wrapper_forms_and_syntax_errors(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", [
        "//auto_route u_ctrl0:err :: to :: u_ctrl1:err_in",
        "/* auto_route u_ctrl1:tick_in :: from :: u_ctrl0:err */",
        r"//auto_route re:top\.u_mem\.u_ctrl[01]:err :: to :: $parent:err_{inst}",
        "//auto_route a.b :: to :: u_ctrl1",                 # malformed LEFT
        "//auto_route u_ctrl0:err :: tox :: u_ctrl1",        # malformed direction
        "// auto_route annotations below are wrapper-level",  # prose: no '::'
        "// auto_routes: none",                               # prose: other word
        "/* see auto_route in the docs */",                   # prose: does not start with it
    ])
    _annotate(root, "cluster.sv", ["//auto_route u_ctl.u_timer:tick :: to :: u_dma:tick_in"])  # deeper path
    d = _design(root)
    comments = scan_auto_routes(d)
    got = [(c.module, c.src_inst, c.port, c.direction, c.targets) for c in comments if not c.error]
    assert got == [
        ("cluster", "u_ctl.u_timer", "tick", "to", ["u_dma:tick_in"]),
        ("mem", "u_ctrl0", "err", "to", ["u_ctrl1:err_in"]),
        ("mem", "u_ctrl1", "tick_in", "from", ["u_ctrl0:err"]),
        ("mem", r"re:top\.u_mem\.u_ctrl[01]", "err", "to", ["$parent:err_{inst}"]),
    ]
    res = resolve_auto_routes(d, comments)
    assert [e.code for e in res.errors] == ["E_AUTOROUTE_SYNTAX"] * 2
    assert {e.line for e in res.errors} == {c.line for c in comments if c.error}
    assert all("a.b" in e.message or "tox" in e.message for e in res.errors)
    with pytest.raises(RouteError):
        collect_auto_routes(d, strict=True)


def test_wrapper_instantiated_twice_gives_one_spec_per_instance(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", ["//auto_route u_ctrl0:err :: to :: u_ctrl1:err_in"])   # mem: in top and sim_top
    d = _design(root)
    res = d.collect_auto_routes()
    assert not res.errors and not res.warnings
    got = sorted((r.spec.src, r.spec.dst, r.spec.net, r.spec.name, r.spec.create_dst, r.wrapper.path)
                 for r in res.routes)
    assert got == [
        (r"sim_top\.u_mem\.u_ctrl0:err", r"sim_top\.u_mem\.u_ctrl1:err_in", "err_in", "u_ctrl0.err->u_ctrl1.err_in",
         False, "sim_top.u_mem"),
        (r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem\.u_ctrl1:err_in", "err_in", "u_ctrl0.err->u_ctrl1.err_in",
         False, "top.u_mem"),
    ]


def test_wrapper_route_is_the_proxy_of_a_leaf_annotation(tmp_path):
    """cluster (twice under top): 'u_ctl.u_timer:tick' resolves like '//auto_route tick ...' in timer."""
    leaf_root = _copy(tmp_path / "a")
    wrap_root = _copy(tmp_path / "b")
    _annotate(leaf_root, "timer.sv", ["//auto_route tick :: to :: u_dma:tick_in",
                                      "//auto_route tick :: to :: $top:tick_{n}"])
    _annotate(wrap_root, "cluster.sv", ["//auto_route u_ctl.u_timer:tick :: to :: u_dma:tick_in",
                                        "//auto_route u_ctl.u_timer:tick :: to :: $top:tick_{n}",
                                        "//auto_route u_core.u_dma:irq :: to :: $parent:irq_{parent}_{path}"])
    leaf = _design(leaf_root).collect_auto_routes()
    wrap = _design(wrap_root).collect_auto_routes()
    assert not wrap.errors and not wrap.warnings
    leaf_set = {(r.spec.src, r.spec.dst, r.spec.net) for r in leaf.routes}
    assert {(r.spec.src, r.spec.dst, r.spec.net) for r in wrap.routes if r.comment.port == "tick"} == leaf_set
    assert (r"top\.u_cluster1\.u_ctl\.u_timer:tick", r"top\.u_cluster1\.u_core\.u_dma:tick_in", "tick_in") in leaf_set
    assert (r"top\.u_cluster1\.u_ctl\.u_timer:tick", "top:tick_1", "tick_1") in leaf_set
    # leaf-style specs keep create_dst=True; wrapper-level ones are False except towards an ancestor boundary
    assert all(r.spec.create_dst and r.wrapper is None and r.comment.src_inst is None for r in leaf.routes)
    cd = {r.spec.dst: r.spec.create_dst for r in wrap.routes}
    assert cd[r"top\.u_cluster0\.u_core\.u_dma:tick_in"] is False
    assert cd["top:tick_0"] is True and cd[r"top\.u_cluster0\.u_core:irq_u_core_u_cluster0_u_core_u_dma"] is True


def test_wrapper_from_direction(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", ["//auto_route u_ctrl1:err_in :: from :: u_ctrl0:err"])
    d = _design(root)
    res = d.collect_auto_routes()
    assert not res.warnings     # the leaf-style form warns for u_ctrl0 (it cannot reach itself)
    got = sorted((r.spec.src, r.spec.dst, r.spec.net, r.spec.name, r.spec.create_dst) for r in res.routes)
    assert got == [
        (r"sim_top\.u_mem\.u_ctrl0:err", r"sim_top\.u_mem\.u_ctrl1:err_in", None, "u_ctrl0.err->u_ctrl1.err_in", False),
        (r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem\.u_ctrl1:err_in", None, "u_ctrl0.err->u_ctrl1.err_in", False),
    ]


def test_wrapper_regex_left(tmp_path):
    """re:/metacharacter LEFTs are full-matched from the top, intersected with each wrapper instance."""
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", [
        r"//auto_route re:top\.u_mem\.u_ctrl[01]:err :: to :: $parent:err_{inst}",   # only top's mem, both ctrls
        "//auto_route .*u_ctrl1:cfg_o :: to :: $parent:cfg_{n}",                      # metachars: both trees
        "//auto_route u_mem.*u_ctrl1:rd :: to :: $parent",                            # no 'top' prefix: no match
        r"//auto_route re:top\.u_cluster0\.u_core:irq :: to :: u_ctrl0",              # outside mem's subtree
        "//auto_route re:u_ctrl[:err :: to :: u_ctrl1",                               # invalid pattern
    ])
    d = _design(root)
    res = d.collect_auto_routes(strict=False)
    assert [e.code for e in res.errors] == ["E_AUTOROUTE_SRC"] * 3
    assert any("invalid pattern" in e.message for e in res.errors)
    assert any("'u_mem.*u_ctrl1'" in e.message and "from the top" in e.message for e in res.errors)
    by_port: dict[str, set] = {}
    for r in res.routes:
        by_port.setdefault(r.comment.port, set()).add((r.spec.src, r.spec.dst))
    assert by_port["err"] == {(r"top\.u_mem\.u_ctrl0:err", r"top\.u_mem:err_u_ctrl0"),
                              (r"top\.u_mem\.u_ctrl1:err", r"top\.u_mem:err_u_ctrl1")}
    # {n} counts the annotation's origins per tree
    assert by_port["cfg_o"] == {(r"top\.u_mem\.u_ctrl1:cfg_o", r"top\.u_mem:cfg_0"),
                                (r"sim_top\.u_mem\.u_ctrl1:cfg_o", r"sim_top\.u_mem:cfg_0")}


def test_wrapper_codes_unused_and_no_match(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "sim_top.sv", ["//auto_route u_mem.u_ctrl0:err :: to :: u_ctrl1:err_in"])
    _annotate(root, "mem.sv", ["//auto_route u_nope:err :: to :: u_ctrl1:err_in",      # no such child
                               "//auto_route u_mem.u_ctrl0:err :: to :: u_ctrl1"])      # path is relative to mem
    d = Design.from_filelist(str(root / "design.f"), relative_to="filelist", backend="slang", top="top")
    res = resolve_auto_routes(d, scan_auto_routes(d))
    assert [w.code for w in res.warnings] == ["W_AUTOROUTE_UNUSED"]           # sim_top is not elaborated
    assert [e.code for e in res.errors] == ["E_AUTOROUTE_SRC"] * 2
    assert not res.routes


def test_create_dst_false_round_trips_through_toml(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", ["//auto_route u_ctrl0:err :: to :: u_ctrl1:err_in",
                               "//auto_route u_ctrl1:tick_in :: from :: u_ctrl0:err"])
    _annotate(root, "ctrl.sv", ["//auto_route irq_in :: from :: $parent:irq_{inst}"])   # leaf-style: no create_dst
    out = tmp_path / "collected.toml"
    cmd = [sys.executable, "-m", "pyverilog_auto", "route", "-f", str(root / "design.f"), "--relative-to", "filelist",
           "--collect-only", "--routes-out", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert text.count("create_dst = false") == 4
    assert "[top.u_mem: u_ctrl0:err -> top.u_mem.u_ctrl1]" in text
    assert "[sim_top.u_mem: u_ctrl1:tick_in <- sim_top.u_mem.u_ctrl0]" in text
    loaded = load_routes(str(out))
    specs = _design(root).collect_auto_routes().specs
    assert loaded == specs
    assert sorted(s.create_dst for s in loaded) == [False] * 4 + [True] * 4


def test_api_matches_comment(tmp_path):
    root = _copy(tmp_path)
    _annotate(root, "mem.sv", ["//auto_route u_ctrl0:err :: to :: u_ctrl1:tick_in, $parent:e_{n}"])
    _annotate(root, "ctrl.sv", ["//auto_route err_in :: from :: u_ctrl0:err"])
    d = _design(root)
    res = d.collect_auto_routes(strict=False)
    by_mod = {m: [r.spec for r in res.routes if r.comment.module == m] for m in ("mem", "ctrl")}
    assert len(by_mod["mem"]) == 4 and len(by_mod["ctrl"]) == 2
    assert d.auto_route("mem", "u_ctrl0:err", "to", "u_ctrl1:tick_in, $parent:e_{n}").specs == by_mod["mem"]
    assert d.auto_route("mem", "u_ctrl0:err", "To", ["u_ctrl1:tick_in", "$parent:e_{n}"]).specs == by_mod["mem"]
    assert d.auto_route("ctrl", "err_in", "from", "u_ctrl0:err").specs == by_mod["ctrl"]
    # create_dst override and errors (returned, not raised)
    assert all(s.create_dst for s in d.auto_route("mem", "u_ctrl0:err", "to", "u_ctrl1:err_in", create_dst=True).specs)
    assert [e.code for e in d.auto_route("mem", "a.b", "to", "u_ctrl1").errors] == ["E_AUTOROUTE_SYNTAX"]
    assert [e.code for e in d.auto_route("mem", "u_ctrl0:err", "sideways", "u_ctrl1").errors] == ["E_AUTOROUTE_SYNTAX"]
    assert [e.code for e in d.auto_route("mem", "u_x:err", "to", "u_ctrl1").errors] == ["E_AUTOROUTE_SRC"]
    with pytest.raises(DesignError):
        d.auto_route("no_such_module", "u_ctrl0:err", "to", "u_ctrl1")


def test_wrapper_route_single_instance_end_to_end(tmp_path):
    """A wrapper-level annotation in 'top' (one instance) applies exactly like the equivalent explicit spec."""
    explicit_root = _copy(tmp_path / "a")
    annotated_root = _copy(tmp_path / "b")
    line = "//auto_route u_mem.u_ctrl0:err :: to :: u_ctrl1:err_in"
    _annotate(annotated_root, "top.v", [line])
    d = _design(annotated_root)
    res = d.collect_auto_routes()
    assert not res.warnings
    expected = RouteSpec(src=r"top\.u_mem\.u_ctrl0:err", dst=r"top\.u_mem\.u_ctrl1:err_in",
                         name="u_mem.u_ctrl0.err->u_ctrl1.err_in", net="err_in", create_dst=False)
    assert res.specs == [expected]
    _design(explicit_root).apply_routes([expected], then_expand=True)
    rep = d.apply_routes(res.specs, then_expand=True)
    assert not rep.residual
    for name in RTL_FILES:
        a = (explicit_root / "rtl" / name).read_bytes()
        b = (annotated_root / "rtl" / name).read_bytes()
        if name == "top.v":
            b = b.replace(f"   {line}\n".encode(), b"")
        assert a == b, name
    mem = (annotated_root / "rtl" / "mem.sv").read_text()
    assert "(err_in)" in mem and "err_in" in mem.split("u_ctrl0")[1].split(";")[0]
    assert d.apply_routes(d.collect_auto_routes().specs, then_expand=True).edits == []
