"""Neovim front end: headless smoke test against the real backend, launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyverilog_auto.gui import launch

HERE = Path(__file__).parent
NVIM = os.environ.get("RTL_INTEG_NVIM") or shutil.which("nvim")
needs_nvim = pytest.mark.skipif(NVIM is None, reason="Neovim not found (set RTL_INTEG_NVIM)")


@needs_nvim
@pytest.mark.parametrize("demo", ["backtick", "prepro"])
def test_nvim_selftest(slang, demo):
    """The packaged self-test: the GUI driven headless on a copy of a demo."""
    proc = subprocess.run([sys.executable, "-m", "pyverilog_auto.gui.launch", "--selftest", "--demo", demo,
                           "--nvim", NVIM],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900,
                          env=dict(os.environ, PYTHONWARNINGS="ignore"))
    report = proc.stdout.decode(errors="replace")
    assert proc.returncode == 0, report + proc.stderr.decode(errors="replace")
    assert "OK all checks passed" in report


@pytest.mark.parametrize("demo, fixture", [("backtick", "proj_bt"), ("prepro", "proj_py")])
def test_demo_matches_fixture(demo, fixture):
    """Each demo is the fixture the operations are tested on."""
    demo = Path(launch.DEMOS[demo])
    fix = HERE / "fixtures" / fixture
    files = sorted(p.relative_to(fix).as_posix() for p in fix.rglob("*") if p.is_file() and "build" not in p.parts)
    assert files == sorted(p.relative_to(demo).as_posix() for p in demo.rglob("*")
                           if p.is_file() and "build" not in p.parts)
    for f in files:
        assert (demo / f).read_bytes() == (fix / f).read_bytes(), f


def test_launch_command_tui(monkeypatch, tmp_path):
    monkeypatch.setenv("RTL_INTEG_NVIM", str(tmp_path / "nvim"))
    monkeypatch.delenv("RTL_INTEG_NEOVIDE", raising=False)
    monkeypatch.setattr(launch, "find_neovide", lambda explicit=None: None)
    ns = launch.argparse.Namespace(project=str(tmp_path), filelist=None, top=None, ui="auto", nvim=None,
                                   neovide=None)
    cmd, env = launch.build_command(ns)
    assert cmd == [str(tmp_path / "nvim"), "--clean", "-u", launch.INIT_LUA]
    assert env["RTL_INTEG_OPEN"] == str(tmp_path)
    assert env["RTL_INTEG_PYTHON"] == sys.executable
    assert env["NVIM_APPNAME"] == "rtl_integ_gui"


def test_launch_command_neovide(monkeypatch, tmp_path):
    monkeypatch.setenv("RTL_INTEG_NVIM", "nvim-bin")
    monkeypatch.setattr(launch, "has_display", lambda: True)
    monkeypatch.setattr(launch, "neovide_problem", lambda path: None)
    ns = launch.argparse.Namespace(project=None, filelist="d.f", top="top", ui="auto", nvim=None,
                                   neovide="neovide-bin")
    cmd, env = launch.build_command(ns)
    assert cmd[:4] == ["neovide-bin", "--neovim-bin", "nvim-bin", "--"]
    assert env["RTL_INTEG_OPEN"].split("\n") == ["-f", os.path.abspath("d.f"), "top"]


def test_launch_falls_back_when_neovide_cannot_start(monkeypatch, capsys):
    # e.g. the bundled Neovide on a Linux whose C library is too old for it
    monkeypatch.setenv("RTL_INTEG_NVIM", "nvim-bin")
    monkeypatch.setattr(launch, "has_display", lambda: True)
    monkeypatch.setattr(launch, "neovide_problem", lambda path: "GLIBC_2.35 not found")
    ns = launch.argparse.Namespace(project=None, filelist=None, top=None, ui="auto", nvim=None,
                                   neovide="neovide-bin")
    cmd, _env = launch.build_command(ns)
    assert cmd[0] == "nvim-bin"
    assert "GLIBC_2.35 not found" in capsys.readouterr().err
    ns.ui = "neovide"                                   # asked for explicitly: no probe, no fallback
    assert launch.build_command(ns)[0][0] == "neovide-bin"


def test_neovide_problem_reports_a_failing_binary(tmp_path):
    if not sys.platform.startswith("linux"):
        assert launch.neovide_problem(str(tmp_path / "missing")) is None   # only checked on Linux
    else:
        assert launch.neovide_problem(str(tmp_path / "missing"))


def test_version_text():
    text = launch.version_text()
    assert text.startswith("rtl-integ-gui ")
    assert "python " in text and "system: " in text


def test_launch_falls_back_to_tui_without_display(monkeypatch):
    monkeypatch.setenv("RTL_INTEG_NVIM", "nvim-bin")
    monkeypatch.setattr(launch, "has_display", lambda: False)
    ns = launch.argparse.Namespace(project=None, filelist=None, top=None, ui="auto", nvim=None,
                                   neovide="neovide-bin")
    cmd, _env = launch.build_command(ns)
    assert cmd[0] == "nvim-bin"
