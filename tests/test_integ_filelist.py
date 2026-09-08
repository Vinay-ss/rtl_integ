"""Tests for pyverilog_auto.integ.filelist."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.integ.filelist import (
    DEFAULT_LIBEXTS,
    Filelist,
    filelist_to_config,
    parse_filelist,
    parse_flags,
    tokenize_filelist_text,
)
from pyverilog_auto.library.getopt import VerilogGetopt

TESTS_DIR = Path(__file__).resolve().parent


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


# ----------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------

def test_tokenize_comments_quotes_continuation():
    text = (
        "// header comment\n"
        "# hash comment\n"
        "-y lib  // trailing\n"
        "/* block\n comment */ +incdir+inc # trailing hash\n"
        "\"C:/My Files/a.v\" 'b c.v'\n"
        "-v \\\n"
        "  lib/cells.v\n"
        "top.v\n"
    )
    toks = tokenize_filelist_text(text)
    assert [t for t, _ in toks] == [
        "-y", "lib", "+incdir+inc", "C:/My Files/a.v", "b c.v", "-v", "lib/cells.v", "top.v",
    ]
    lines = dict(toks)
    assert lines["-y"] == 3
    assert lines["+incdir+inc"] == 5
    assert lines["top.v"] == 9


def test_tokenize_hash_inside_token_is_not_a_comment():
    toks = tokenize_filelist_text("+define+A=x#1 dir#2/a.v")
    assert [t for t, _ in toks] == ["+define+A=x#1", "dir#2/a.v"]


def test_tokenize_double_slash_inside_path():
    toks = tokenize_filelist_text("C:/a//b.v //comment")
    assert [t for t, _ in toks] == ["C:/a//b.v"]


# ----------------------------------------------------------------------
# Flag parsing
# ----------------------------------------------------------------------

def _mk_design(tmp_path: Path) -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc2").mkdir()
    for name in ("top.v", "rtl/a.v", "rtl/b.sv", "lib/cells.v"):
        (tmp_path / name).write_text(f"module {Path(name).stem}; endmodule\n")
    return tmp_path


def test_parse_flags_all_kinds(tmp_path, monkeypatch):
    _mk_design(tmp_path)
    monkeypatch.setenv("PROJ", str(tmp_path))
    f = tmp_path / "design.f"
    f.write_text(
        "// design filelist\n"
        "+incdir+inc+inc2\n"
        "-I inc\n"
        "+define+WIDTH=8+DEBUG\n"
        "-DSIM=1 -DFAST\n"
        "+libext+.v+.sv\n"
        "-y rtl\n"
        "-v lib/cells.v\n"
        "--top top -top top\n"
        "+librescan -timescale=1ns/1ps +notimingchecks\n"
        "+weird_flag -zzz\n"
        "$PROJ/top.v\n"
        "rtl/*.v\n"
        "rtl/*.sv\n"
    )
    fl = parse_filelist(str(f), relative_to="filelist")
    assert [_norm(p) for p in fl.include_dirs] == [_norm(str(tmp_path / "inc")), _norm(str(tmp_path / "inc2"))]
    assert fl.defines == {"WIDTH": "8", "DEBUG": "", "SIM": "1", "FAST": ""}
    assert fl.libexts == [".v", ".sv"]
    assert [_norm(p) for p in fl.library_dirs] == [_norm(str(tmp_path / "rtl"))]
    assert [_norm(p) for p in fl.library_files] == [_norm(str(tmp_path / "lib" / "cells.v"))]
    assert fl.tops == ["top"]
    assert [e.token for e in fl.unknown] == ["+weird_flag", "-zzz"]
    assert fl.missing == []
    assert [_norm(p) for p in fl.sources] == [
        _norm(str(tmp_path / "top.v")),
        _norm(str(tmp_path / "rtl" / "a.v")),
        _norm(str(tmp_path / "rtl" / "b.sv")),
    ]
    assert fl.warnings == []


def test_missing_paths_are_reported(tmp_path):
    f = tmp_path / "design.f"
    f.write_text("nope.v\n-y nodir\n-v nolib.v\n-f nested.f\n")
    fl = parse_filelist(str(f), relative_to="filelist")
    assert fl.sources == []
    assert fl.library_files == []
    # A missing -y dir is kept (with a diagnostic) so the user sees the intent.
    assert len(fl.library_dirs) == 1
    tokens = [e.token for e in fl.missing]
    assert tokens == ["nope.v", "-y nodir", "-v nolib.v", "-f nested.f"]
    assert all(e.origin == str(f) for e in fl.missing)
    assert fl.problems()


def test_nested_f_is_cwd_relative_and_F_is_file_relative(tmp_path, monkeypatch):
    _mk_design(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "leaf.v").write_text("module leaf; endmodule\n")
    # nested -f: relative paths resolve against cwd (tmp_path here)
    (sub / "cwd.f").write_text("rtl/a.v\n")
    # nested -F: relative paths resolve against the nested file's directory
    (sub / "rel.f").write_text("leaf.v\n")
    top = tmp_path / "design.f"
    top.write_text("-f sub/cwd.f\n-F sub/rel.f\ntop.v\n")
    monkeypatch.chdir(tmp_path)
    fl = parse_filelist("design.f")
    assert [_norm(p) for p in fl.sources] == [
        _norm(str(tmp_path / "rtl" / "a.v")),
        _norm(str(sub / "leaf.v")),
        _norm(str(tmp_path / "top.v")),
    ]
    assert len(fl.visited) == 3


def test_nested_f_with_filelist_policy(tmp_path):
    _mk_design(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "leaf.v").write_text("module leaf; endmodule\n")
    (sub / "inner.f").write_text("leaf.v\n")
    top = tmp_path / "design.f"
    top.write_text("-f sub/inner.f\n")
    fl = parse_filelist(str(top), relative_to="filelist")
    assert [_norm(p) for p in fl.sources] == [_norm(str(sub / "leaf.v"))]


def test_recursion_guard(tmp_path):
    a = tmp_path / "a.f"
    b = tmp_path / "b.f"
    a.write_text("-f b.f\n")
    b.write_text("-f a.f\n")
    fl = parse_filelist(str(a), relative_to="filelist")
    assert fl.missing == []
    assert any("cycle" in w for w in fl.warnings)
    assert len(fl.visited) == 2


def test_duplicates_are_dropped_case_insensitively_on_windows(tmp_path):
    _mk_design(tmp_path)
    f = tmp_path / "design.f"
    f.write_text("top.v\ntop.v\n./top.v\n-y rtl\n-y rtl/\n")
    fl = parse_filelist(str(f), relative_to="filelist")
    assert len(fl.sources) == 1
    assert len(fl.library_dirs) == 1


def test_parse_flags_from_argv(tmp_path):
    _mk_design(tmp_path)
    fl = parse_flags(["-y rtl +libext+.v", "top.v"], base_dir=str(tmp_path))
    assert [_norm(p) for p in fl.library_dirs] == [_norm(str(tmp_path / "rtl"))]
    assert [_norm(p) for p in fl.sources] == [_norm(str(tmp_path / "top.v"))]
    assert fl.libexts == [".v"]


def test_from_args_and_merge(tmp_path):
    _mk_design(tmp_path)
    a = Filelist.from_args(["top.v"], library_dirs=["rtl"], defines={"A": "1"}, base_dir=str(tmp_path))
    b = Filelist.from_args(["rtl/a.v", "top.v"], include_dirs=["inc"], defines={"A": "2", "B": ""},
                           libexts=[".sv"], base_dir=str(tmp_path))
    a.merge(b)
    assert [Path(p).name for p in a.sources] == ["top.v", "a.v"]
    assert a.defines == {"A": "1", "B": ""}   # first definition wins
    assert a.libexts == [".sv"]
    assert a.effective_libexts == [".sv"]
    assert Filelist().effective_libexts == DEFAULT_LIBEXTS


# ----------------------------------------------------------------------
# VerilogConfig bridge
# ----------------------------------------------------------------------

def test_filelist_to_config_mapping(tmp_path):
    _mk_design(tmp_path)
    f = tmp_path / "design.f"
    f.write_text("+incdir+inc\n-y rtl\n-v lib/cells.v\n+define+W=4\ntop.v\n")
    fl = parse_filelist(str(f), relative_to="filelist")
    cfg = filelist_to_config(fl)
    assert cfg.library_directories[0] == "."
    assert [_norm(p) for p in cfg.library_directories[1:]] == [_norm(str(tmp_path / "inc")), _norm(str(tmp_path / "rtl"))]
    assert [_norm(p) for p in cfg.library_files] == [_norm(str(tmp_path / "lib" / "cells.v")), _norm(str(tmp_path / "top.v"))]
    assert cfg.library_extensions == DEFAULT_LIBEXTS
    assert cfg.defines == {"W": "4"}

    base = VerilogConfig(library_directories=["/x"], library_extensions=[".vh"], defines={"W": "9", "Z": "1"})
    cfg2 = filelist_to_config(fl, base)
    assert cfg2.library_directories[0] == "/x"
    assert cfg2.library_extensions == [".vh"]     # no +libext+ in the filelist -> base kept
    assert cfg2.defines == {"W": "9", "Z": "1"}   # base wins
    assert base.library_files == []               # base not mutated


def test_parity_with_verilog_getopt_on_fixture_flag_files():
    # tests/flag_f_reeves.vc contains "-y subdir"; the classic getopt resolves
    # it against the library directories, we resolve against the file's dir.
    vc = TESTS_DIR / "flag_f_reeves.vc"
    getopt_cfg = VerilogGetopt(VerilogConfig(library_directories=[str(TESTS_DIR)])).parse_flag_file(str(vc))
    fl = parse_filelist(str(vc), relative_to="filelist")
    assert len(fl.library_dirs) == 1
    assert any(_same(d, fl.library_dirs[0]) for d in getopt_cfg.library_directories)

    # tests/subdir/flag_frel_reeves.vc contains "-y ." (the -F convention).
    vc2 = TESTS_DIR / "subdir" / "flag_frel_reeves.vc"
    getopt_cfg2 = VerilogGetopt(VerilogConfig(library_directories=["."])).parse_flag_file(str(vc2), relative_paths=True)
    fl2 = parse_filelist(str(vc2), relative_to="filelist")
    assert _same(fl2.library_dirs[0], str(TESTS_DIR / "subdir"))
    assert any(_same(d, fl2.library_dirs[0]) for d in getopt_cfg2.library_directories)


def test_inject_path_fixture_lists_sources():
    fl = parse_filelist(str(TESTS_DIR / "inject_path.f"), relative_to="filelist")
    assert [Path(p).name for p in fl.sources] == ["inject_path.v", "inject_path_sub.v"]
    assert fl.missing == []
