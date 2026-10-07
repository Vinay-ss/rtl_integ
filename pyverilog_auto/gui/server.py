"""JSON-lines RPC over stdio for the Neovim front end.

Requests:      {"id": 1, "method": "tree", "params": {...}}
Responses:     {"id": 1, "result": ...}  or  {"id": 1, "error": {"code": ..., "message": ...}}
Notifications: {"method": "log", "params": {"text": ..., "level": ...}}   (server -> client)

Standard output carries only protocol lines; anything else printed while a
request runs is redirected to standard error.

    python -m pyverilog_auto.gui.server
"""

from __future__ import annotations

import io
import json
import sys
import traceback
from typing import Any, BinaryIO, Optional

from .project import ProjectError
from .session import Session, SessionError

PROTOCOL_VERSION = 1


class Server:
    def __init__(self, out: BinaryIO):
        self._out = out
        self.session = Session(notify=self.notify)
        self.running = True
        self._methods = {
            "hello": self.hello,
            "open": self.session.open,
            "build": self.session.build,
            "summary": self.session.summary,
            "tree": self.session.tree,
            "node_info": self.session.node_info,
            "locate": self.session.locate,
            "diagnostics": self.session.diagnostics,
            "console_cmd": self.session.console_cmd,
            "shutdown": self.shutdown,
        }

    def register(self, name: str, fn) -> None:
        self._methods[name] = fn

    # -- output -------------------------------------------------------------

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self._out.write(data)
        self._out.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    # -- built-in methods -----------------------------------------------------

    def hello(self) -> dict:
        return {"protocol": PROTOCOL_VERSION, "server": "pyverilog_auto.gui", "python": sys.version.split()[0],
                "methods": sorted(self._methods)}

    def shutdown(self) -> dict:
        self.running = False
        return {}

    # -- dispatch -------------------------------------------------------------

    def handle_line(self, line: bytes) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send({"id": None, "error": {"code": "E_PARSE", "message": str(exc)}})
            return
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        fn = self._methods.get(method)
        if fn is None:
            self._send({"id": mid, "error": {"code": "E_METHOD", "message": f"unknown method {method!r}"}})
            return
        try:
            result: Any = fn(**params) if isinstance(params, dict) else fn(*params)
        except (SessionError, ProjectError) as exc:
            code = getattr(exc, "code", "E_PROJECT")
            self._send({"id": mid, "error": {"code": code, "message": str(exc)}})
            return
        except TypeError as exc:
            self._send({"id": mid, "error": {"code": "E_PARAMS", "message": str(exc)}})
            return
        except Exception as exc:  # pragma: no cover - reported to the client
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            self._send({"id": mid, "error": {"code": "E_INTERNAL", "message": f"{type(exc).__name__}: {exc}",
                                             "trace": tb}})
            return
        if mid is not None:
            self._send({"id": mid, "result": result})

    def serve(self, inp: BinaryIO) -> None:
        while self.running:
            line = inp.readline()
            if not line:
                break
            self.handle_line(line)


def main(argv: Optional[list[str]] = None) -> int:
    out = sys.stdout.buffer
    # Keep library prints off the protocol channel.
    sys.stdout = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    server = Server(out)
    try:
        from .ops import install as install_ops
    except ImportError:  # pragma: no cover - ops layer not present
        install_ops = None
    if install_ops is not None:
        install_ops(server)
    server.serve(sys.stdin.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
