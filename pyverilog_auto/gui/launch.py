"""Start the GUI: Neovide (when available and a display exists) or terminal Neovim.

    python -m pyverilog_auto.gui.launch [PROJECT] [--ui auto|neovide|tui]
    python -m pyverilog_auto.gui.launch -f design.f [--top TOP]
    python -m pyverilog_auto.gui.launch --demo [backtick|prepro]

PROJECT is a directory with ``rtl_integ_project.toml`` or the manifest itself
(default: the current directory or the nearest parent with a manifest).

Neovim runs with ``--clean -u <package>/gui/nvim/init.lua``: no user config
and no third-party plugins are needed or loaded.  Executables are looked up
in this order: ``--nvim``/``--neovide``, ``RTL_INTEG_NVIM``/``RTL_INTEG_NEOVIDE``,
the bundle's ``runtime/`` directory, then ``PATH``.  With ``--ui auto`` a
Neovide that cannot start here (no display, or a Linux too old for it) gives
way to terminal Neovim.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Optional

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
INIT_LUA = os.path.join(PKG_DIR, "nvim", "init.lua")
EXE = ".exe" if os.name == "nt" else ""


def bundle_runtime() -> Optional[str]:
    """``<bundle>/runtime`` when running from a self-contained bundle."""
    # <bundle>/runtime/python/{python.exe | bin/python3}
    here = os.path.abspath(sys.prefix)
    for cand in (os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        if os.path.isdir(os.path.join(cand, "nvim")) and os.path.basename(cand) == "runtime":
            return cand
    return None


def find_nvim(explicit: Optional[str] = None) -> Optional[str]:
    for cand in (explicit, os.environ.get("RTL_INTEG_NVIM")):
        if cand:
            return cand
    rt = bundle_runtime()
    if rt:
        p = os.path.join(rt, "nvim", "bin", "nvim" + EXE)
        if os.path.isfile(p):
            return p
    return shutil.which("nvim")


def find_neovide(explicit: Optional[str] = None) -> Optional[str]:
    for cand in (explicit, os.environ.get("RTL_INTEG_NEOVIDE")):
        if cand:
            return cand
    rt = bundle_runtime()
    if rt:
        for name in ("neovide" + EXE, "neovide.AppImage", os.path.join("bin", "neovide" + EXE)):
            p = os.path.join(rt, "neovide", name)
            if os.path.isfile(p):
                return p
    return shutil.which("neovide")


def has_display() -> bool:
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def neovide_problem(path: str) -> Optional[str]:
    """Why Neovide at *path* cannot run on this machine, or None.

    Only checked on Linux, where the bundled Neovide needs a newer C library
    than many servers have (it then fails before printing its version)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        proc = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip().splitlines()
        return lines[-1] if lines else f"exit code {proc.returncode}"
    return None


def open_args(ns: argparse.Namespace) -> list[str]:
    if ns.filelist:
        out = ["-f", os.path.abspath(ns.filelist)]
        if ns.top:
            out.append(ns.top)
        return out
    if ns.project:
        return [os.path.abspath(ns.project)]
    return []


