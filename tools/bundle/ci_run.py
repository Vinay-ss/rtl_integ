#!/usr/bin/env python
"""Run a command in CI; if it fails, repeat the end of its output as an error
annotation (annotations are readable without signing in, logs are not).

    python tools/bundle/ci_run.py [--tail N] -- COMMAND [ARG]...
"""

from __future__ import annotations

import collections
import re
import shutil
import subprocess
import sys


ERROR_LINE = re.compile(r"\berror\b|^FAILED|^E\s|Traceback|Error:", re.IGNORECASE)
LIMIT = 3500        # annotations are cut off at a few KB


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def clip(lines: list[str], width: int = 240) -> str:
    out, size = [], 0
    for line in lines:
        line = line if len(line) <= width else line[:width] + " ..."
        if size + len(line) > LIMIT:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


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
    errors: list[str] = []          # first error lines, each with the two lines after it
    pending = 0
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace")
        sys.stdout.write(line)
        sys.stdout.flush()
        text = line.rstrip("\r\n")
        tail.append(text)
        if len(errors) < 60:
            if ERROR_LINE.search(text):
                errors.append(text)
                pending = 2
            elif pending:
                errors.append(text)
                pending -= 1
    code = proc.wait()
    if code:
        cmd = " ".join(argv)
        head = f"$ {cmd[:300]}\n(exit {code})\n"
        if errors:
            print(f"::error title=first errors::{escape(head + clip(errors))}", flush=True)
        print(f"::error title=last lines::{escape(head + clip(list(tail)[-40:]))}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
