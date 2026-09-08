"""Load route specifications from TOML or JSON files.

TOML (recommended; literal strings keep backslashes)::

    [[route]]
    name = "dma-axi"
    src  = 'top\\.u_cluster\\.u_core(\\d+)\\.u_dma:m_axi'
    dst  = 'top\\.u_mem\\.u_ctrl\\1:s_axi'
    iface_conn = { clk = "clk", rst_n = "rst_n" }

JSON: ``{"routes": [{"src": ..., "dst": ...}]}`` or a bare list.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .route import Diagnostic, RouteError, RouteSpec


def _load_toml(path: str) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            raise RouteError([Diagnostic(
                "E_SPEC", "error",
                "TOML routes files need Python >= 3.11 or the 'tomli' package "
                "(pip install pyverilog-auto[routes-py310]); JSON is also accepted",
            )]) from None
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_routes(path: str) -> list[RouteSpec]:
    """Read *path* (``.toml`` or ``.json``) into :class:`RouteSpec` objects."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
    elif ext == ".toml":
        data = _load_toml(path)
    else:
        raise RouteError([Diagnostic("E_SPEC", "error", f"{path}: routes file must be .toml or .json")])
    if isinstance(data, dict):
        entries = data.get("route", data.get("routes"))
        if entries is None:
            raise RouteError([Diagnostic("E_SPEC", "error", f"{path}: expected [[route]] tables or a 'routes' list")])
    else:
        entries = data
    if not isinstance(entries, list):
        raise RouteError([Diagnostic("E_SPEC", "error", f"{path}: routes must be a list")])
    specs: list[RouteSpec] = []
    for i, e in enumerate(entries, 1):
        if isinstance(e, str):
            specs.append(RouteSpec.parse(e))
        elif isinstance(e, dict):
            specs.append(RouteSpec.from_mapping(e, index=i))
        else:
            raise RouteError([Diagnostic("E_SPEC", "error", f"{path}: route #{i} must be a table or 'SRC -> DST' string")])
    return specs
