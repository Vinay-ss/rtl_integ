#!/usr/bin/env python
"""Download and unpack the pinned Neovim for this OS; print the nvim path.

    python tools/bundle/fetch_nvim.py [--dest build/nvim]

Used by CI to run the headless GUI self-test (RTL_INTEG_NVIM) with the same
Neovim the bundles ship.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bundle as bb  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", type=Path, default=bb.ROOT / "build" / "nvim")
    ap.add_argument("--cache", type=Path, default=bb.ROOT / "build" / "bundle-cache")
    ns = ap.parse_args(argv)
    target = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
    spec = bb._toml(bb.VERSIONS)["neovim"][target]
    archive, _digest = bb.fetch(spec["url"], ns.cache, spec["sha256"])
    bb.extract(archive, ns.dest)
    exe = next(p for p in ns.dest.rglob("nvim.exe" if os.name == "nt" else "nvim")
               if p.is_file() and p.parent.name == "bin")
    if os.name != "nt":
        bb.make_executable(exe)
    print(exe.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
