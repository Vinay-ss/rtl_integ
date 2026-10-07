"""Python 3 port of prepro: translation, execution, line map, captures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyverilog_auto.prepro import (
    NAME_ONLY,
    PAD_NONE,
    PAD_RIGHT,
    PreproError,
    PreproOptions,
    find_perl,
    preprocess_file,
    preprocess_text,
    recover_values,
    translate,
)
from pyverilog_auto.prepro.cli import main as prepro_main
from pyverilog_auto.prepro.cli import parse_args

FIX = Path(__file__).parent / "fixtures" / "prepro"
PERL = find_perl()
needs_perl = pytest.mark.skipif(PERL is None, reason="no perl interpreter")

FIFO_SUBS = [("#$", PAD_NONE), ("<#;>", PAD_NONE), (">#;>", PAD_RIGHT), ("#[;]#", PAD_NONE)]


def py(text: str, **kw):
    return preprocess_text(text, PreproOptions(language="python"), **kw)


# ----------------------------------------------------------------------
# translation
# ----------------------------------------------------------------------

def test_translate_python_text_line_tokens():
    tr = translate("a #$x b #[1+y]# c #$z#d\n", PreproOptions(language="python"))
    t = tr.lines[0]
    assert t.kind == "text"
    assert [tok.raw for tok in t.tokens] == ["#$x", "#[1+y]#", "#$z#"]
    assert [tok.inner for tok in t.tokens] == ["x", "1+y", "z"]
    for tok in t.tokens:
        assert t.text[tok.col:tok.col + len(tok.raw)] == tok.raw
    assert tr.script.splitlines()[0] == "print('a '+str(x)+' b '+str(1+y)+' c '+str(z)+'d');"


def test_translate_perl_quotes_and_eol():
    tr = translate("it's #$v\\n\n", PreproOptions(language="perl"))
    assert tr.script.splitlines()[0] == "print 'it\\'s '.$v.'\\\\n'.\"\\n\".'';"


def test_translate_kinds_and_indent():
    text = "\n".join([
        "/* py-begin",
        "x = 1",
        "py-end */",
        "// py for i in range(2):",
        "  line #$i",
        "// py",
        "after",
    ]) + "\n"
    tr = translate(text, PreproOptions(language="python"))
    assert [t.kind for t in tr.lines] == ["begin", "block", "end", "code", "text", "reset", "text"]
    assert tr.lines[4].indent == "  "
    assert tr.lines[6].indent == ""


# ----------------------------------------------------------------------
# python flavor
# ----------------------------------------------------------------------

def test_python_example_output_and_linemap():
    res = preprocess_file(str(FIX / "example_py3.txt"), PreproOptions(language="python", args=["a", "b", "c"]))
    expected = (FIX / "example_py3.expected").read_text(encoding="utf-8")
    assert res.text == expected
    lm = res.linemap
    assert not res.warnings
    assert len(lm.out) == len(expected.splitlines())
    # line 14 ("variable substitution") is inside the 3-iteration loop
    assert lm.emit_count(14) == 3
    assert lm.kind_of_output(1) == "code"            # printed from the py-begin block
    assert lm.kind_of_output(4) == "literal"         # "loop begin"
    assert lm.kind_of_output(5) == "subst"
    loop = lm.innermost(14)
    assert loop is not None and loop.kind == "for" and loop.is_loop
    assert (loop.start, loop.end) == (12, 19)
    assert lm.innermost(10) is None
    assert [c.kind for c in lm.constructs][:1] == ["codeblock"]


def test_python_crlf_preserved():
    res = py("a\r\n// py v = 2\r\nb #$v\r\n")
    assert res.text == "a\r\nb 2\r\n"
    assert res.linemap.eol == "\r\n"


def test_python_error_maps_to_template_line():
    with pytest.raises(PreproError) as ei:
        py("ok\nline #$undefined_name\n", template_path="tpl.svp")
    e = ei.value
    assert e.line == 2
    assert "tpl.svp" in e.stderr and "line 2" in e.stderr
    assert "NameError" in e.stderr


def test_python_capture_values_per_emission():
    text = "// py for i in range(3):\n  w #$i\n// py # end\n"
    res = py(text, capture={2: ["i", "missing_var"]})
    caps = res.linemap.captures[2]
    assert [c["i"] for c in caps] == ["0", "1", "2"]
    assert caps[0]["missing_var"] == "!missing"


def test_python_conditional_constructs():
    text = "\n".join([
        "// py w = 8",
        "// py if w > 4:",
        "  big",
        "// py else:",
        "  small",
        "// py if w > 1:",
        "  plain if",
        "// py # end",
    ]) + "\n"
    res = py(text)
    assert res.text == "  big\n  plain if\n"
    lm = res.linemap
    c3 = lm.innermost(3)
    assert c3 is not None and c3.kind == "if" and c3.has_else
    c7 = lm.innermost(7)
    assert c7 is not None and c7.kind == "if" and not c7.has_else


def test_recover_values_padded_token():
    opts = PreproOptions(language="python", substitutions=[("#$", PAD_NONE), (">#;>", PAD_RIGHT)])
    t = translate("  input >#w> a_#$n,\n", opts).lines[0]
    res = preprocess_text("// py w = '[7:0]'\n// py n = 'x'\n" + t.text + "\n", opts)
    out = res.text.rstrip("\n")
    assert out == "  input [7:0] a_x,"
    assert recover_values(t, out) == ["[7:0]", "x"]


def test_recover_values_simple():
    t = translate("x #$a y #[b]# z\n", PreproOptions(language="python")).lines[0]
    assert [tok.raw for tok in t.tokens] == ["#$a", "#[b]#"]   # column order
    assert recover_values(t, "x 1 y 2 z") == ["1", "2"]
    assert recover_values(t, "nope") is None


def test_cli_parse_original_flags():
    st = parse_args(["-c", "//x", "-py", "-r", "@@", "-rr", "<;>", "-d", "v=1", "-o", "out.v", "-k",
                     "--capture", "3:a,b", "in.tpl", "++", "p", "q"])
    o = st["options"]
    assert o.language == "python"
    assert o.line is None              # -py resets the code marker, as in prepro
    assert o.substitutions == [("@@", PAD_NONE), ("<;>", PAD_RIGHT)]
    assert o.defines == ["v=1"]
    assert o.args == ["p", "q"]
    assert st["infile"] == "in.tpl" and st["output"] == "out.v" and st["keep"]
    assert st["capture"] == {3: ["a", "b"]}


def test_cli_writes_output_and_linemap(tmp_path):
    tpl = tmp_path / "t.svp"
    tpl.write_text("// py for i in range(2):\n  x#$i\n// py # end\n", encoding="utf-8")
    out = tmp_path / "t.sv"
    lm = tmp_path / "t.sv.map.json"
    rc = prepro_main(["-py", "-o", str(out), "--linemap", str(lm), str(tpl)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == "  x0\n  x1\n"
    assert lm.exists()


# ----------------------------------------------------------------------
# perl flavor
# ----------------------------------------------------------------------

@needs_perl
def test_perl_simple_and_linemap():
    text = "\n".join([
        "/* pl-begin",
        "@names = ('a', 'b');",
        "pl-end */",
        "// pl for $n (@names) {",
        "wire #$n;",
        "// pl }",
        "done",
    ]) + "\n"
    res = preprocess_text(text, PreproOptions(language="perl"))
    assert res.text == "wire a;\nwire b;\ndone\n"
    lm = res.linemap
    assert lm.emit_count(5) == 2
    loop = lm.innermost(5)
    assert loop is not None and loop.kind == "for" and (loop.start, loop.end) == (4, 5)


@needs_perl
def test_perl_error_maps_to_template_line():
    with pytest.raises(PreproError) as ei:
        preprocess_text("a\n// pl die 'boom';\n", PreproOptions(language="perl"), template_path="x.plv")
    assert ei.value.line == 2


@needs_perl
def test_perl_fifofum_template():
    opts = PreproOptions(language="perl", substitutions=list(FIFO_SUBS), args=["myfifo:ff8x16fesc"])
    res = preprocess_file(str(FIX / "fifofum" / "fff_fifo.plv"), opts,
                          perl_lib=[str(FIX / "fifofum")], capture={33: ["module", "width_r"]})
    expected = (FIX / "fifofum" / "myfifo_ff8x16fesc.v").read_text(encoding="utf-8")
    assert res.text == expected
    lm = res.linemap
    assert lm.captures[33] == [{"module": '"myfifo"', "width_r": '"[15:0]"'}]
    # the port list is inside "if ($store ne 'as') {" and the optional ports in nested ifs
    outer = lm.enclosing(41)
    assert [c.kind for c in outer][-2:] == ["if", "if"]
    assert lm.innermost(37) is not None and lm.innermost(37).kind == "if"
    assert lm.kind_of_output(lm.outputs_of(37)[0]) == "subst"


@needs_perl
def test_perl_fifofum_elaborates():
    pyslang = pytest.importorskip("pyslang")
    opts = PreproOptions(language="perl", substitutions=list(FIFO_SUBS), args=["myfifo:ff8x16fesc"])
    res = preprocess_file(str(FIX / "fifofum" / "fff_fifo.plv"), opts, perl_lib=[str(FIX / "fifofum")])
    tree = pyslang.syntax.SyntaxTree.fromText(res.text, "myfifo.v")
    comp = pyslang.ast.Compilation()
    comp.addSyntaxTree(tree)
    errors = [d for d in comp.getAllDiagnostics() if d.isError()]
    # the FIFO instantiates the external clock gate cell "fff_gate" only for lp options
    assert errors == []


def test_perl_lib_value_native(monkeypatch):
    from pyverilog_auto.prepro import run as prun

    monkeypatch.setattr(prun, "perl_osname", lambda perl: "linux")
    assert prun.perl_lib_value("perl", ["a", "b"]) == os.pathsep.join(os.path.abspath(d) for d in ("a", "b"))


@pytest.mark.skipif(os.name != "nt", reason="MSYS path form is Windows-only")
def test_perl_lib_value_msys(monkeypatch):
    from pyverilog_auto.prepro import run as prun

    monkeypatch.setattr(prun, "perl_osname", lambda perl: "msys")
    assert prun.perl_lib_value("perl", [r"C:\Work\x"]) == "/c/Work/x"


# ----------------------------------------------------------------------
# backtick syntax: ` code, [* *] blocks, `var`
# ----------------------------------------------------------------------

def bt(text: str, language: str = "python", **kw):
    return preprocess_text(text, PreproOptions(language=language, syntax="backtick"), **kw)


BT_TEMPLATE = """[*
LANES = 2
*]
[* NAME = "top" *]
` W = 8
`define WIDTH 8
`define SUM(a, b) (a + b)
module `NAME`;
   logic [`WIDTH-1:0] a = `SUM(`WIDTH, 1);
   logic [`W`-1:0] b;
