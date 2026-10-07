#!/usr/bin/env python
"""Pack the sources of the LGPL parts the bundles redistribute in binary form.

    python tools/bundle/third_party_sources.py [--out dist]
    # -> dist/rtl-integ-gui-<ver>-third-party-sources.tar.gz

The archive list ([[third_party_source]] in versions.toml) is attached to
every release next to the bundles, so the corresponding source is offered
from the same place as the binaries.
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bundle as bb  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=bb.ROOT / "dist")
    ap.add_argument("--cache", type=Path, default=bb.ROOT / "build" / "bundle-cache")
    ap.add_argument("--offline", action="store_true")
    ns = ap.parse_args(argv)
    pins = bb._toml(bb.VERSIONS)
    version = bb.version_of_package()
    name = f"rtl-integ-gui-{version}-third-party-sources"
    ns.out.mkdir(parents=True, exist_ok=True)
    dst = ns.out / f"{name}.tar.gz"
    index = [f"Sources of the LGPL parts in the rtl-integ-gui {version} bundles", ""]
    with tarfile.open(dst, "w:gz") as t:
        for s in pins.get("third_party_source", []):
            path, digest = bb.fetch(s["url"], ns.cache, s.get("sha256", ""), offline=ns.offline)
            base = path.name.split("-", 1)[1]                 # without the cache prefix
            project = s["name"].split()[0]
            arcname = f"{name}/{base if base.startswith(project) else project + '-' + base}"
            t.add(path, arcname=arcname)
            targets = ", ".join(s.get("targets", sorted(bb.TARGETS)))
            index += [s["name"], f"  file:    {arcname.split('/', 1)[1]}", f"  from:    {s['url']}",
                      f"  sha256:  {digest}", f"  used in: {targets}", ""]
        data = ("\n".join(index) + "\n").encode("utf-8")
        info = tarfile.TarInfo(f"{name}/README.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    print(f"sources: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
