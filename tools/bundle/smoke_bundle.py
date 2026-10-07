#!/usr/bin/env python
"""Check a bundle directory on the machine it was built for.

    python tools/bundle/smoke_bundle.py dist/rtl-integ-gui-<ver>-windows-x86_64

Runs the bundle's own launcher with ``--selftest`` in a stripped environment
(no virtualenv, no Python or Neovim on PATH, no RTL_INTEG_* variables), so the
check passes only if everything comes from the bundle.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def clean_env() -> dict[str, str]:
    keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA",
            "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    if os.name == "nt":
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["PATH"] = os.pathsep.join([os.path.join(root, "System32"), root])
    else:
        env["PATH"] = "/usr/bin:/bin"
    return env


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    bundle = Path(argv[0]).resolve()
    launcher = bundle / ("rtl-integ-gui.cmd" if os.name == "nt" else "rtl-integ-gui")
    if not launcher.exists():
        print(f"no launcher in {bundle}")
        return 2
    cmd = ["cmd", "/c", str(launcher), "--selftest"] if os.name == "nt" else [str(launcher), "--selftest"]
    with tempfile.TemporaryDirectory(prefix="bundle_smoke_") as cwd:   # nothing importable from here
        proc = subprocess.run(cmd, env=clean_env(), cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=1200)
    # from the source checkout (worst case: a pyverilog_auto package in the cwd) the
    # launcher must still load the bundled copy
    repo = Path(__file__).resolve().parent.parent.parent
    where = subprocess.run(cmd[:-1] + ["--print-command"], env=clean_env(), cwd=str(repo),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    launch = where.stdout.decode("utf-8", "replace")
    if str(bundle) not in launch.split("-u", 1)[-1]:
        print(f"bundle selftest: FAIL (plugin not loaded from the bundle: {launch.strip()})")
        return 1
    sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
    if proc.returncode != 0:
        sys.stdout.write(proc.stderr.decode("utf-8", "replace")[-3000:])
    print(f"bundle selftest: {'PASS' if proc.returncode == 0 else 'FAIL'}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