` for i in range(LANES):
   stage u_lane`i` (.d(a[`i`]));
`
   assert property (@(posedge clk) a[0] |-> a[1][*3]);
[*3] ##1 b;
   wire [#[W-1]#:0] w#<str(LANES)<#_x;
endmodule
"""


def test_backtick_syntax_python():
    res = bt(BT_TEMPLATE)
    assert res.text == "\n".join([
        "`define WIDTH 8",
        "`define SUM(a, b) (a + b)",
        "module top;",
        "   logic [`WIDTH-1:0] a = `SUM(`WIDTH, 1);",      # macros are not variables
        "   logic [8-1:0] b;",
        "   stage u_lane0 (.d(a[0]));",
        "   stage u_lane1 (.d(a[1]));",
        "   assert property (@(posedge clk) a[0] |-> a[1][*3]);",
        "[*3] ##1 b;",                                     # repetition, not a code block
        "   wire [7:0] w2             _x;",               # prepro's expression/padding tokens stay
        "endmodule",
    ]) + "\n"
    lm = res.linemap
    kinds = {t.lineno: (t.kind, t.code) for t in lm.lines if t.kind != "text"}
    assert kinds == {1: ("begin", None), 2: ("block", "LANES = 2"), 3: ("end", None), 4: ("code", 'NAME = "top"'),
                     5: ("code", "W = 8"), 11: ("code", "for i in range(LANES):"), 13: ("reset", None)}
    assert [tok.raw for tok in lm.tpl(12).tokens] == ["`i`", "`i`"]
    assert lm.tpl(9).tokens == []
    assert lm.emit_count(12) == 2
    loop = lm.innermost(12)
    assert loop is not None and loop.kind == "for" and (loop.start, loop.end) == (11, 12)


def test_backtick_code_next_to_block_markers():
    text = "[* a = 1\nb = a + 1 *]\nx`b`\n[*   \n  \n*]\ny\n"
    res = bt(text)
    assert res.text == "x2\ny\n"
    lm = res.linemap
    assert [(t.kind, t.code) for t in lm.lines[:2]] == [("begin", "a = 1"), ("end", "b = a + 1")]
    with pytest.raises(PreproError) as ei:
        bt("a\n[* x = 1\ny = undefined_name *]\n", template_path="t.svp")
    assert ei.value.line == 3


def test_backtick_names_python_dotted():
    res = bt("[* import types; cfg = types.SimpleNamespace(width=4) *]\nw`cfg.width`\n")
    assert res.text == "w4\n"


@needs_perl
def test_backtick_syntax_perl():
    text = "\n".join([
        "[*",
        "my @names = ('a', 'b');",
        "*]",
        "` my $w = 8;",
        "` for my $n (@names) {",
        "wire [`w`-1:0] `n`; // `WIDTH `n",
        "` }",
        "done",
    ]) + "\n"
    res = bt(text, language="perl")
    assert res.text == "wire [8-1:0] a; // `WIDTH `n\nwire [8-1:0] b; // `WIDTH `n\ndone\n"
    loop = res.linemap.innermost(6)
    assert loop is not None and loop.kind == "for" and (loop.start, loop.end) == (5, 6)


def test_backtick_cli_and_name_tokens(tmp_path):
    st = parse_args(["-py", "--syntax", "backtick", "-rn", "@;@", "x"])
    o = st["options"]
    assert o.syntax == "backtick" and o.line_id() == "`" and o.begin_id() == "[*" and o.end_id() == "*]"
    assert o.substitutions == [("@;@", PAD_NONE | NAME_ONLY)]
    # an explicit token list replaces the syntax's own tokens, as -r does in prepro
    res = preprocess_text("` v = 3\na@v@ `v` @v+1@\n", o)
    assert res.text == "a3 `v` @v+1@\n"
    tpl = tmp_path / "t.svp"
    tpl.write_text("` n = 2\nw`n`\n", encoding="utf-8")
    out = tmp_path / "t.sv"
    assert prepro_main(["-py", "--syntax", "backtick", "-o", str(out), str(tpl)]) == 0
    assert out.read_text(encoding="utf-8") == "w2\n"
    with pytest.raises(SystemExit):
        parse_args(["--syntax", "nope"])
    with pytest.raises(ValueError):
        PreproOptions(syntax="nope").line_id()
