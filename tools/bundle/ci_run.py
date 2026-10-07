#!/usr/bin/env python
"""Run a command in CI; if it fails, repeat the end of its output as an error
annotation (annotations are readable without signing in, logs are not).

    python tools/bundle/ci_run.py [--tail N] -- COMMAND [ARG]...
"""

from __future__ import annotations

import collections
import shutil
import subprocess
import sys


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    tail_n = 80
    if argv[:1] == ["--tail"]:
        tail_n, argv = int(argv[1]), argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print(__doc__)
        return 2
    tail: collections.deque[str] = collections.deque(maxlen=tail_n)
    exe = shutil.which(argv[0]) or argv[0]          # Windows: .exe / .cmd suffixes
    try:
        proc = subprocess.Popen([exe, *argv[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        print(f"::error title=command failed::{escape(f'$ {argv[0]}: {exc}')}", flush=True)
        return 127
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace")
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line.rstrip("\r\n"))
    code = proc.wait()
    if code:
        cmd = " ".join(argv)
        body = f"$ {cmd[:300]}\n(exit {code}; last {len(tail)} lines)\n" + "\n".join(tail)
        print(f"::error title=command failed::{escape(body)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
