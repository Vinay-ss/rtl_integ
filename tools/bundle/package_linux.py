#!/usr/bin/env python
"""Package a Linux bundle directory as .rpm and .deb (with nfpm).

    python tools/bundle/package_linux.py dist/rtl-integ-gui-<ver>-linux-x86_64 [--out dist] [--nfpm PATH]

The packages install the bundle unchanged under /opt/rtl-integ-gui and put
``rtl-integ-gui`` and ``prepro3`` on PATH (symlinks in /usr/bin; the
launchers resolve them).  They declare no dependencies, so one rpm serves
CentOS/RHEL 7 to current Fedora/SUSE; Perl, needed only for Perl
templates, is a recommendation of the deb.  Run on Linux, where the bundle's
file modes and symlinks are real.  Without --nfpm, an nfpm on PATH is used,
else the version pinned in versions.toml is downloaded (sha256-checked).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bundle as bb  # noqa: E402

PREFIX = "/opt/rtl-integ-gui"
MAINTAINER = "Vinay S <vinay.arthes16@icloud.com>"
HOMEPAGE = "https://github.com/Vinay-ss/rtl_integ"
DESCRIPTION = ("Neovim-based GUI to browse and restructure template-generated RTL "
               "(wrap/hoist instances in prepro templates); self-contained.")
LICENSE = "GPL-3.0-or-later AND Apache-2.0 AND Vim AND MIT AND PSF-2.0 AND LGPL-3.0-or-later AND LGPL-2.1-or-later"


def contents(stage: Path) -> list[dict]:
    """nfpm content entries for every file and symlink of *stage*."""
    items: list[dict] = [{"dst": PREFIX, "type": "dir", "file_info": {"mode": 0o755}}]
    for dirpath, dirnames, filenames in os.walk(stage):
        d = Path(dirpath)
        for name in sorted(dirnames):
            p = d / name
            dst = f"{PREFIX}/{p.relative_to(stage).as_posix()}"
            if p.is_symlink():
                items.append({"src": os.readlink(p), "dst": dst, "type": "symlink"})
            else:
                items.append({"dst": dst, "type": "dir", "file_info": {"mode": 0o755}})
        dirnames[:] = [n for n in dirnames if not (d / n).is_symlink()]
        for name in sorted(filenames):
            p = d / name
            dst = f"{PREFIX}/{p.relative_to(stage).as_posix()}"
            if p.is_symlink():
                items.append({"src": os.readlink(p), "dst": dst, "type": "symlink"})
            else:
                mode = 0o755 if p.stat().st_mode & 0o111 else 0o644
                items.append({"src": str(p), "dst": dst, "file_info": {"mode": mode}})
    for tool in ("rtl-integ-gui", "prepro3"):
        items.append({"src": f"{PREFIX}/{tool}", "dst": f"/usr/bin/{tool}", "type": "symlink"})
    return items


def config(stage: Path, version: str) -> dict:
    return {
        "name": "rtl-integ-gui",
        "arch": "amd64",
        "platform": "linux",
        "version": version,
        "release": "1",
        "section": "devel",
        "priority": "optional",
        "maintainer": MAINTAINER,
        "description": DESCRIPTION,
        "vendor": "rtl_integ",
        "homepage": HOMEPAGE,
        "license": LICENSE,
        "contents": contents(stage),
        "overrides": {"deb": {"recommends": ["perl"]}},
    }


def find_nfpm(explicit: str | None, cache: Path) -> str:
    if explicit:
        return explicit
    found = shutil.which("nfpm")
    if found:
        return found
    spec = bb._toml(bb.VERSIONS)["nfpm"]["linux-x86_64"]
    archive, _digest = bb.fetch(spec["url"], cache, spec["sha256"])
    dst = cache / "nfpm"
    bb.extract(archive, dst)
    exe = dst / "nfpm"
    bb.make_executable(exe)
    return str(exe)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", type=Path, help="bundle directory (rtl-integ-gui-<ver>-linux-x86_64)")
    ap.add_argument("--out", type=Path, default=Path("dist"))
    ap.add_argument("--nfpm", help="nfpm executable (default: PATH, else the pinned download)")
    ap.add_argument("--cache", type=Path, default=bb.ROOT / "build" / "bundle-cache")
    ns = ap.parse_args(argv)
    stage = ns.stage.resolve()
    info = json.loads((stage / "runtime" / "versions.json").read_text(encoding="utf-8"))
    version = info["tool"]
    nfpm = find_nfpm(ns.nfpm, ns.cache)
    ns.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "nfpm.json"            # nfpm reads YAML, and JSON is YAML
        cfg.write_text(json.dumps(config(stage, version), indent=1), encoding="utf-8")
        for packager in ("rpm", "deb"):
            subprocess.run([nfpm, "package", "--config", str(cfg), "--packager", packager,
                            "--target", str(ns.out)], check=True)
    for p in sorted(ns.out.glob("rtl-integ-gui*" + version + "*")):
        if p.suffix in (".rpm", ".deb"):
            print(f"package: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
