#!/usr/bin/env python
"""Build a self-contained rtl-integ-gui bundle for Windows or Linux.

    python tools/bundle/build_bundle.py --target windows-x86_64   # -> dist/rtl-integ-gui-<ver>-windows-x86_64.zip
    python tools/bundle/build_bundle.py --target linux-x86_64     # -> dist/rtl-integ-gui-<ver>-linux-x86_64.tar.gz

The bundle holds a standalone CPython (python-build-standalone) with
pyverilog_auto and pyslang installed, portable Neovim, Neovide (optional), a
launcher and the licenses:

    rtl-integ-gui-<ver>-<target>/
      rtl-integ-gui(.cmd)   prepro3(.cmd)   README.txt   SOURCES.txt
      runtime/python/  runtime/nvim/  runtime/neovide/  runtime/versions.json
      LICENSES/

Components are pinned in versions.toml and checked by sha256; downloads are
cached in --cache (default: build/bundle-cache).  The Linux bundle runs on
glibc 2.17 and later when its pyslang wheel is built for glibc 2.17
(--pyslang-wheel, see build_pyslang_wheel.sh); with PyPI's wheel it needs
glibc 2.28.  A bundle for the other OS can be assembled too (nothing from it
is executed here, so its Python files are not precompiled).  Only Perl, for
``// pl`` templates, comes from the system.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VERSIONS = HERE / "versions.toml"
LICENSES = HERE / "licenses"
REPO_URL = "https://github.com/Vinay-ss/rtl_integ"

TARGETS = {"windows-x86_64": "windows", "linux-x86_64": "linux"}
HOST_OS = "windows" if os.name == "nt" else ("linux" if sys.platform.startswith("linux") else "")

# parts of the standalone Python the GUI never uses (pip and its vendored
# packages, Tcl/Tk, IDLE, headers); relative to runtime/python
TRIM = {
    "windows": ["Lib/site-packages/pip", "Lib/site-packages/pip-*", "Lib/ensurepip", "Lib/idlelib", "Lib/tkinter",
                "Lib/turtledemo", "Lib/turtle.py", "tcl", "DLLs/_tkinter.pyd", "DLLs/tcl*.dll", "DLLs/tk*.dll",
                "DLLs/_test*.pyd", "Scripts/pip*", "include", "libs"],
    "linux": ["lib/python3.*/site-packages/pip", "lib/python3.*/site-packages/pip-*", "lib/python3.*/ensurepip",
              "lib/python3.*/idlelib", "lib/python3.*/tkinter", "lib/python3.*/turtledemo",
              "lib/python3.*/turtle.py", "lib/python3.*/lib-dynload/_tkinter*", "lib/python3.*/lib-dynload/_test*",
              "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*", "lib/libtcl*", "lib/libtk*", "lib/pkgconfig",
              # bin/python3.12 is linked statically; libpython is only for embedding
              "lib/libpython3*",
              "bin/pip*", "bin/idle*", "bin/*-config", "bin/2to3*", "include", "share"],
}


def _toml(path: Path) -> dict:
    import tomllib

    with open(path, "rb") as fh:
        return tomllib.load(fh)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_name(url: str) -> str:
    """Cache file for *url*: its file name, prefixed with a hash of the URL
    (two projects may publish files of the same name)."""
    base = urllib.request.unquote(url.rsplit("/", 1)[-1])
    return f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}-{base}"


def fetch(url: str, cache: Path, expect: str = "", *, offline: bool = False) -> tuple[Path, str]:
    """Download *url* into *cache* (once); return (path, sha256)."""
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / cache_name(url)
    if not dst.exists():
        if offline:
            raise SystemExit(f"not in the cache (offline): {url}")
        print(f"  downloading {url}")
        tmp = dst.with_suffix(dst.suffix + ".part")
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as fh:
            shutil.copyfileobj(r, fh)
        tmp.replace(dst)
    digest = sha256(dst)
    if expect and digest != expect:
        raise SystemExit(f"sha256 mismatch for {url}: expected {expect}, got {digest}")
    return dst, digest


def update_pin(section: str, key: str, digest: str) -> None:
    text = VERSIONS.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    cur = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[(\w+)\]", line)
        if m:
            cur = m.group(1)
        elif cur == section and line.lstrip().startswith(f"{key} ="):
            lines[i] = re.sub(r'sha256 = "[0-9a-f]*"', f'sha256 = "{digest}"', line)
    VERSIONS.write_text("".join(lines), encoding="utf-8", newline="\n")


def pyslang_wheel(version: str, tag: str, cache: Path, offline: bool) -> Path:
    meta_path = cache / f"pyslang-{version}.json"
    if not meta_path.exists():
        if offline:
            raise SystemExit("pyslang metadata not cached (offline)")
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"https://pypi.org/pypi/pyslang/{version}/json") as r:
            meta_path.write_bytes(r.read())
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for f in meta["urls"]:
        if f["filename"] == f"pyslang-{version}-{tag}.whl":
            path, _digest = fetch(f["url"], cache, f["digests"]["sha256"], offline=offline)
            return path
    raise SystemExit(f"no pyslang {version} wheel for {tag}")


def build_own_wheel(cache: Path) -> Path:
    out = Path(tempfile.mkdtemp(prefix="rtl_integ_wheel_"))
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "build", "--wheel", "--out-dir", str(out), str(ROOT)]
    else:
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(ROOT)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    wheels = list(out.glob("pyverilog_auto-*.whl"))
    if not wheels:
        raise SystemExit("building the pyverilog_auto wheel failed")
    return wheels[0]


def install_wheel(wheel: Path, site: Path) -> None:
    """Unpack a wheel into site-packages (no scripts are needed in the bundle)."""
    with zipfile.ZipFile(wheel) as z:
        z.extractall(site)
    for info in site.glob("*.dist-info"):
        if info.name.split("-")[0].lower() in wheel.name.lower():
            (info / "INSTALLER").write_text("rtl-integ-gui bundle\n")


def extract(archive: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dst)
    else:
        with tarfile.open(archive) as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(dst, filter="data")
            else:  # pragma: no cover - Python < 3.12
                t.extractall(dst)


def single_child(d: Path) -> Path:
    kids = [p for p in d.iterdir()]
    return kids[0] if len(kids) == 1 and kids[0].is_dir() else d


def version_of_package() -> str:
    data = _toml(ROOT / "pyproject.toml")
    return data["project"]["version"]


def trim_python(py: Path, os_name: str) -> list[str]:
    removed = []
    for pattern in TRIM[os_name]:
        for p in sorted(py.glob(pattern)):
            removed.append(p.relative_to(py).as_posix())
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
    return removed


def python_exe(py: Path, os_name: str) -> Path:
    return py / "python.exe" if os_name == "windows" else py / "bin" / "python3"


def precompile(py: Path, os_name: str) -> bool:
    """Compile the bundle's Python files with its own interpreter, so a
    read-only install (/opt) starts fast.  Only possible on the target OS."""
    if HOST_OS != os_name:
        return False
    lib = py / "Lib" if os_name == "windows" else next((py / "lib").glob("python3.*"))
    subprocess.run([str(python_exe(py, os_name)), "-I", "-m", "compileall", "-q", "-j", "0", str(lib)],
                   check=True, stdout=subprocess.DEVNULL)
    return True


# -I: isolated mode -- the current directory and PYTHON* variables must not
# shadow the bundled packages (e.g. when started inside a source checkout)
LAUNCH_CMD = '@echo off\r\n"%~dp0runtime\\python\\python.exe" -I -m {mod} %*\r\n'
# readlink -f: packages put a symlink in /usr/bin; the bundle is where it points
LAUNCH_SH = ('#!/bin/sh\n'
             'self=$(readlink -f "$0" 2>/dev/null || echo "$0")\n'
             'here=$(cd "$(dirname "$self")" && pwd)\n'
             'exec "$here/runtime/python/bin/python3" -I -m {mod} "$@"\n')

README = """rtl-integ-gui {version} ({target}) -- self-contained bundle
{repo}

