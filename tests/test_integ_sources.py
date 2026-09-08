"""Tests for pyverilog_auto.integ.sources (byte<->char offset maps)."""

from __future__ import annotations

from pyverilog_auto.integ.sources import SourceFile, _OffsetMap, decode_source


def _check_roundtrip(data: bytes) -> None:
    text = decode_source(data)
    m = _OffsetMap(data)
    # Every char maps to a byte whose char maps back to itself.
    for c in range(len(text) + 1):
        b = m.byte_offset(c)
        assert 0 <= b <= len(data)
        assert m.char_offset(b) == c, (c, b)
    # Byte offsets are monotonic in char offsets.
    bs = [m.byte_offset(c) for c in range(len(text) + 1)]
    assert bs == sorted(bs)


def test_ascii_lf_identity(tmp_path):
    p = tmp_path / "a.v"
    p.write_bytes(b"module a;\n  wire x;\nendmodule\n")
    sf = SourceFile.read(str(p))
    assert sf.text == "module a;\n  wire x;\nendmodule\n"
    assert sf.char_offset(12) == 12 and sf.byte_offset(12) == 12
    assert sf.line_col(12) == (2, 2)
    assert sf.eol == "\n"


def test_crlf_mapping(tmp_path):
    data = b"module a;\r\n  wire x;\r\nendmodule\r\n"
    p = tmp_path / "a.v"
    p.write_bytes(data)
    sf = SourceFile.read(str(p))
    assert sf.text == "module a;\n  wire x;\nendmodule\n"
    assert sf.eol == "\r\n"
    # 'w' of "wire": text offset 12, byte offset 13 (one \r before it)
    assert sf.text[12] == "w" and data[13:14] == b"w"
    assert sf.char_offset(13) == 12
    assert sf.byte_offset(12) == 13
    # The \r byte itself maps to the newline char
    assert sf.char_offset(9) == 9      # '\r' -> index of '\n' in text
    assert sf.char_offset(10) == 9     # '\n'
    assert sf.byte_offset(9) == 9      # newline char -> start of its "\r\n" bytes
    assert sf.byte_offset(10) == 11    # char after the newline -> byte after "\r\n"
    _check_roundtrip(data)


def test_utf8_multibyte_mapping(tmp_path):
    # em dash (3 bytes) and e-acute (2 bytes) in a comment
    data = "// Mini SoC — café\nmodule a; endmodule\n".encode("utf-8")
    p = tmp_path / "a.v"
    p.write_bytes(data)
    sf = SourceFile.read(str(p))
    text = sf.text
    i = text.index("module")
    b = data.index(b"module")
    assert sf.char_offset(b) == i
    assert sf.byte_offset(i) == b
    # bytes inside the em dash map to the em dash char
    d = data.index("—".encode("utf-8"))
    assert sf.char_offset(d + 1) == text.index("—")
    _check_roundtrip(data)


def test_invalid_utf8_and_mixed_newlines():
    data = b"// caf\xe9 latin1\r\nmodule a;\rendmodule\n"
    text = decode_source(data)
    assert "�" in text and "\r" not in text
    _check_roundtrip(data)


def test_update_bumps_version_and_resets_maps(tmp_path):
    p = tmp_path / "a.v"
    p.write_bytes(b"module a;\nendmodule\n")
    sf = SourceFile.read(str(p))
    assert sf.version == 0
    sf.update("module a;\r\n  // é\r\nendmodule\r\n")
    assert sf.version == 1
    assert sf.tree is None
    assert sf.text.count("\n") == 3
    assert sf.byte_offset(len(sf.text)) == len(sf.data)
