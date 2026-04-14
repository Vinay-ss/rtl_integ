"""Tests for pyverilog_auto.scanner."""

import pytest

from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.scanner import CommentRegion, Scanner


class TestScanner:
    def _scan(self, text: str) -> list[CommentRegion]:
        buf = VerilogBuffer.from_string(text)
        return Scanner().scan(buf)

    def test_line_comment(self):
        regions = self._scan("code // comment\nmore")
        assert len(regions) == 1
        r = regions[0]
        assert r.kind == "line_comment"
        # The "//" itself is NOT inside — region starts at +1
        assert r.start == 6  # position after first '/'
        assert r.end == 16  # past the newline

    def test_block_comment(self):
        regions = self._scan("code /* block */ more")
        assert len(regions) == 1
        r = regions[0]
        assert r.kind == "block_comment"
        assert r.start == 6
        assert r.end == 16  # past "*/"

    def test_string_literal(self):
        regions = self._scan('code "hello" more')
        assert len(regions) == 1
        r = regions[0]
        assert r.kind == "string"
        assert r.start == 6
        assert r.end == 12

    def test_string_with_escape(self):
        regions = self._scan(r'code "he\"llo" more')
        assert len(regions) == 1
        r = regions[0]
        assert r.kind == "string"

    def test_attribute(self):
        regions = self._scan("(* synthesis *) module")
        assert len(regions) == 1
        r = regions[0]
        assert r.kind == "attribute"

    def test_multiple_regions(self):
        text = '// comment\nmodule foo; /* block */ wire "str";\n'
        regions = self._scan(text)
        kinds = [r.kind for r in regions]
        assert "line_comment" in kinds
        assert "block_comment" in kinds
        assert "string" in kinds

    def test_no_regions(self):
        regions = self._scan("module foo; wire clk; endmodule")
        assert regions == []


class TestScannerHelpers:
    def test_is_in_comment(self):
        buf = VerilogBuffer.from_string("code // comment\nmore")
        scanner = Scanner()
        regions = scanner.scan(buf)
        assert scanner.is_in_comment(8, regions)  # inside comment
        assert not scanner.is_in_comment(0, regions)  # before comment

    def test_is_in_string(self):
        buf = VerilogBuffer.from_string('code "hello" more')
        scanner = Scanner()
        regions = scanner.scan(buf)
        assert scanner.is_in_string(7, regions)  # inside string
        assert not scanner.is_in_string(0, regions)

    def test_next_non_comment(self):
        buf = VerilogBuffer.from_string("code // comment\nmore")
        scanner = Scanner()
        regions = scanner.scan(buf)
        buf.goto_char(8)  # inside comment
        pos = scanner.next_non_comment(buf, regions)
        assert pos == 16  # past the newline (end of line_comment region)


class TestBufferScanRegions:
    def test_cached(self):
        buf = VerilogBuffer.from_string("// comment\ncode")
        r1 = buf.scan_regions()
        r2 = buf.scan_regions()
        assert r1 is r2  # same object — cached

    def test_invalidated_on_insert(self):
        buf = VerilogBuffer.from_string("// comment\ncode")
        r1 = buf.scan_regions()
        buf.goto_char(11)
        buf.insert(" extra")
        r2 = buf.scan_regions()
        assert r1 is not r2  # cache was invalidated
