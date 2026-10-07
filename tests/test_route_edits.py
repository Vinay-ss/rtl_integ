"""Unit tests for pyverilog_auto.integ.edits (no pyslang needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyverilog_auto.integ import is_slang_available
from pyverilog_auto.integ.design import Design
from pyverilog_auto.integ.edits import (
    EditError, EditSet, TextEdit, fence_spans_of, insert_list_entry, insert_lines_after,
)
from pyverilog_auto.integ.model import SrcRange


def _design(tmp_path: Path, text: str, name: str = "m.v", newline: str = "\n") -> tuple[Design, object]:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    d = Design.from_files([str(p)], backend="text")
    return d, d.file(str(p))


def _conn_range(design: Design, inst_name: str) -> SrcRange:
    for mod in design.modules.values():
        for r in mod.refs:
            if r.inst_name == inst_name:
                return r.conn_range
    raise AssertionError(inst_name)


def _marker(design: Design, inst_name: str) -> SrcRange:
    for mod in design.modules.values():
        for r in mod.refs:
            if r.inst_name == inst_name:
                assert r.marker_range is not None
                return r.marker_range
    raise AssertionError(inst_name)


SRC = """\
module top;
   sub u_a (/*AUTOINST*/);
   sub u_b (.x (x),
            /*AUTOINST*/);
   sub u_c ();
   sub u_d (.x (x), .y (y));
   sub u_e (.x (x)
            /*AUTOINST*/);
endmodule
"""


def test_marker_on_paren_line_moves_marker(tmp_path):
    d, sf = _design(tmp_path, SRC)
    edits = insert_list_entry(sf, _conn_range(d, "u_a"), ".p (n)", anchor=_marker(d, "u_a"),
                              followers_nonempty=True, comment="// routed")
    es = EditSet(d)
    es.extend(edits)
    out = es.render()[sf.key].decode()
    assert "   sub u_a (\n            .p (n),   // routed\n            /*AUTOINST*/);" in out


def test_marker_on_own_line_and_missing_comma_on_previous_pin(tmp_path):
    d, sf = _design(tmp_path, SRC)
    # u_b: previous pin already has a comma -> only the new line
    es = EditSet(d)
    es.extend(insert_list_entry(sf, _conn_range(d, "u_b"), ".p (n)", anchor=_marker(d, "u_b"), followers_nonempty=True))
    out = es.render()[sf.key].decode()
    assert "   sub u_b (.x (x),\n            .p (n),\n            /*AUTOINST*/);" in out
    # u_e: previous pin lacks a comma -> add it; expansion empty -> no trailing comma
    es = EditSet(d)
    es.extend(insert_list_entry(sf, _conn_range(d, "u_e"), ".p (n)", anchor=_marker(d, "u_e"), followers_nonempty=False))
    out = es.render()[sf.key].decode()
    assert "   sub u_e (.x (x),\n            .p (n)\n            /*AUTOINST*/);" in out


def test_append_to_empty_and_nonempty_lists(tmp_path):
    d, sf = _design(tmp_path, SRC)
    es = EditSet(d)
    es.extend(insert_list_entry(sf, _conn_range(d, "u_c"), ".p (n)"))
    out = es.render()[sf.key].decode()
    assert "   sub u_c (\n            .p (n)\n           );" in out
    es = EditSet(d)
    es.extend(insert_list_entry(sf, _conn_range(d, "u_d"), ".p (n)", comment="// r"))
    out = es.render()[sf.key].decode()
    assert "   sub u_d (.x (x), .y (y),\n            .p (n)   // r\n           );" in out
    # existing trailing comma before ')' on its own line
    d2, sf2 = _design(tmp_path / "b", "module t;\n   sub u_f (.x (x),\n            .y (y),\n           );\nendmodule\n")
    es = EditSet(d2)
    es.extend(insert_list_entry(sf2, _conn_range(d2, "u_f"), ".p (n)"))
    out = es.render()[sf2.key].decode()
    assert "            .y (y),\n            .p (n)\n           );" in out


def test_crlf_preserved_and_reverse_application(tmp_path):
    d, sf = _design(tmp_path, SRC, newline="\r\n")
    assert sf.eol == "\r\n"
    es = EditSet(d)
    es.extend(insert_list_entry(sf, _conn_range(d, "u_a"), ".p (n)", anchor=_marker(d, "u_a"), followers_nonempty=True))
    es.extend(insert_list_entry(sf, _conn_range(d, "u_b"), ".q (m)", anchor=_marker(d, "u_b"), followers_nonempty=True))
    es.add(insert_lines_after(sf, sf.text.index("module top;"), ["wire n;", "wire m;"], indent="   "))
    new = es.render()[sf.key]
    assert b"\n" not in new.replace(b"\r\n", b"")
    txt = new.decode().replace("\r\n", "\n")
    assert "module top;\n   wire n;\n   wire m;\n" in txt
    assert ".p (n),\n            /*AUTOINST*/" in txt
    assert ".q (m),\n            /*AUTOINST*/" in txt
    changed = es.apply(dry_run=True)
    assert changed == [sf.path]
    assert (tmp_path / "m.v").read_bytes().count(b".p (n)") == 0   # dry run
    assert ".p (n)" in sf.text                                       # overlay updated
    assert sf.version == 1


def test_apply_writes_and_refreshes(tmp_path):
    d, sf = _design(tmp_path, SRC)
    es = EditSet(d)
    es.add(insert_lines_after(sf, sf.text.index("module top;"), ["sub u_new ();"], indent="   "))
    changed = es.apply()
    assert changed == [sf.path]
    assert "sub u_new ();" in (tmp_path / "m.v").read_text()
    assert any(r.inst_name == "u_new" for r in d.modules["top"].refs)


def test_overlap_and_fence_checks(tmp_path):
    d, sf = _design(tmp_path, SRC)
    es = EditSet(d)
    es.add(TextEdit(sf.key, 10, 20, "x", description="a"), TextEdit(sf.key, 15, 25, "y", description="b"))
    with pytest.raises(EditError):
        es.check()
    es = EditSet(d)
    es.add(TextEdit(sf.key, 5, 5, "z", description="in fence"))
    with pytest.raises(EditError):
        es.check({sf.key: [(0, 10)]})
    es = EditSet(d)
    es.add(TextEdit(sf.key, 5, 5, "z"), TextEdit(sf.key, 5, 5, "w"))   # two insertions at one point are fine
    es.check()


RESET_SRC = """\
module r (input clk, input rst, output reg q);
   always @(posedge clk) begin
      if (rst) begin
         /*AUTORESET*/
         // Beginning of autoreset for uninitialized flops
         q <= 1'h0;
         // End of automatics
      end
      else begin
         q <= ~q;
      end
   end
