"""Design integration: filelists, hierarchy, leaf-first AUTO expansion, routing.

The package works without pyslang (text backend) for filelist parsing,
hierarchy mapping and ordered expansion; the routing API and precise
instance paths through generate blocks need the pyslang backend
(``pip install pyverilog-auto[integ]``).
"""

from __future__ import annotations

from .filelist import (
    DEFAULT_LIBEXTS,
    Filelist,
    FilelistEntry,
    filelist_to_config,
    parse_filelist,
    parse_flags,
    tokenize_filelist_text,
)


def is_slang_available() -> bool:
    """True when pyslang 11 can be imported (and is not disabled via env)."""
    import os

    if os.environ.get("PYVERILOG_AUTO_NO_SLANG", "").strip() not in ("", "0", "false", "no"):
        return False
    try:
        import pyslang  # noqa: F401
        import pyslang.ast  # noqa: F401
        import pyslang.syntax  # noqa: F401
    except Exception:  # pragma: no cover - depends on environment
        return False
    return True


__all__ = [
    "DEFAULT_LIBEXTS",
    "Filelist",
    "FilelistEntry",
    "filelist_to_config",
    "parse_filelist",
    "parse_flags",
    "tokenize_filelist_text",
    "is_slang_available",
]
