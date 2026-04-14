"""pyverilog_auto — Verilog AUTO code generation as a standalone Python tool."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from .auto.engine import AutoEngine
from .buffer import VerilogBuffer
from .config import VerilogConfig

if TYPE_CHECKING:
    pass

__all__ = [
    "AutoEngine",
    "VerilogBuffer",
    "VerilogConfig",
    "auto_expand",
    "auto_delete",
    "auto_inject",
]


def auto_expand(filepath: str, config: Optional[VerilogConfig] = None) -> None:
    """Expand all AUTO markers in *filepath* (in-place)."""
    cfg = config or VerilogConfig()
    buf = VerilogBuffer.from_file(filepath)
    engine = AutoEngine(cfg)
    engine.run(buf, cfg)
    buf.write_to_file()


def auto_delete(filepath: str, config: Optional[VerilogConfig] = None) -> None:
    """Delete all AUTO-generated sections in *filepath* (in-place)."""
    from .auto.delete import AutoDeleter
    from .local_vars import apply_local_vars, parse_local_vars

    cfg = config or VerilogConfig()
    buf = VerilogBuffer.from_file(filepath)

    local_vars = parse_local_vars(buf)
    if local_vars:
        cfg = apply_local_vars(cfg, local_vars)

    deleter = AutoDeleter(buf, cfg)
    deleter.delete()
    buf.write_to_file()


def auto_inject(filepath: str, config: Optional[VerilogConfig] = None) -> None:
    """Add AUTO markers to *filepath* (in-place)."""
    cfg = config or VerilogConfig()
    buf = VerilogBuffer.from_file(filepath)
    engine = AutoEngine(cfg)
    engine.run(buf, cfg, inject=True)
    buf.write_to_file()
