"""Tests for pyverilog_auto.buffer.VerilogBuffer."""

import pytest

from pyverilog_auto.buffer import VerilogBuffer


class TestConstruction:
    def test_from_string(self):
        buf = VerilogBuffer.from_string("hello world")
        assert buf.buffer_string() == "hello world"
        assert buf.point() == 0

    def test_point_min_max(self):
        buf = VerilogBuffer.from_string("abc")
        assert buf.point_min() == 0
        assert buf.point_max() == 3


class TestCursorMovement:
    def test_goto_char(self):
        buf = VerilogBuffer.from_string("abcdef")
        buf.goto_char(3)
        assert buf.point() == 3
        buf.goto_char(-1)
        assert buf.point() == 0
        buf.goto_char(100)
        assert buf.point() == 6

    def test_forward_backward_char(self):
        buf = VerilogBuffer.from_string("abcdef")
        buf.forward_char(3)
        assert buf.point() == 3
        buf.backward_char(1)
        assert buf.point() == 2

    def test_forward_line(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        moved = buf.forward_line(1)
        assert moved == 1
        assert buf.point() == 6  # start of "line2"
        moved = buf.forward_line(1)
        assert moved == 1
        assert buf.point() == 12  # start of "line3"

    def test_forward_line_negative(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        buf.goto_char(12)  # start of line3
        buf.forward_line(-1)
        assert buf.point() == 6  # start of line2

    def test_beginning_of_line(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        buf.goto_char(8)  # middle of line2
        buf.beginning_of_line()
        assert buf.point() == 6

    def test_end_of_line(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        buf.goto_char(6)  # start of line2
        buf.end_of_line()
        assert buf.point() == 11

    def test_at_start_end(self):
        buf = VerilogBuffer.from_string("abc")
        assert buf.at_start()
        assert not buf.at_end()
        buf.goto_char(3)
        assert buf.at_end()
        assert not buf.at_start()


class TestCharacterAccess:
    def test_following_preceding(self):
        buf = VerilogBuffer.from_string("abc")
        buf.goto_char(1)
        assert buf.following_char() == "b"
        assert buf.preceding_char() == "a"

    def test_char_after_before(self):
        buf = VerilogBuffer.from_string("abc")
        assert buf.char_after(0) == "a"
        assert buf.char_after(2) == "c"
        assert buf.char_before(1) == "a"
        assert buf.char_before(3) == "c"

    def test_boundary_chars(self):
        buf = VerilogBuffer.from_string("abc")
        assert buf.char_after(3) == ""  # past end
        assert buf.char_before(0) == ""  # before start
        buf.goto_char(3)
        assert buf.following_char() == ""
        buf.goto_char(0)
        assert buf.preceding_char() == ""


class TestTextInspection:
    def test_current_column(self):
        buf = VerilogBuffer.from_string("  hello\nworld")
        buf.goto_char(4)  # 'l' in hello
        assert buf.current_column() == 4

    def test_current_indentation(self):
        buf = VerilogBuffer.from_string("   hello\nworld")
        buf.goto_char(5)  # inside indentation area
        assert buf.current_indentation() == 3

    def test_line_beginning_position(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        buf.goto_char(8)  # middle of line2
        assert buf.line_beginning_position() == 6
        assert buf.line_beginning_position(2) == 12  # start of next line

    def test_line_end_position(self):
        buf = VerilogBuffer.from_string("line1\nline2\nline3")
        buf.goto_char(8)
        assert buf.line_end_position() == 11

    def test_buffer_substring(self):
        buf = VerilogBuffer.from_string("hello world")
        assert buf.buffer_substring(0, 5) == "hello"
        assert buf.buffer_substring(6, 11) == "world"


class TestPatternMatching:
    def test_looking_at(self):
        buf = VerilogBuffer.from_string("module foo")
        m = buf.looking_at(r"module")
        assert m is not None
        assert buf.match_string(0) == "module"

    def test_looking_at_emacs_group(self):
        buf = VerilogBuffer.from_string("module foo")
        m = buf.looking_at(r"\(module\)\s-+\(\sw+\)")
        assert m is not None
        assert buf.match_string(1) == "module"
        assert buf.match_string(2) == "foo"

    def test_re_search_forward(self):
        buf = VerilogBuffer.from_string("aaa bbb ccc")
        result = buf.re_search_forward(r"bbb")
        assert result is not None
        # Point should be at end of match
        assert buf.point() == 7
        assert buf.match_string(0) == "bbb"

    def test_re_search_forward_sets_last_match(self):
        buf = VerilogBuffer.from_string("foo bar baz")
        buf.re_search_forward(r"\(bar\)")
        assert buf.match_string(1) == "bar"
        assert buf.match_beginning(1) == 4
        assert buf.match_end(1) == 7

    def test_re_search_forward_no_match(self):
        buf = VerilogBuffer.from_string("foo bar")
        result = buf.re_search_forward(r"xyz")
        assert result is None

    def test_re_search_backward(self):
        buf = VerilogBuffer.from_string("aaa bbb ccc")
        buf.goto_char(11)  # end
        result = buf.re_search_backward(r"bbb")
        assert result is not None
        assert buf.point() == 4  # start of "bbb"
        assert buf.match_string(0) == "bbb"

    def test_looking_back(self):
        buf = VerilogBuffer.from_string("hello world")
        buf.goto_char(5)
        assert buf.looking_back(r"hello")
        assert not buf.looking_back(r"world")


class TestTextMutation:
    def test_insert(self):
        buf = VerilogBuffer.from_string("helloworld")
        buf.goto_char(5)
        buf.insert(" ")
        assert buf.buffer_string() == "hello world"
        assert buf.point() == 6  # point advances past insert

    def test_insert_shifts_positions(self):
        buf = VerilogBuffer.from_string("ab")
        buf.goto_char(1)
        buf.insert("X")
        assert buf.buffer_string() == "aXb"
        assert buf.point() == 2

    def test_delete_region(self):
        buf = VerilogBuffer.from_string("hello world")
        buf.goto_char(8)  # inside "world"
        buf.delete_region(5, 6)  # delete the space
        assert buf.buffer_string() == "helloworld"
        # point was at 8, should shift back by 1
        assert buf.point() == 7

    def test_delete_region_point_inside(self):
        buf = VerilogBuffer.from_string("hello world")
        buf.goto_char(7)  # inside deleted region
        buf.delete_region(5, 9)
        assert buf.buffer_string() == "hellold"
        assert buf.point() == 5

    def test_delete_char(self):
        buf = VerilogBuffer.from_string("abc")
        buf.goto_char(1)
        buf.delete_char(1)
        assert buf.buffer_string() == "ac"

    def test_replace_match(self):
        buf = VerilogBuffer.from_string("foo bar baz")
        buf.re_search_forward(r"bar")
        buf.replace_match("qux")
        assert buf.buffer_string() == "foo qux baz"


class TestSaveExcursion:
    def test_restores_point(self):
        buf = VerilogBuffer.from_string("hello world")
        buf.goto_char(3)
        with buf.save_excursion():
            buf.goto_char(8)
            assert buf.point() == 8
        assert buf.point() == 3

    def test_restores_after_insert(self):
        buf = VerilogBuffer.from_string("hello")
        buf.goto_char(2)
        with buf.save_excursion():
            buf.goto_char(5)
            buf.insert(" world")
        assert buf.point() == 2
        assert buf.buffer_string() == "hello world"


class TestNarrowing:
    def test_narrow_to_region(self):
        buf = VerilogBuffer.from_string("abcdefghij")
        buf.narrow_to_region(2, 7)
        assert buf.point_min() == 2
        assert buf.point_max() == 7
        assert buf.buffer_substring(2, 7) == "cdefg"

    def test_widen(self):
        buf = VerilogBuffer.from_string("abcdefghij")
        buf.narrow_to_region(2, 7)
        buf.widen()
        assert buf.point_min() == 0
        assert buf.point_max() == 10

    def test_save_restriction(self):
        buf = VerilogBuffer.from_string("abcdefghij")
        buf.narrow_to_region(2, 7)
        with buf.save_restriction():
            buf.widen()
            assert buf.point_max() == 10
        assert buf.point_max() == 7


class TestIndentTo:
    def test_indent_to(self):
        buf = VerilogBuffer.from_string("   hello")
        buf.goto_char(3)  # at 'h'
        buf.indent_to(6)
        assert buf.buffer_string() == "      hello"