endmodule
"""


@pytest.mark.parametrize("backend", [
    "text",
    pytest.param("slang", marks=pytest.mark.skipif(not is_slang_available(), reason="pyslang backend unavailable")),
])
def test_autoreset_block_is_a_fence(tmp_path, backend):
    # AUTORESET opens with "// Beginning of autoreset ...", not "... automatic ..."
    p = tmp_path / "r.v"
    p.write_bytes(RESET_SRC.encode("utf-8"))
    d = Design.from_files([str(p)], backend=backend)
    sf = d.file(str(p))
    mod = d.modules["r"]
    (mi,) = mod.markers["AUTORESET"]
    assert mi.fence_range is not None
    fenced = sf.text[mi.fence_range.start:mi.fence_range.end]
    assert fenced.lstrip().startswith("// Beginning of autoreset for uninitialized flops")
    assert fenced.endswith("// End of automatics")
    spans = {sf.key: fence_spans_of(mod)}
    inside = sf.text.index("q <= 1'h0;")
    es = EditSet(d)
    es.add(TextEdit(sf.key, inside, inside, "x <= 1'b0;\n         ", description="in autoreset"))
    with pytest.raises(EditError, match="AUTO fence"):
        es.check(spans)
    outside = sf.text.index("q <= ~q;")
    es = EditSet(d)
    es.add(TextEdit(sf.key, outside, outside, "x <= 1'b1;\n         ", description="hand-written branch"))
    es.check(spans)


def test_stale_file_detection(tmp_path):
    d, sf = _design(tmp_path, SRC)
    (tmp_path / "m.v").write_text(SRC + "// touched\n")
    es = EditSet(d)
    es.add(TextEdit(sf.key, 0, 0, "// x\n"))
    with pytest.raises(EditError):
        es.check()
