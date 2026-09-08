"""Unit tests for pyverilog_auto.integ.route and routes_file (no pyslang)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pyverilog_auto.integ.route import RouteError, RouteSpec, expand_backrefs, pair_routes, split_endpoint
from pyverilog_auto.integ.routes_file import load_routes

PATHS = [
    "top", "top.u_cluster", "top.u_cluster.u_core0", "top.u_cluster.u_core0.u_dma",
    "top.u_cluster.u_core1", "top.u_cluster.u_core1.u_dma", "top.u_cluster.u_ctl.u_timer",
    "top.u_mem", "top.u_mem.u_ctrl0", "top.u_mem.u_ctrl1", "top.gen_x[0].u_leaf", "top.gen_x[1].u_leaf",
]


def test_split_endpoint():
    assert split_endpoint(r"top\.u_a:port") == (r"top\.u_a", "port")
    assert split_endpoint(r"top\.(?:a|b)\.u:p") == (r"top\.(?:a|b)\.u", "p")
    assert split_endpoint(r"top\.[^:]+\.u:p") == (r"top\.[^:]+\.u", "p")
    assert split_endpoint(r"top\.u_a") == (r"top\.u_a", None)
    assert split_endpoint(r"top\.u_a:") == (r"top\.u_a", None)


def test_expand_backrefs():
    m = re.fullmatch(r"top\.u_core(\d+)\.(?P<leaf>\w+)", "top.u_core1.u_dma")
    assert expand_backrefs(r"top\.u_mem\.u_ctrl\1:x", m, regex_escape=True) == r"top\.u_mem\.u_ctrl1:x"
    assert expand_backrefs(r"\g<leaf>_\1", m, regex_escape=False) == "u_dma_1"
    assert expand_backrefs(r"a\d+\\b\1", m, regex_escape=True) == r"a\d+\\b1"
    m2 = re.fullmatch(r"(a)|(b)", "a")
    with pytest.raises(RouteError) as ei:
        expand_backrefs(r"x\2", m2, regex_escape=False)
    assert ei.value.diagnostics[0].code == "E_BACKREF_UNMATCHED"
    with pytest.raises(RouteError) as ei:
        expand_backrefs(r"x\3", m2, regex_escape=False)
    assert ei.value.diagnostics[0].code == "E_BAD_BACKREF"
    # captured text is escaped when it becomes a regex
    m3 = re.fullmatch(r"top\.(gen_x\[0\])\.u_leaf", "top.gen_x[0].u_leaf")
    assert expand_backrefs(r"top\.\1\.other", m3, regex_escape=True) == r"top\.gen_x\[0\]\.other"


def test_pairing_with_backrefs():
    spec = RouteSpec(src=r"top\.u_cluster\.u_core(\d+)\.u_dma:m_axi", dst=r"top\.u_mem\.u_ctrl\1:s_axi")
    pairs = pair_routes(spec, PATHS)
    assert [(str(p.src), str(p.dst)) for p in pairs] == [
        ("top.u_cluster.u_core0.u_dma:m_axi", "top.u_mem.u_ctrl0:s_axi"),
        ("top.u_cluster.u_core1.u_dma:m_axi", "top.u_mem.u_ctrl1:s_axi"),
    ]


def test_pairing_fanout_and_default_port():
    spec = RouteSpec(src=r"top\.u_cluster\.u_ctl\.u_timer:tick", dst=r"top\.u_mem\.u_ctrl\d")
    pairs = pair_routes(spec, PATHS)
    assert [str(p.dst) for p in pairs] == ["top.u_mem.u_ctrl0:tick", "top.u_mem.u_ctrl1:tick"]
    spec2 = RouteSpec(src=r"top\.u_cluster\.u_core(\d+)\.u_dma:busy", dst=r"top\.u_cluster:core\1_busy")
    pairs = pair_routes(spec2, PATHS)
    assert [p.dst.port for p in pairs] == ["core0_busy", "core1_busy"]


def test_pairing_errors():
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"nope:x", dst=r"top"), PATHS)
    assert ei.value.diagnostics[0].code == "E_NO_SRC_MATCH"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.u_mem:x", dst=r"nope"), PATHS)
    assert ei.value.diagnostics[0].code == "E_NO_DST_MATCH"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.u_mem:x", dst=r"top\.u_mem"), PATHS)
    assert ei.value.diagnostics[0].code == "E_SAME_INSTANCE"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.u_mem", dst=r"top"), PATHS)
    assert ei.value.diagnostics[0].code == "E_SPEC"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"*bad:x", dst=r"top"), PATHS)
    assert ei.value.diagnostics[0].code == "E_BAD_SRC_REGEX"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.u_mem:x", dst=r"top:1bad"), PATHS)
    assert ei.value.diagnostics[0].code == "E_SPEC"
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.(u_mem\.u_ctrl0):x", dst=r"top:\1"), PATHS)
    assert ei.value.diagnostics[0].code == "E_BAD_PORT_NAME"


def test_spec_parsing_and_validation():
    s = RouteSpec.parse(r"top\.a:p -> top\.b:q")
    assert (s.src, s.dst) == (r"top\.a:p", r"top\.b:q")
    with pytest.raises(RouteError):
        RouteSpec.from_mapping({"src": "a:b", "dst": "c", "bogus": 1})
    with pytest.raises(RouteError):
        RouteSpec.from_mapping({"src": "a:b"})
    with pytest.raises(RouteError):
        RouteSpec.from_mapping({"src": "a:b", "dst": "c", "modport_policy": "x"})
    s = RouteSpec.from_mapping({"src": "a:b", "dst": "c", "iface_conn": {"clk": "clk"}, "comment": False})
    assert s.iface_conn == {"clk": "clk"} and s.comment is False and s.label() == "a:b -> c"


def test_routes_file_toml_and_json(tmp_path: Path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        "[[route]]\nname = 'axi'\nsrc = 'top\\.u_core(\\d+)\\.u_dma:m_axi'\ndst = 'top\\.u_mem\\.u_ctrl\\1:s_axi'\n"
        "iface_conn = { clk = 'clk' }\n\n[[route]]\nsrc = 'top\\.a:x'\ndst = 'top\\.b'\n",
        encoding="utf-8",
    )
    specs = load_routes(str(toml))
    assert [s.name for s in specs] == ["axi", None]
    assert specs[0].src == r"top\.u_core(\d+)\.u_dma:m_axi" and specs[0].iface_conn == {"clk": "clk"}
    js = tmp_path / "r.json"
    js.write_text(json.dumps({"routes": [{"src": "top\\.a:x", "dst": "top\\.b"}, "top\\.c:y -> top\\.d"]}))
    specs = load_routes(str(js))
    assert [s.src for s in specs] == [r"top\.a:x", r"top\.c:y"]
    bad = tmp_path / "r.yaml"
    bad.write_text("x")
    with pytest.raises(RouteError):
        load_routes(str(bad))
    js2 = tmp_path / "r2.json"
    js2.write_text(json.dumps([{"src": "a:b", "dst": "c", "unknown": 1}]))
    with pytest.raises(RouteError) as ei:
        load_routes(str(js2))
    assert "unknown" in str(ei.value)


def test_wildcards_are_matched_from_the_top():
    from pyverilog_auto.integ.route import RouteError, RouteSpec, pair_routes

    paths = ["top", "top.core", "top.core.instA", "top.core.instA.instB", "top.core.instA.instB.instE", "top.mem"]
    # '.*' wildcards work, written from the top
    pairs = pair_routes(RouteSpec(src=r"top.*core.*instE:sig", dst=r"top\.mem:sig"), paths)
    assert [(p.src.path, p.dst.path) for p in pairs] == [("top.core.instA.instB.instE", "top.mem")]
    # groups still pair the ends
    pairs = pair_routes(RouteSpec(src=r"top.*core.*(inst[A-Z]):sig", dst=r"top\.mem:sig_\1"), paths)
    assert {(p.src.path, p.dst.port) for p in pairs} == {
        ("top.core.instA", "sig_instA"), ("top.core.instA.instB", "sig_instB"), ("top.core.instA.instB.instE", "sig_instE")}
    # a pattern that omits the top does not match: no implicit tail anchoring, and the message says so
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"core.*instE:sig", dst=r"top\.mem:sig"), paths)
    msg = str(ei.value)
    assert "E_NO_SRC_MATCH" in msg and "from the top" in msg and "top.*core.*instE" in msg
    with pytest.raises(RouteError) as ei:
        pair_routes(RouteSpec(src=r"top\.core\.instA\.instB\.instE:sig", dst=r"mem:sig"), paths)
    assert "E_NO_DST_MATCH" in str(ei.value) and "from the top" in str(ei.value)