Start:
  {launcher} PROJECT_DIR            open a project (rtl_integ_project.toml)
  {launcher} -f design.f [--top T]  browse an existing filelist (view only)
  {launcher} --demo [prepro]        open a copy of the demo (backtick or // py templates)
  {launcher} --selftest             check this installation (headless)
  {launcher} --ui tui               terminal Neovim instead of the Neovide window
  {launcher} --version              versions, for bug reports
{ui_note}
Inside: tree on the left (<CR> module, i instantiation, t template/RTL, m mark,
W wrap, H hoist, U unroll, u undo, R rebuild, / filter, ? help), source on the
right, console at the bottom (<CR> in the console runs a command).

A project is a directory with rtl_integ_project.toml, for example:

  [project]
  top = "top"
  syntax = "backtick"          # template delimiters: ` CODE, [* .. *], `var`

  [[template]]
  src = "tpl/top.svp"          # generates build/gen/top.sv
  lang = "python"              # or "perl"

  [[source]]
  path = "rtl/stage.sv"        # plain RTL, used as it is

The demo (--demo) is a complete example; the user guide is
pyverilog_auto/gui/README.md in the source repository.

Template preprocessor:  {prepro} [prepro options] FILE   (Python 3 port of prepro)
Perl templates need a perl on PATH (Linux: system perl; Windows: Git for
Windows or Strawberry Perl).

Components: Python {python}, Neovim {neovim}, Neovide {neovide}, pyslang {pyslang}.
Licenses: LICENSES/ (start with THIRD-PARTY-NOTICES.txt); sources: SOURCES.txt.
"""

UI_NOTE = {
    "windows": "",
    "linux": "\nNeovide needs glibc 2.35+, a display and OpenGL; elsewhere (RHEL/CentOS 7-9,\n"
             "SSH sessions) the launcher starts Neovim in the terminal instead.\n",
}

PREPRO_NOTICE = """pyverilog_auto/prepro is a Python 3 port of prepro, the Perl/Python
preprocessor for text files, Copyright (c) 2006 Adrian Lewis
<indproj@yahoo.com>, distributed under the GNU General Public License
version 2 or (at your option) any later version.

The port (Copyright (c) 2026 Vinay S: rewritten for Python 3, Windows and
Linux; line maps, template constructs, variable capture and the backtick
template syntax added) is part of rtl_integ and distributed under the GNU
General Public License version 3 or later (rtl_integ-GPL-3.0.txt), as the
"any later version" clause of prepro's license permits.
"""


def make_executable(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def notices_text(pins: dict, target: str, neovide: bool, pyslang_note: str) -> str:
    rows = [
        ("rtl-integ-gui / pyverilog-auto", version_of_package(), "GPL-3.0-or-later", "rtl_integ-GPL-3.0.txt",
         REPO_URL),
        ("prepro (Python 3 port inside pyverilog-auto)", "2006 / 2026", "GPL-2.0-or-later, used under GPL-3.0",
         "prepro-NOTICE.txt", REPO_URL),
        ("Python (python-build-standalone)", pins["python"]["version"], pins["python"]["license"], "python/",
         pins["python"]["source"]),
        ("Neovim", pins["neovim"]["version"], pins["neovim"]["license"], "neovim-LICENSE.txt",
         pins["neovim"]["source"]),
        ("pyslang", pins["pyslang"]["version"] + pyslang_note, pins["pyslang"]["license"], "pyslang-LICENSE.txt",
         pins["pyslang"]["source"]),
    ]
    if neovide:
        rows.append(("Neovide", pins["neovide"]["version"], pins["neovide"]["license"], "neovide-LICENSE.txt",
                     pins["neovide"]["source"]))
    out = [f"Third-party notices for rtl-integ-gui {version_of_package()} ({target})", "",
           "This bundle is an aggregate of separate programs, each under its own license.", ""]
    for name, ver, lic, files, src in rows:
        out += [f"{name} {ver}", f"  license: {lic}", f"  text:    LICENSES/{files}", f"  source:  {src}", ""]
    libs = sorted(p.name for p in (LICENSES / "python").glob("LICENSE.*.txt"))
    out += ["Python links the libraries whose licenses are in LICENSES/python/:",
            "  " + ", ".join(n[len("LICENSE."):-len(".txt")] for n in libs), "",
            "Neovim includes third-party code listed at the end of neovim-LICENSE.txt,",
            "among it LGPL libraries (xdiff, unibilium; gettext and libiconv on Windows);",
            "their sources are listed in SOURCES.txt.", ""]
    return "\n".join(out)


def sources_text(pins: dict, target: str, used: list[tuple[str, str, str]]) -> str:
    out = [f"Sources of rtl-integ-gui {version_of_package()} ({target})", "",
           f"rtl-integ-gui / pyverilog-auto: {REPO_URL}/tree/v{version_of_package()}", "",
           "Binary components in this bundle (download URL, sha256):"]
    for name, url, digest in used:
        out.append(f"  {name}: {url}")
        out.append(f"    sha256 {digest}")
    out += ["", "Their source code:"]
    for sec in ("python", "neovim", "neovide", "pyslang"):
        out.append(f"  {sec} {pins[sec]['version']}: {pins[sec]['source']}")
    out += ["", "Sources of the LGPL parts redistributed in binary form (also attached to the",
            f"release as rtl-integ-gui-{version_of_package()}-third-party-sources.tar.gz):"]
    for s in pins.get("third_party_source", []):
        if target in s.get("targets", list(TARGETS)):
            out.append(f"  {s['name']}: {s['url']}")
    return "\n".join(out) + "\n"


def build(target: str, out_dir: Path, cache: Path, *, neovide: bool, update_pins: bool, offline: bool,
          wheel: Path | None, pyslang: Path | None, archive: bool) -> Path:
    os_name = TARGETS[target]
    pins = _toml(VERSIONS)
    version = version_of_package()
    name = f"rtl-integ-gui-{version}-{target}"
    stage = out_dir / name
    if stage.exists():
        shutil.rmtree(stage)
    runtime = stage / "runtime"
    runtime.mkdir(parents=True)
    lic = stage / "LICENSES"
    lic.mkdir()
    used: list[tuple[str, str, str]] = []

    def component(section: str) -> Path:
        spec = pins[section][target]
        path, digest = fetch(spec["url"], cache, spec.get("sha256", ""), offline=offline)
        if not spec.get("sha256"):
            if update_pins:
                update_pin(section, target, digest)
                print(f"  pinned {section}/{target} sha256 {digest}")
            else:
                print(f"  WARNING: {section}/{target} is not pinned (sha256 {digest}); use --update-pins")
        used.append((f"{section} {pins[section]['version']}", spec["url"], digest))
        return path

    print(f"[python {pins['python']['version']}]")
    tmp = Path(tempfile.mkdtemp(prefix="bundle_"))
    extract(component("python"), tmp / "py")
    shutil.move(str(single_child(tmp / "py")), runtime / "python")
    py = runtime / "python"
    site = (py / "Lib" / "site-packages") if os_name == "windows" else \
        next((py / "lib").glob("python3.*")) / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    removed = trim_python(py, os_name)
    print(f"  removed {len(removed)} unused parts (pip, Tcl/Tk, IDLE, headers)")
    shutil.copytree(LICENSES / "python", lic / "python")

    print(f"[neovim {pins['neovim']['version']}]")
    extract(component("neovim"), tmp / "nvim")
    shutil.move(str(single_child(tmp / "nvim")), runtime / "nvim")
    shutil.copy(LICENSES / "neovim-LICENSE.txt", lic / "neovim-LICENSE.txt")

    if neovide:
        print(f"[neovide {pins['neovide']['version']}]")
        extract(component("neovide"), tmp / "neovide")
        src = tmp / "neovide"
        (runtime / "neovide").mkdir()
        for exe in list(src.rglob("neovide.exe")) + list(src.rglob("neovide")):
            if exe.is_file():
                shutil.copy(exe, runtime / "neovide" / exe.name)
                make_executable(runtime / "neovide" / exe.name)
                break
        shutil.copy(LICENSES / "neovide-LICENSE.txt", lic / "neovide-LICENSE.txt")

    print(f"[pyslang {pins['pyslang']['version']}]")
    if pyslang is not None:
        slang_wheel = pyslang
        used.append((f"pyslang {pins['pyslang']['version']} (built from {pins['pyslang']['sdist']['url']})",
                     pyslang.name, sha256(pyslang)))
    else:
        slang_wheel = pyslang_wheel(pins["pyslang"]["version"], pins["pyslang"][target]["tag"], cache, offline)
        used.append((f"pyslang {pins['pyslang']['version']}", slang_wheel.name.split("-", 1)[1], sha256(slang_wheel)))
    install_wheel(slang_wheel, site)
    for dist in site.glob("pyslang-*.dist-info"):
        for cand in ("LICENSE", "licenses/LICENSE", "LICENSE.txt"):
            if (dist / cand).exists():
                shutil.copy(dist / cand, lic / "pyslang-LICENSE.txt")
                break

    print("[pyverilog_auto]")
    install_wheel(wheel or build_own_wheel(cache), site)
    shutil.copy(ROOT / "LICENSE", lic / "rtl_integ-GPL-3.0.txt")
    (lic / "prepro-NOTICE.txt").write_text(PREPRO_NOTICE, newline="\n")
    note = " (built for glibc 2.17)" if pyslang is not None else ""
    (lic / "THIRD-PARTY-NOTICES.txt").write_text(notices_text(pins, target, neovide, note), newline="\n")
    (stage / "SOURCES.txt").write_text(sources_text(pins, target, used), newline="\n")
    (runtime / "versions.json").write_text(json.dumps({
        "tool": version, "target": target,
        "components": {"python": pins["python"]["version"], "neovim": pins["neovim"]["version"],
                       "neovide": pins["neovide"]["version"] if neovide else "not included",
                       "pyslang": pins["pyslang"]["version"] + note},
    }, indent=1) + "\n", newline="\n")

    for tool, mod in (("rtl-integ-gui", "pyverilog_auto.gui.launch"), ("prepro3", "pyverilog_auto.prepro")):
        if os_name == "windows":
            (stage / f"{tool}.cmd").write_bytes(LAUNCH_CMD.format(mod=mod).encode())
        else:
            p = stage / tool
            p.write_text(LAUNCH_SH.format(mod=mod), newline="\n")
            make_executable(p)
    launcher = "rtl-integ-gui.cmd" if os_name == "windows" else "./rtl-integ-gui"
    (stage / "README.txt").write_text(README.format(
        version=version, target=target, repo=REPO_URL, launcher=launcher, ui_note=UI_NOTE[os_name],
        prepro="prepro3.cmd" if os_name == "windows" else "./prepro3",
        python=pins["python"]["version"], neovim=pins["neovim"]["version"],
        neovide=pins["neovide"]["version"] if neovide else "(not included)",
        pyslang=pins["pyslang"]["version"] + note),
        newline="\r\n" if os_name == "windows" else "\n")
    if precompile(py, os_name):
        print("  precompiled the Python files")
    else:
        print("  not precompiled (built on another OS)")
    shutil.rmtree(tmp, ignore_errors=True)

    if not archive:
        return stage
    if os_name == "windows":
        dst = out_dir / f"{name}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                z.write(p, p.relative_to(out_dir))
    else:
        dst = out_dir / f"{name}.tar.gz"
        with tarfile.open(dst, "w:gz") as t:
            def exec_bits(ti: tarfile.TarInfo) -> tarfile.TarInfo:
                # modes are set explicitly: a bundle assembled on Windows has none
                rel = ti.name.split("/", 1)[-1]
                executable = rel in ("rtl-integ-gui", "prepro3") or "/bin/" in rel or \
                    rel.startswith("runtime/neovide/") or rel.endswith(".so") or ".so." in rel
                ti.mode = 0o755 if ti.isdir() or (ti.isfile() and executable) else 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                return ti
            t.add(stage, arcname=name, filter=exec_bits)
    print(f"bundle: {dst}")
    return stage


def refresh_licenses(cache: Path, offline: bool) -> None:
    """Re-vendor licenses/python/ from the pinned 'full' Python archive."""
    spec = _toml(VERSIONS)["python"]["licenses"]
    path, _digest = fetch(spec["url"], cache, spec["sha256"], offline=offline)
    try:
        from compression import zstd  # Python 3.14+
        opener = zstd.open
    except ImportError:  # pragma: no cover - older Python
        import zstandard

        def opener(p, mode):
            return zstandard.ZstdDecompressor().stream_reader(open(p, "rb"))
    dst = LICENSES / "python"
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    with opener(path, "rb") as zf, tarfile.open(fileobj=zf, mode="r|") as t:
        for m in t:
            if m.isfile() and m.name.startswith("python/licenses/"):
                data = t.extractfile(m).read().replace(b"\r\n", b"\n")
                (dst / m.name.rsplit("/", 1)[-1]).write_bytes(data)
    print(f"licenses: {len(list(dst.iterdir()))} files in {dst}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    host = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
    ap.add_argument("--target", choices=sorted(TARGETS), default=host)
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--cache", type=Path, default=ROOT / "build" / "bundle-cache")
    ap.add_argument("--no-neovide", action="store_true", help="leave Neovide out (terminal Neovim only)")
    ap.add_argument("--update-pins", action="store_true", help="record sha256 of unpinned downloads")
    ap.add_argument("--offline", action="store_true", help="use the download cache only")
    ap.add_argument("--wheel", type=Path, help="pyverilog_auto wheel to install (default: build one)")
    ap.add_argument("--pyslang-wheel", type=Path, help="pyslang wheel to install (default: PyPI's for the target)")
    ap.add_argument("--no-archive", action="store_true", help="leave the bundle directory, no zip/tar.gz")
    ap.add_argument("--refresh-licenses", action="store_true",
                    help="re-vendor licenses/python from the pinned full Python archive, then stop")
    ns = ap.parse_args(argv)
    if ns.refresh_licenses:
        refresh_licenses(ns.cache, ns.offline)
        return 0
    ns.out.mkdir(parents=True, exist_ok=True)
    build(ns.target, ns.out, ns.cache, neovide=not ns.no_neovide, update_pins=ns.update_pins, offline=ns.offline,
          wheel=ns.wheel, pyslang=ns.pyslang_wheel, archive=not ns.no_archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
