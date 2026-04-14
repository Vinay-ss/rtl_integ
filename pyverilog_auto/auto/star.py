"""AutoStar — expand SystemVerilog ``.*`` pins.

Ported from ``verilog-auto-star`` (lines 12451–12468) of ``verilog-mode.el``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig
    from ..library.module_db import ModuleDatabase


class AutoStar:
    """Expand ``.*`` in module instantiations.

    If ``auto_star_expand`` is set, ``.*`` is treated like ``AUTOINST``,
    otherwise it is ignored.

    Port of ``verilog-auto-star``.
    """

    def __init__(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig",
        db: "ModuleDatabase",
    ) -> None:
        self._buf = buf
        self._config = config
        self._db = db

    def expand(self) -> None:
        """Expand ``.*`` at current point if safe and enabled.

        Port of ``verilog-auto-star``: when ``auto_star_expand`` is set
        and the ``.*`` is in a safe position (last in pin list), treat
        it as AUTOINST.
        """
        if not self._auto_star_safe():
            return

        from .inst import AutoInst
        AutoInst(self._buf, self._config, self._db).expand()

    def delete(self) -> None:
        """Remove ``.*`` expansion (delete-auto-star-all).

        Port of ``verilog-delete-auto-star-implicit``.
        """
        from .delete import AutoDeleter
        deleter = AutoDeleter(self._buf, self._config)
        deleter.delete_auto_star()

    # ------------------------------------------------------------------
    # Safety check
    # ------------------------------------------------------------------

    def _auto_star_safe(self) -> bool:
        """Check if the ``.*`` at point is safe to expand.

        Returns True if ``auto_star_expand`` is enabled and the ``.*``
        is at the end of the pin list (before the closing paren).

        Port of ``verilog-auto-star-safe``.
        """
        if not self._config.auto_star_expand:
            return False

        text = self._buf.buffer_string()
        pt = self._buf.point()

        # Check context: .* should be preceded by . and followed by
        # whitespace/commas then ) or // section comment
        # The .* must be the last item in the pin list
        rest = text[pt:]
        m = re.match(r'[ \t\n\f,]*(?:\)|//)', rest)
        if not m:
            return False

        # Make sure we're inside an instantiation (not in a string or comment)
        # Check that preceding text has a . before the *
        if pt >= 2 and text[pt - 2] == ".":
            return True

        return False
