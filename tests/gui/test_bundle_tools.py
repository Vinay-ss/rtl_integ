"""Bundle tooling (tools/bundle): pins, cache names, trimming, notices, packages."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "bundle"
sys.path.insert(0, str(TOOLS))

import build_bundle as bb  # noqa: E402
import package_linux as pl  # noqa: E402


def test_every_component_is_pinned_for_every_target():
    pins = bb._toml(bb.VERSIONS)
    for sec in ("python", "neovim", "neovide"):
        for target in bb.TARGETS:
            spec = pins[sec][target]
            assert spec["url"].startswith("https://") and len(spec["sha256"]) == 64, (sec, target)
        assert pins[sec]["license"] and pins[sec]["source"]
    for target in bb.TARGETS:
        assert pins["pyslang"][target]["tag"]
    assert len(pins["pyslang"]["sdist"]["sha256"]) == 64
    assert len(pins["python"]["licenses"]["sha256"]) == 64
    # Linux Neovim is the glibc 2.17 build
    assert "neovim-releases" in pins["neovim"]["linux-x86_64"]["url"]
    srcs = pins["third_party_source"]
    assert {s["name"].split()[0] for s in srcs} == {"neovim", "unibilium", "gettext", "libiconv"}
    assert all(len(s["sha256"]) == 64 for s in srcs if not s["name"].startswith("neovim"))


def test_cache_names_differ_for_same_file_name():
    a = bb.cache_name("https://github.com/neovim/neovim/releases/download/v0.12.5/nvim-linux-x86_64.tar.gz")
    b = bb.cache_name("https://github.com/neovim/neovim-releases/releases/download/v0.12.5/nvim-linux-x86_64.tar.gz")
    assert a != b and a.endswith("-nvim-linux-x86_64.tar.gz") and b.endswith("-nvim-linux-x86_64.tar.gz")
    assert bb.cache_name("https://x/y/cpython-3.12%2B1.tar.gz").endswith("-cpython-3.12+1.tar.gz")


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")


def test_trim_python_linux(tmp_path):
    keep = ["lib/python3.12/pipes.py", "lib/python3.12/json/__init__.py", "bin/python3.12",
            "lib/python3.12/site-packages/pyslang/__init__.py", "lib/python3.12/lib-dynload/_ssl.so"]
    drop = ["lib/python3.12/site-packages/pip/__init__.py", "lib/python3.12/site-packages/pip-25.0.dist-info/RECORD",
            "lib/python3.12/ensurepip/__init__.py", "lib/python3.12/tkinter/__init__.py",
            "lib/python3.12/lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so", "lib/libtcl9.0.so",
            "lib/tcl9.0/init.tcl", "lib/libpython3.12.so.1.0", "bin/pip3", "bin/idle3", "bin/python3.12-config",
            "include/python3.12/Python.h", "share/man/man1/python3.1"]
    for rel in keep + drop:
        _touch(tmp_path, rel)
    bb.trim_python(tmp_path, "linux")
    assert all((tmp_path / rel).exists() for rel in keep)
    assert not any((tmp_path / rel).exists() for rel in drop)


def test_trim_python_windows(tmp_path):
    keep = ["Lib/pipes.py", "DLLs/_ssl.pyd", "DLLs/libffi-8.dll", "python.exe", "Lib/site-packages/README.txt"]
    drop = ["Lib/site-packages/pip/__init__.py", "Lib/ensurepip/__init__.py", "Lib/idlelib/run.py", "tcl/tcl8.6/init.tcl",
            "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "DLLs/_testcapi.pyd", "include/Python.h",
            "libs/python312.lib"]
    for rel in keep + drop:
        _touch(tmp_path, rel)
    bb.trim_python(tmp_path, "windows")
    assert all((tmp_path / rel).exists() for rel in keep)
    assert not any((tmp_path / rel).exists() for rel in drop)


def test_notices_and_sources_name_every_component():
    pins = bb._toml(bb.VERSIONS)
    notes = bb.notices_text(pins, "linux-x86_64", True, " (built for glibc 2.17)")
    for needle in ("GPL-3.0-or-later", "prepro", "Neovim", "Neovide", "pyslang 11.0.0 (built for glibc 2.17)",
                   "LICENSES/python/", "openssl-3", "xdiff, unibilium"):
        assert needle in notes, needle
    used = [("python 3.12.13", "https://example/python.tar.gz", "0" * 64)]
    src = bb.sources_text(pins, "linux-x86_64", used)
    assert "https://example/python.tar.gz" in src and "unibilium" in src
    assert "libiconv" not in src                         # Windows-only part
    assert "libiconv" in bb.sources_text(pins, "windows-x86_64", used)


def test_launcher_script_follows_symlinks():
    sh = bb.LAUNCH_SH.format(mod="pyverilog_auto.gui.launch")
    assert 'readlink -f "$0"' in sh and '"$here/runtime/python/bin/python3" -I -m' in sh


def test_package_contents(tmp_path):
    stage = tmp_path / "rtl-integ-gui-9.9.9-linux-x86_64"
    _touch(stage, "rtl-integ-gui")
    _touch(stage, "runtime/python/bin/python3.12")
    _touch(stage, "LICENSES/neovim-LICENSE.txt")
    if os.name != "nt":
        os.chmod(stage / "rtl-integ-gui", 0o755)
        os.chmod(stage / "runtime/python/bin/python3.12", 0o755)
        os.symlink("python3.12", stage / "runtime/python/bin/python3")
    items = {i["dst"]: i for i in pl.contents(stage)}
    assert items["/opt/rtl-integ-gui"]["type"] == "dir"
    assert items["/usr/bin/rtl-integ-gui"] == {"src": "/opt/rtl-integ-gui/rtl-integ-gui",
                                               "dst": "/usr/bin/rtl-integ-gui", "type": "symlink"}
    assert items["/opt/rtl-integ-gui/LICENSES/neovim-LICENSE.txt"]["file_info"]["mode"] == 0o644
    if os.name != "nt":
        assert items["/opt/rtl-integ-gui/rtl-integ-gui"]["file_info"]["mode"] == 0o755
        assert items["/opt/rtl-integ-gui/runtime/python/bin/python3"] == {
            "src": "python3.12", "dst": "/opt/rtl-integ-gui/runtime/python/bin/python3", "type": "symlink"}
    cfg = pl.config(stage, "9.9.9")
    assert cfg["version"] == "9.9.9" and cfg["overrides"]["deb"]["recommends"] == ["perl"]
    assert "depends" not in cfg                          # one rpm for EL7 .. current


@pytest.mark.parametrize("target", sorted(bb.TARGETS))
def test_vendored_licenses_present(target):
    for name in ("neovim-LICENSE.txt", "neovide-LICENSE.txt"):
        assert (bb.LICENSES / name).stat().st_size > 500
    texts = {p.name for p in (bb.LICENSES / "python").iterdir()}
    assert {"LICENSE.cpython.txt", "LICENSE.openssl-3.txt", "LICENSE.libffi.txt", "LICENSE.mpdecimal.txt"} <= texts
