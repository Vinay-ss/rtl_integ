"""Declaration alignment engine for Verilog buffers.

Ported from ``verilog-pretty-declarations`` in ``verilog-mode.el``
(lines ~7565-7698).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


# Pattern matching declaration lines (input, output, wire, reg, logic, etc.)
_DECL_RE = re.compile(
    r"^(\s*)"                              # leading whitespace
    r"((?:input|output|inout|wire|reg|logic|bit|integer|real|realtime|time"
    r"|tri|tri0|tri1|triand|trior|trireg|wand|wor|supply0|supply1"
    r"|signed|unsigned|genvar|parameter|localparam)"
    r"(?:\s+(?:wire|reg|logic|signed|unsigned))*)"  # type keywords
    r"(\s*)"                               # space after type
    r"(\[[^\]]*\])?"                       # optional range [N:M]
    r"(\s*)"                               # space after range
    r"(\S+)"                               # signal name
    r"(.*)"                                # rest (including ;)
)

# Simpler pattern for detecting if a line is a declaration
_IS_DECL_RE = re.compile(
    r"^\s*(?:input|output|inout|wire|reg|logic|bit|integer|real|realtime|time"
    r"|tri|tri0|tri1|triand|trior|trireg|wand|wor|supply0|supply1"
    r"|signed|unsigned|genvar|parameter|localparam)\b"
)


class AlignEngine:
    """Align Verilog declarations to consistent columns.

    Port of ``verilog-pretty-declarations``.
    """

    def __init__(self, config: "VerilogConfig") -> None:
        self.config = config

    def align_declarations(
        self,
        buf: "VerilogBuffer",
        beg: Optional[int] = None,
        end: Optional[int] = None,
    ) -> None:
        """Align signal declarations in the specified region.

        If *beg*/*end* are None, aligns the entire buffer.
        """
        text = buf.buffer_string()
        lines = text.split("\n")

        start_line = 0
        end_line = len(lines)

        if beg is not None:
            start_line = text[:beg].count("\n")
        if end is not None:
            end_line = text[:end].count("\n") + 1

        # Find groups of consecutive declaration lines
        groups: list[list[int]] = []
        current_group: list[int] = []

        for i in range(start_line, min(end_line, len(lines))):
            if _IS_DECL_RE.match(lines[i]):
                current_group.append(i)
            else:
                if current_group:
                    groups.append(current_group)
                    current_group = []
        if current_group:
            groups.append(current_group)

        if not groups:
            return

        # Process each group
        for group in groups:
            self._align_group(lines, group)

        # Rebuild buffer
        new_text = "\n".join(lines)
        buf.delete_region(0, buf.point_max())
        buf.goto_char(0)
        buf.insert(new_text)

    def _align_group(self, lines: list[str], group: list[int]) -> None:
        """Align a group of consecutive declaration lines."""
        parsed = []
        for idx in group:
            m = _DECL_RE.match(lines[idx])
            if m:
                parsed.append((idx, m))
            else:
                parsed.append((idx, None))

        if not parsed:
            return

        # Find maximum widths for alignment
        max_type_width = 0
        max_range_width = 0

        for idx, m in parsed:
            if m is None:
                continue
            type_kw = m.group(2)
            range_part = m.group(4) or ""
            max_type_width = max(max_type_width, len(type_kw))
            max_range_width = max(max_range_width, len(range_part))

        # Rebuild each line with aligned columns
        for idx, m in parsed:
            if m is None:
                continue
            indent = m.group(1)
            type_kw = m.group(2)
            range_part = m.group(4) or ""
            name = m.group(6)
            rest = m.group(7)

            # Pad type keyword
            padded_type = type_kw.ljust(max_type_width)

            # Pad range
            if max_range_width > 0:
                padded_range = range_part.ljust(max_range_width)
                lines[idx] = f"{indent}{padded_type} {padded_range} {name}{rest}"
            else:
                lines[idx] = f"{indent}{padded_type} {name}{rest}"

    def align_assignments(
        self,
        buf: "VerilogBuffer",
        beg: Optional[int] = None,
        end: Optional[int] = None,
    ) -> None:
        """Align assign/parameter statements on ``=``."""
        text = buf.buffer_string()
        lines = text.split("\n")

        start_line = 0
        end_line = len(lines)

        if beg is not None:
            start_line = text[:beg].count("\n")
        if end is not None:
            end_line = text[:end].count("\n") + 1

        # Find groups of consecutive lines with = (not <= or ==)
        assign_re = re.compile(r"^([^=]*?)(\s*)(=)(\s*)(.*)")
        groups: list[list[int]] = []
        current_group: list[int] = []

        for i in range(start_line, min(end_line, len(lines))):
            line = lines[i]
            # Skip lines with <=, ==, !=, >=, =>
            if re.search(r"[<>!=]=|=>", line):
                if current_group:
                    groups.append(current_group)
                    current_group = []
                continue
            m = assign_re.match(line)
            if m and '=' in line:
                current_group.append(i)
            else:
                if current_group:
                    groups.append(current_group)
                    current_group = []
        if current_group:
            groups.append(current_group)

        if not groups:
            return

        for group in groups:
            if len(group) < 2:
                continue
            max_lhs = 0
            for idx in group:
                m = assign_re.match(lines[idx])
                if m:
                    max_lhs = max(max_lhs, len(m.group(1).rstrip()))
            for idx in group:
                m = assign_re.match(lines[idx])
                if m:
                    lhs = m.group(1).rstrip()
                    rhs = m.group(5)
                    lines[idx] = f"{lhs.ljust(max_lhs)} = {rhs}"

        new_text = "\n".join(lines)
        buf.delete_region(0, buf.point_max())
        buf.goto_char(0)
        buf.insert(new_text)