def build_command(ns: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    nvim = find_nvim(ns.nvim)
    if nvim is None:
        raise SystemExit("rtl-integ-gui: Neovim not found (install it, put it on PATH, or set RTL_INTEG_NVIM)")
    env = dict(os.environ)
    env["NVIM_APPNAME"] = "rtl_integ_gui"
    env["RTL_INTEG_PYTHON"] = sys.executable
    env["RTL_INTEG_OPEN"] = "\n".join(open_args(ns))
    nvim_args = ["--clean", "-u", INIT_LUA]
    ui = ns.ui
    neovide = find_neovide(ns.neovide) if ui in ("auto", "neovide") else None
    if ui == "neovide" and neovide is None:
        raise SystemExit("rtl-integ-gui: Neovide not found (use --ui tui or set RTL_INTEG_NEOVIDE)")
    if neovide is not None and ui == "auto" and has_display():
        problem = neovide_problem(neovide)
        if problem:
            print(f"rtl-integ-gui: Neovide cannot start here ({problem}); using terminal Neovim", file=sys.stderr)
            neovide = None
    if neovide is not None and (ui == "neovide" or has_display()):
        return [neovide, "--neovim-bin", nvim, "--", *nvim_args], env
    return [nvim, *nvim_args], env


# --demo NAME: the same design written in each template syntax
DEMOS = {
    "backtick": os.path.join(PKG_DIR, "examples", "demo"),
    "prepro": os.path.join(PKG_DIR, "examples", "demo_prepro"),
}
DEMO_DIR = DEMOS["backtick"]
SELFTEST_LUA = os.path.join(PKG_DIR, "nvim", "selftest.lua")


def copy_demo(name: str, work: str) -> str:
    return shutil.copytree(DEMOS[name], os.path.join(work, "demo"),
                           ignore=shutil.ignore_patterns("build", ".rtl_integ_gui"))


def version_text() -> str:
    """Tool and component versions (for bug reports)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        tool = version("pyverilog-auto")
    except PackageNotFoundError:
        tool = "unknown"
    lines = [f"rtl-integ-gui {tool}"]
    rt = bundle_runtime()
    info = os.path.join(rt, "versions.json") if rt else ""
    if info and os.path.isfile(info):
        with open(info, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        comps = ", ".join(f"{k} {v}" for k, v in data.get("components", {}).items())
        lines.append(f"bundle {data.get('target', '?')}: {comps}")
    else:
        lines.append(f"nvim: {find_nvim(None) or 'not found'}")
        lines.append(f"neovide: {find_neovide(None) or 'not found'}")
    lines.append(f"python {platform.python_version()} ({sys.executable})")
    try:
        lines.append(f"pyslang {version('pyslang')}")
    except PackageNotFoundError:
        lines.append("pyslang: not installed")
    lines.append(f"system: {platform.platform()}")
    return "\n".join(lines)


def selftest(nvim: Optional[str], *, demo: str = "backtick", keep: bool = False, timeout: float = 900) -> int:
    """Run the GUI headless on a copy of a demo project; print the report."""
    import tempfile

    exe = find_nvim(nvim)
    if exe is None:
        print("selftest: Neovim not found", file=sys.stderr)
        return 2
    work = tempfile.mkdtemp(prefix="rtl_integ_selftest_")
    proj = copy_demo(demo, work)
    out = os.path.join(work, "selftest.txt")
    env = dict(os.environ, RTL_SMOKE_PROJECT=proj, RTL_SMOKE_OUT=out, RTL_INTEG_PYTHON=sys.executable,
               NVIM_APPNAME="rtl_integ_gui_selftest", RTL_INTEG_SETTINGS=os.path.join(work, "settings.json"))
    env.pop("RTL_INTEG_OPEN", None)
    try:
        proc = subprocess.run([exe, "--headless", "--clean", "-u", INIT_LUA, "-c", f"luafile {SELFTEST_LUA}"],
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 3
        proc = None
    report = ""
    if os.path.exists(out):
        with open(out, "r", encoding="utf-8") as fh:
            report = fh.read()
    elif proc is not None:
        report = proc.stderr.decode("utf-8", "replace")
    print(report.rstrip() or "selftest: no report (timeout?)")
    ok = code == 0 and report.rstrip().endswith("OK all checks passed")
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"selftest files kept in {work}")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rtl-integ-gui", description=__doc__.split("\n\n")[0])
    ap.add_argument("project", nargs="?", help="project directory or rtl_integ_project.toml")
    ap.add_argument("-f", "--filelist", help="browse an existing filelist (view-only)")
    ap.add_argument("--top", help="top module (view-only mode)")
    ap.add_argument("--ui", choices=("auto", "neovide", "tui"), default="auto")
    ap.add_argument("--nvim", help="path to nvim")
    ap.add_argument("--neovide", help="path to neovide")
    ap.add_argument("--print-command", action="store_true", help="print the command instead of running it")
    ap.add_argument("--demo", nargs="?", const="backtick", choices=sorted(DEMOS),
                    help="open a copy of a demo project (template syntax: backtick, the default, or prepro)")
    ap.add_argument("--selftest", action="store_true", help="run the GUI headless on the demo and report")
    ap.add_argument("--version", action="store_true", help="print the tool and component versions")
    ap.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args(argv)
    if ns.version:
        print(version_text())
        return 0
    if ns.selftest:
        return selftest(ns.nvim, demo=ns.demo or "backtick", keep=ns.keep)
    if ns.demo:
        import tempfile

        ns.project = copy_demo(ns.demo, tempfile.mkdtemp(prefix="rtl_integ_demo_"))
        print(f"demo project copied to {ns.project}")
    cmd, env = build_command(ns)
    if ns.print_command:
        print(subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(cmd))
        return 0
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
