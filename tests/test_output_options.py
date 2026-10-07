"""Output options: config fields, CLI flags, strip/lineup wiring, RouteSpec.create_dst.

The wiring tests monkeypatch the formatter and the stripper, so they test the
plumbing independently of the formatting and stripping rules.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import pyverilog_auto.auto.inst_lineup as inst_lineup_mod
import pyverilog_auto.auto.strip as strip_mod
from pyverilog_auto.cli import _build_config, build_parser, main
from pyverilog_auto.config import VerilogConfig, port_comment_fields
from pyverilog_auto.integ.cli_cmds import build_design
from pyverilog_auto.integ.route import RouteError, RouteSpec
from pyverilog_auto.signal import ModDecls

LEAF = """\
module leaf (input [7:0] a, output y);
   assign y = ^a;
endmodule
"""

TOP = """\
module top (/*AUTOARG*/);
   /*AUTOWIRE*/
   leaf u_leaf (/*AUTOINST*/);
endmodule
// Local Variables:
// verilog-auto-inst-lineup: nil
// verilog-auto-inst-port-comment: "type"
// verilog-auto-inst-comment-column: 72
// End:
"""

PLAIN_CRLF = b"module m (input a, output b);\r\n   assign b = a;  // user comment\r\nendmodule\r\n"

FLAGS = ["--inst-lineup", "--inst-port-comment", "width,dir", "--inst-comment-column", "50"]


@pytest.fixture
def design_dir(tmp_path: Path) -> Path:
    (tmp_path / "leaf.v").write_text(LEAF, newline="\n")
    (tmp_path / "top.v").write_text(TOP, newline="\n")
    return tmp_path


@pytest.fixture
def lineup_calls(monkeypatch):
    calls: list[dict] = []

    def fake(text, lookup, cfg):
        calls.append({"text": text, "lookup": lookup, "cfg": cfg})
        return text

    monkeypatch.setattr(inst_lineup_mod, "lineup_instances", fake)
    return calls


@pytest.fixture
def strip_calls(monkeypatch):
    calls: list[str] = []

    def fake(text):
        calls.append(text)
        return text

    monkeypatch.setattr(strip_mod, "strip_autos", fake)
    return calls


def _cfg_for(calls: list[dict], module: str) -> VerilogConfig:
    hits = [c["cfg"] for c in calls if f"module {module}" in c["text"]]
    assert hits, f"lineup_instances never called for {module}"
    return hits[-1]


# ----------------------------------------------------------------------
# Config + CLI parsing
# ----------------------------------------------------------------------

def test_config_defaults_off():
    cfg = VerilogConfig()
    assert cfg.auto_inst_lineup is False
    assert cfg.auto_inst_port_comment is None
    assert cfg.auto_inst_comment_column == 0


def test_port_comment_fields():
    assert port_comment_fields("type,dir") == ("dir", "type")
    assert port_comment_fields("dir width type") == ("dir", "width", "type")
    assert port_comment_fields(" width , width ") == ("width",)
    assert port_comment_fields(True) == port_comment_fields("t") == ("dir", "width", "type")
    assert port_comment_fields(None) == port_comment_fields("") == port_comment_fields("nil") == ()
    assert port_comment_fields("dir bogus") == ("dir",)
    with pytest.raises(ValueError):
        port_comment_fields("dir bogus", strict=True)


def test_expand_flags_reach_config():
    args = build_parser().parse_args(["expand", *FLAGS, "x.v"])
    assert args.inst_port_comment == "dir width"          # normalized, rendered order
    cfg = _build_config(args)
    assert (cfg.auto_inst_lineup, cfg.auto_inst_port_comment, cfg.auto_inst_comment_column) == (True, "dir width", 50)
    cfg0 = _build_config(build_parser().parse_args(["expand", "x.v"]))
    assert (cfg0.auto_inst_lineup, cfg0.auto_inst_port_comment, cfg0.auto_inst_comment_column) == (False, None, 0)


@pytest.mark.parametrize("bad", [["--inst-port-comment", "dir,colour"], ["--inst-port-comment", ","],
                                 ["--inst-comment-column", "-1"], ["--inst-comment-column", "x"]])
def test_flag_validation(bad):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["expand", *bad, "x.v"])


@pytest.mark.parametrize("cmd", ["expand", "diff", "integrate", "route"])
def test_flags_accepted(cmd):
    args = build_parser().parse_args([cmd, *FLAGS, "--strip-autos", "x.v"])
    assert args.inst_lineup and args.inst_port_comment == "dir width" and args.inst_comment_column == 50
    assert args.strip_autos is True


@pytest.mark.parametrize("cmd", ["delete", "inject", "indent", "hierarchy"])
@pytest.mark.parametrize("flag", [["--inst-lineup"], ["--strip-autos"]])
def test_flags_not_on_other_commands(cmd, flag):
    with pytest.raises(SystemExit):
        build_parser().parse_args([cmd, *flag, "x.v"])


def test_local_vars_convert_new_fields():
    from pyverilog_auto.local_vars import apply_local_vars

    cfg = apply_local_vars(VerilogConfig(), {
        "verilog-auto-inst-lineup": "t",
        "verilog-auto-inst-port-comment": '"dir width type"',
        "verilog-auto-inst-comment-column": "64",
    })
    assert (cfg.auto_inst_lineup, cfg.auto_inst_port_comment, cfg.auto_inst_comment_column) == (True, "dir width type", 64)
    cfg = apply_local_vars(cfg, {"verilog-auto-inst-lineup": "nil", "verilog-auto-inst-port-comment": "nil"})
    assert cfg.auto_inst_lineup is False and cfg.auto_inst_port_comment is None


def test_auto_lisp_sets_new_fields():
    from pyverilog_auto.auto.engine import _apply_lisp_env_to_config

    class _Env:
        env = {"verilog-auto-inst-lineup": "t", "verilog-auto-inst-port-comment": "dir",
               "verilog-auto-inst-comment-column": "56"}

    cfg = VerilogConfig()
    _apply_lisp_env_to_config(cfg, _Env())
    assert (cfg.auto_inst_lineup, cfg.auto_inst_port_comment, cfg.auto_inst_comment_column) == (True, "dir", 56)


# ----------------------------------------------------------------------
# expand path: engine wiring, precedence, lookup
# ----------------------------------------------------------------------

def test_expand_lineup_not_called_by_default(design_dir, lineup_calls, capsys):
    assert main(["expand", "--no-save", str(design_dir / "leaf.v")]) == 0
    assert lineup_calls == []


def test_expand_cli_flags_and_local_var_override(design_dir, lineup_calls, capsys):
    rc = main(["expand", *FLAGS, "--no-save", str(design_dir / "leaf.v"), str(design_dir / "top.v")])
    assert rc == 0
    leaf = _cfg_for(lineup_calls, "leaf")
    assert (leaf.auto_inst_lineup, leaf.auto_inst_port_comment, leaf.auto_inst_comment_column) == (True, "dir width", 50)
    top = _cfg_for(lineup_calls, "top")                   # Local Variables win over the CLI
    assert (top.auto_inst_lineup, top.auto_inst_port_comment, top.auto_inst_comment_column) == (False, "type", 72)
    # the formatter sees the fully expanded text and a working module lookup
    call = next(c for c in lineup_calls if "module top" in c["text"])
    assert ".a" in call["text"] and "// Inputs" in call["text"]
    assert isinstance(call["lookup"]("leaf"), ModDecls)
    assert call["lookup"]("no_such_module") is None


def test_expand_lineup_result_replaces_buffer(design_dir, monkeypatch, capsys):
    monkeypatch.setattr(inst_lineup_mod, "lineup_instances", lambda text, lookup, cfg: text + "// lined up\n")
    assert main(["expand", "--inst-lineup", "--no-save", str(design_dir / "leaf.v")]) == 0
    assert capsys.readouterr().out.endswith("endmodule\n// lined up\n")


# ----------------------------------------------------------------------
# design path (integrate): base config from the flags, Local Variables win
# ----------------------------------------------------------------------

def test_design_effective_config(design_dir):
    args = build_parser().parse_args(["integrate", "--no-slang", *FLAGS,
                                      str(design_dir / "leaf.v"), str(design_dir / "top.v")])
    design = build_design(args)
    assert design is not None
    leaf = design.effective_config(str(design_dir / "leaf.v"))
    assert (leaf.auto_inst_lineup, leaf.auto_inst_port_comment, leaf.auto_inst_comment_column) == (True, "dir width", 50)
    top = design.effective_config(str(design_dir / "top.v"))
    assert (top.auto_inst_lineup, top.auto_inst_port_comment, top.auto_inst_comment_column) == (False, "type", 72)


def test_design_without_flags_unchanged(design_dir):
    args = build_parser().parse_args(["integrate", "--no-slang", str(design_dir / "leaf.v"), str(design_dir / "top.v")])
    design = build_design(args)
    from pyverilog_auto.integ.design import Design

    plain = Design.from_files([str(design_dir / "leaf.v"), str(design_dir / "top.v")], backend="text")
    assert design.base_config == plain.base_config


def test_integrate_flags_reach_engine(design_dir, lineup_calls):
    rc = main(["integrate", "--no-slang", "--quiet", *FLAGS, str(design_dir / "leaf.v"), str(design_dir / "top.v")])
    assert rc == 0
    leaf = _cfg_for(lineup_calls, "leaf")
    assert (leaf.auto_inst_lineup, leaf.auto_inst_port_comment, leaf.auto_inst_comment_column) == (True, "dir width", 50)
    top = _cfg_for(lineup_calls, "top")
    assert (top.auto_inst_lineup, top.auto_inst_port_comment, top.auto_inst_comment_column) == (False, "type", 72)


# ----------------------------------------------------------------------
# strip wiring
# ----------------------------------------------------------------------

def test_strip_subcommand_noop_preserves_crlf(tmp_path, capsys):
    f = tmp_path / "m.v"
    f.write_bytes(PLAIN_CRLF)
    stamp = f.stat().st_mtime_ns
    assert main(["strip", str(f)]) == 0
    assert f.read_bytes() == PLAIN_CRLF and f.stat().st_mtime_ns == stamp
    assert main(["strip", "--no-save", str(f)]) == 0
    assert capsys.readouterr().out == PLAIN_CRLF.decode()


def test_strip_subcommand_writes_crlf(tmp_path, monkeypatch):
    monkeypatch.setattr(strip_mod, "strip_autos", lambda text: text.replace("  // user comment", ""))
    f = tmp_path / "m.v"
    f.write_bytes(PLAIN_CRLF)
    assert main(["strip", str(f)]) == 0
    assert f.read_bytes() == PLAIN_CRLF.replace(b"  // user comment", b"")


def test_strip_subcommand_missing_file(tmp_path, capsys):
    assert main(["strip", str(tmp_path / "nope.v")]) == 1


def test_expand_and_diff_strip_autos(design_dir, monkeypatch, capsys):
    monkeypatch.setattr(strip_mod, "strip_autos", lambda text: text.replace("/*AUTOINST*/", ""))
    top = str(design_dir / "top.v")
    assert main(["diff", "--strip-autos", top]) == 0
    out = capsys.readouterr().out
    assert "-   leaf u_leaf (/*AUTOINST*/);" in out and "+   leaf u_leaf (" in out
    assert "/*AUTOINST*/" not in "".join(l for l in out.splitlines() if l.startswith("+"))
    assert main(["expand", "--strip-autos", top]) == 0
    text = Path(top).read_text()
    assert "/*AUTOINST*/" not in text and ".a" in text


def test_expand_strips_once_after_every_file(design_dir, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(inst_lineup_mod, "lineup_instances",
                        lambda text, lookup, cfg: events.append("expand") or text)
    monkeypatch.setattr(strip_mod, "strip_autos", lambda text: events.append("strip") or text)
    files = [str(design_dir / "leaf.v"), str(design_dir / "top.v")]
    assert main(["expand", "--inst-port-comment", "dir", "--strip-autos", *files]) == 0
    assert events == ["expand", "expand", "strip", "strip"]


def test_integrate_strip_every_source_file_also_on_rerun(design_dir, strip_calls):
    files = [str(design_dir / "leaf.v"), str(design_dir / "top.v")]
    assert main(["integrate", "--no-slang", "--quiet", "--strip-autos", *files]) == 0
    assert sorted("module top" in t for t in strip_calls) == [False, True]
    strip_calls.clear()
    assert main(["integrate", "--no-slang", "--quiet", "--strip-autos", *files]) == 0   # nothing to expand now
    assert sorted("module top" in t for t in strip_calls) == [False, True]


def test_integrate_strip_dry_run_writes_nothing(design_dir, monkeypatch, capsys):
    monkeypatch.setattr(strip_mod, "strip_autos", lambda text: text.replace("assign y", "assign  y"))
    files = [str(design_dir / "leaf.v"), str(design_dir / "top.v")]
    before = {p: Path(p).read_bytes() for p in files}
    assert main(["integrate", "--no-slang", "--diff", "--strip-autos", *files]) == 0
    out = capsys.readouterr().out
    assert "+   assign  y = ^a;" in out
    assert {p: Path(p).read_bytes() for p in files} == before


def test_design_strip_autos_keeps_crlf(tmp_path, monkeypatch):
    from pyverilog_auto.integ.design import Design

    monkeypatch.setattr(strip_mod, "strip_autos", lambda text: text.replace("assign b = a;", "assign b = !a;"))
    f = tmp_path / "m.v"
    f.write_bytes(PLAIN_CRLF)
    design = Design.from_files([str(f)], backend="text")
    assert design.strip_autos(dry_run=True) == [Path(str(f.resolve()))]
    assert f.read_bytes() == PLAIN_CRLF
    design = Design.from_files([str(f)], backend="text")
    assert design.strip_autos([str(f)]) == [Path(str(f.resolve()))]
    assert f.read_bytes() == PLAIN_CRLF.replace(b"assign b = a;", b"assign b = !a;")
    assert design.strip_autos() == []                     # idempotent fake: nothing left to change


def test_route_strip_autos(tmp_path, strip_calls, capsys):
    pytest.importorskip("pyslang")
    from pyverilog_auto.integ import is_slang_available

    if not is_slang_available():
        pytest.skip("pyslang backend unavailable")
    work = tmp_path / "work"
    shutil.copytree(Path(__file__).resolve().parent.parent / "sample_env" / "route_demo" / "src", work)
    f = ["-f", str(work / "design.f"), "--relative-to", "filelist", "--quiet"]
    before = {p.name: p.read_bytes() for p in work.iterdir()}
    assert main(["route", *f, "--collect", "--dry-run", "--strip-autos"]) == 0
    assert {p.name: p.read_bytes() for p in work.iterdir()} == before
    assert any("module top" in t for t in strip_calls)
    strip_calls.clear()
    assert main(["route", *f, "--collect", "--then-expand", "--strip-autos"]) == 0
    for mod in ("top", "core_a", "core_b", "leaf_c"):
        assert any(f"module {mod}" in t for t in strip_calls), mod


def test_route_pin_pad_column_follows_auto_inst_column(design_dir):
    from pyverilog_auto.integ.design import Design
    from pyverilog_auto.integ.route_plan import RoutePlanner

    wide = design_dir / "wide.v"
    wide.write_text("module wide;\nendmodule\n// Local Variables:\n// verilog-auto-inst-column: 56\n// End:\n",
                    newline="\n")
    design = Design.from_files([str(design_dir / "leaf.v"), str(wide)], backend="text")
    planner = RoutePlanner(design, [])
    assert planner._auto_inst_column(design.file(str(design_dir / "leaf.v")).key) == 40
    assert planner._auto_inst_column(design.file(str(wide)).key) == 56


# ----------------------------------------------------------------------
# RouteSpec.create_dst
# ----------------------------------------------------------------------

def test_route_spec_create_dst_mapping():
    base = {"src": "top\\.a:x", "dst": "top\\.b"}
    assert RouteSpec.from_mapping(base).create_dst is True
    assert RouteSpec(src="a:x", dst="b").create_dst is True
    assert RouteSpec.from_mapping({**base, "create_dst": False}).create_dst is False
    assert RouteSpec.from_mapping({**base, "create_dst": "false"}).create_dst is False
    assert RouteSpec.from_mapping({**base, "create_dst": True}).create_dst is True
    with pytest.raises(RouteError):
        RouteSpec.from_mapping({**base, "create_dst": "maybe"})


def test_route_spec_create_dst_toml(tmp_path):
    from pyverilog_auto.integ.routes_file import load_routes

    p = tmp_path / "routes.toml"
    p.write_text("[[route]]\nsrc = 'top\\.a:x'\ndst = 'top\\.b'\ncreate_dst = false\n\n"
                 "[[route]]\nsrc = 'top\\.a:y'\ndst = 'top\\.b'\n", newline="\n")
    specs = load_routes(str(p))
    assert [s.create_dst for s in specs] == [False, True]
