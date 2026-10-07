"""Tests for strip mode: ``pyverilog_auto.auto.strip.strip_autos``.

* one unit case per removed / kept form;
* a corpus property test: for every ``tests/*.v`` case that ``test_golden``
  runs and that is not in ``tests/baseline_failures.txt``,
  ``s = strip(expand(x))`` has no active AUTO attribute, keeps the code tokens
  of ``expand(x)`` (comments removed, ``.*`` normalized away) and
  ``strip(s) == s``.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.auto.strip import MARKER_NAMES, find_active_markers, strip_autos
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig
from pyverilog_auto.local_vars import apply_local_vars, parse_local_vars

TESTS_DIR = Path(__file__).resolve().parent


def _strip(text: str) -> str:
    """strip_autos plus the invariants every result must satisfy."""
    out = strip_autos(text)
    assert strip_autos(out) == out, "not idempotent"
    assert find_active_markers(out) == []
    old_lines = set(text.split("\n"))
    for line in out.split("\n"):
        body = line.rstrip("\r")
        assert body == body.rstrip(" \t") or not body.strip() or line in old_lines, \
            f"trailing whitespace added: {line!r}"
    return out


# ----------------------------------------------------------------------
# Marker comments
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name", MARKER_NAMES)
def test_marker_alone_on_line_removes_line(name):
    src = f"module m;\n   wire a;\n   /*{name}*/\n   wire b;\nendmodule\n"
    assert _strip(src) == "module m;\n   wire a;\n   wire b;\nendmodule\n"


@pytest.mark.parametrize("marker", [
    "/*autoinst*/",                                  # case-insensitive
    "/* AUTOINST */",                                # inner spaces
    '/*AUTOINST("^a_")*/',
    '/*AUTOINOUTMODULE("ExampMain", "^in")*/',
    '/*AUTOASCIIENUM("state", "state_ascii", "S_")*/',
    '/*AUTOINSERTLISP(my-verilog-insert-hello "world")*/',
    "/*AUTOINSERTLAST(verilog-auto-tieoff)*/",
    "/*memory or*/",
])
def test_marker_variants(marker):
    assert _strip(f"module m;\n   {marker}\nendmodule\n") == "module m;\nendmodule\n"


@pytest.mark.parametrize("src, want", [
    ("module m (/*AUTOARG*/);\n", "module m ();\n"),
    ("module m (output j/*AUTOARG*/);\n", "module m (output j);\n"),
    ("module m (i, o,/*AUTOARG*/\n   a);\n", "module m (i, o,\n   a);\n"),
    ("   always @(/*AUTOSENSE*/ a or b)\n", "   always @(a or b)\n"),
    ("   always @(/*AUTOSENSE*/a or b)\n", "   always @(a or b)\n"),
    ("   always @(a or/*AUTOSENSE*/c)\n", "   always @(a or c)\n"),
    ("   always @(a/*AUTOSENSE*/ or c)\n", "   always @(a or c)\n"),
    ("   always @(a/*AS*/) begin\n", "   always @(a) begin\n"),
    ("   always @(/*AUTOSENSE*/ /*memory or*/ x or y)\n", "   always @(x or y)\n"),
    ("           /*AUTOSENSE*/b)\n", "           b)\n"),
    ("   wire a; /*AUTOWIRE*/\n", "   wire a;\n"),
])
def test_inline_marker_spacing(src, want):
    assert _strip(src) == want


@pytest.mark.parametrize("src", [
    "module lba\n  (/*AUTOnotARG*/\n   // Outputs\n   );\nendmodule\n",
    "   // the pad instance to look like \"pads pads (/*AUTOINST*/\". Then at\n",
    "   //   always @(a or/*AUTOSENSE*/c) begin\n",
    "   //  always @(/*AUTOnotSENSE*/\n",
    '   initial $display("/*AUTOINST*/ // Templated");\n',
    "   /* AUTOWIRE is used below */\n",
    "/*\n  Uses AUTO_TEMPLATE for the sub instances.\n*/\n",
    "   // Beginning of autonomous mode\n   wire a; // To u of sub.v\n",
    "   foo u (.*);\n",                                   # .* without expanded pins
    "   //  /* xx AUTO_TEMPLATE (\n   //.TWI_\\(.*\\) (),\n   //);\n   //   */\n",
    "   //   .a (a),  // Templated\n",                   # commented-out pin line
])
def test_inactive_and_user_comments_survive(src):
    assert strip_autos(src) == src
    assert find_active_markers(src) == []


# ----------------------------------------------------------------------
# Fences, templates, lisp, enum tags, auto_route
# ----------------------------------------------------------------------

def test_fences_removed_content_kept():
    src = (
        "module m;\n"
        "   /*AUTOWIRE*/\n"
        "   // Beginning of automatic wires (for undeclared instantiated-module outputs)\n"
        "   wire a;  // From u of sub.v\n"
        "   // End of automatics\n"
        "   always @(posedge clk) begin\n"
        "      if (rst) begin\n"
        "         /*AUTORESET*/\n"
        "         // Beginning of autoreset for uninitialized flops\n"
        "         q <= 1'h0;\n"
        "         // End of automatics\n"
        "      end\n"
        "   end\n"
        "endmodule\n"
    )
    want = (
        "module m;\n"
        "   wire a;  // From u of sub.v\n"
        "   always @(posedge clk) begin\n"
        "      if (rst) begin\n"
        "         q <= 1'h0;\n"
        "      end\n"
        "   end\n"
        "endmodule\n"
    )
    assert _strip(src) == want


def test_auto_template_blocks():
    src = (
        "module m;\n"
        "\n"
        "   /* sub AUTO_TEMPLATE\n"
        "      sub2 AUTO_TEMPLATE \"u_\\(.*\\)\" (\n"
        "      .a (b),   // AUTONOHOOKUP\n"
        "    ); */\n"
        "\n"
        "   /* InstMod AUTO_TEMPLATE(\n"
        "        .WIDTH(@\"vh-TOP_WIDTH\"),\n"
        "      ) */\n"
        "\n"
        "   // /* sub AUTO_TEMPLATE ( .x (y) ); */\n"
        "   sub u ();\n"
        "endmodule\n"
    )
    want = (
        "module m;\n"
        "\n"
        "   // /* sub AUTO_TEMPLATE ( .x (y) ); */\n"
        "   sub u ();\n"
        "endmodule\n"
    )
    assert _strip(src) == want


def test_auto_lisp_and_constant():
    src = (
        "module m;\n"
        "   /*AUTO_LISP(setq my-nc-output \"\\/*NC*\\/\"  my-space \"|\")*/\n"
        "   /* AUTO_LISP(defun f (sig) (let ((result \"{\"))\n"
        "    (concat result \")\" sig \"}\")))*/\n"
        "   always @(b)\n"
        "     case (b)\n"
        "       /*AUTO_CONSTANT(`ot.BOC) */\n"
        "       /* AUTO_CONSTANT ( \"name0\" \"name1\" ) */\n"
        "       /* AUTO_CONSTANT( temp )*/\n"
        "       default: ;\n"
        "     endcase\n"
        "endmodule\n"
    )
    want = (
        "module m;\n"
        "   always @(b)\n"
        "     case (b)\n"
        "       default: ;\n"
        "     endcase\n"
        "endmodule\n"
    )
    assert _strip(src) == want


def test_auto_lisp_keeps_other_comment_text():
    src = "   /* note AUTO_LISP(setq x (1)) */\n"
    assert _strip(src) == "   /* note  */\n"


def test_auto_enum_tags():
    src = (
        "   reg [2:0]        /* auto enum sm_psm */ sm_psm;\n"
        "   localparam [2:0] // auto enum sm_psm\n"
        "                    PSM_IDL = 0;\n"
        "   reg [2:0] st; // synopsys enum sm_psm\n"
    )
    want = (
        "   reg [2:0]        sm_psm;\n"
        "   localparam [2:0]\n"
        "                    PSM_IDL = 0;\n"
        "   reg [2:0] st; // synopsys enum sm_psm\n"
    )
    assert _strip(src) == want


def test_auto_route_annotations():
    src = (
        "module m (\n"
        "   output logic busy,  //auto_route busy :: to :: instB\n"
        "   input  logic clk\n"
        "   );\n"
        "   //auto_route instC:c_busy :: to :: instD:d_hold\n"
        "   /* auto_route data_ch :: from :: instE */\n"
        "   // AUTO_ROUTE ctl :: to :: instF, instG\n"
        "endmodule\n"
    )
    want = (
        "module m (\n"
        "   output logic busy,\n"
        "   input  logic clk\n"
        "   );\n"
        "endmodule\n"
    )
    assert _strip(src) == want


# ----------------------------------------------------------------------
# Instance and AUTOARG lists
# ----------------------------------------------------------------------

def test_instance_headers_and_pin_comments():
    src = (
        "   sub u (\n"
        "          .busy (core_busy),   // routed: busy\n"
        "          /*AUTOINST*/\n"
        "          // Interfaces\n"
        "          .m_axi (m_axi),   // routed: axi\n"
        "          // Outputs\n"
        "          .c_in (c_in[7:0]),    // input  [7:0] logic  // Templated\n"
        "          .d (d),    // Templated 12\n"
        "          .e (e),    // Templated LHS: ^\\(.*\\)$\n"
        "          .f (f),    // Templated AUTONOHOOKUP\n"
        "          .g (g),    // output wire  // Templated  // keep me\n"
        "          .h (),     // routed: unconnected here\n"
        "          // Inputs\n"
        "          .i (i));   // Templated\n"
    )
    want = (
        "   sub u (\n"
        "          .busy (core_busy),\n"
        "          .m_axi (m_axi),\n"
        "          .c_in (c_in[7:0]),    // input  [7:0] logic\n"
        "          .d (d),\n"
        "          .e (e),\n"
        "          .f (f),\n"
        "          .g (g),    // output wire  // keep me\n"
        "          .h (),\n"
        "          .i (i));\n"
    )
    assert _strip(src) == want


def test_instparam_and_autoarg_headers():
    src = (
        "module m (/*AUTOARG*/\n"
        "   // Outputs\n"
        "   a, b,\n"
        "   // Inputs\n"
        "   c);\n"
        "   sub #(/*AUTOINSTPARAM*/\n"
        "         // Parameters\n"
        "         .W (W))\n"
        "     u (/*AUTOINST*/\n"
        "        // Outputs\n"
        "        .a (a));\n"
        "endmodule\n"
    )
    want = (
        "module m (\n"
        "   a, b,\n"
        "   c);\n"
        "   sub #(\n"
        "         .W (W))\n"
        "     u (\n"
        "        .a (a));\n"
        "endmodule\n"
    )
    assert _strip(src) == want


def test_headers_outside_auto_lists_survive():
    src = (
        "module m (\n"
        "   // Outputs\n"
        "   a,\n"
        "   // Inputs\n"
        "   b);\n"
        "   sub u (\n"
        "      // Inputs\n"
        "      .b (b));\n"
        "   // Outputs\n"
        "endmodule\n"
    )
    assert strip_autos(src) == src


def test_auto_star_with_implicit_pins():
    src = (
        "   sub_1 sub_1 (.*,\n"
        "                // Outputs\n"
        "                .z    (z),     // Implicit .*\n"
        "                // Inputs\n"
        "                .a    (a));    // Implicit .*\n"
        "   sub_2 sub_2 (.x (x),\n"
        "                .*);\n"
    )
    want = (
        "   sub_1 sub_1 (\n"
        "                .z    (z),\n"
        "                .a    (a));\n"
        "   sub_2 sub_2 (.x (x),\n"
        "                .*);\n"
    )
    assert _strip(src) == want


def test_auto_star_last_with_implicit_pins():
    src = (
        "   sub u (.x (x),  // Implicit .*\n"
        "          .*);\n"
    )
    assert _strip(src) == "   sub u (.x (x)\n          );\n"


def test_provenance_and_port_comments_kept():
    src = (
        "   output [7:0] q;  // From u of sub.v\n"
        "   wire         w;  // To/From u of sub.v\n"
        "   sub u (.c_in (c_in[7:0]),    // input  [7:0] logic\n"
        "          .o    (o));           // output wire  // user note\n"
    )
    assert strip_autos(src) == src


# ----------------------------------------------------------------------
# Local Variables, mode cookie
# ----------------------------------------------------------------------

def test_local_variables_line_form():
    src = (
        "endmodule\n"
        "\n"
        "// Local Variables:\n"
        "// verilog-library-directories:(\".\")\n"
        "// eval:(setq x \"/*AUTOINST*/\")\n"
        "// End:\n"
    )
    assert _strip(src) == "endmodule\n"


def test_local_variables_line_form_inside_module():
    src = (
        "   end\n"
        "\n"
        "   // Local Variables:\n"
        "   // verilog-library-directories:(\".\" \"../cdp/\")\n"
        "   // End:\n"
        "\n"
        "endmodule\n"
    )
    assert _strip(src) == "   end\n\nendmodule\n"


def test_local_variables_block_form():
    src = (
        "endmodule\n"
        "/*\n"
        " Local Variables:\n"
        " eval:\n"
        "   (defun my-verilog-insert-hello (who)\n"
        "     (insert (concat \"initial $write(\\\"hello \" who \"\\\");\\n\")))\n"
        " End:\n"
        "*/\n"
    )
    assert _strip(src) == "endmodule\n"


def test_unterminated_local_variables_kept():
    src = "// Local Variables:\n// verilog-auto-inst-sort: t\nwire a;\n"
    assert strip_autos(src) == src


@pytest.mark.parametrize("first, want", [
    ("//-*- mode: Verilog; verilog-indent-level: 3; indent-tabs-mode: nil -*-\n\n", ""),
    ("// $Revision: #70 $$Date: 2002/10/19 $ -*- Verilog -*-\n",
     "// $Revision: #70 $$Date: 2002/10/19 $\n"),
    ("// -*- C -*-\n", "// -*- C -*-\n"),
])
def test_mode_cookie(first, want):
    assert _strip(first + "module m;\nendmodule\n") == want + "module m;\nendmodule\n"


def test_mode_cookie_only_on_first_line():
    src = "module m;\n// -*- mode: Verilog -*-\nendmodule\n"
    assert strip_autos(src) == src


# ----------------------------------------------------------------------
# Layout, EOL style, idempotency
# ----------------------------------------------------------------------

_SAMPLE = (
    "module m (/*AUTOARG*/\n"
    "   // Outputs\n"
    "   q,\n"
    "   // Inputs\n"
    "   a);\n"
    "\n"
    "   /*AUTOOUTPUT*/\n"
    "   // Beginning of automatic outputs (from unused autoinst outputs)\n"
    "   output q;  // From u of sub.v\n"
    "   // End of automatics\n"
    "\n"
    "   /* sub AUTO_TEMPLATE (\n"
    "      .a (a),\n"
    "    ); */\n"
    "\n"
    "   sub u (/*AUTOINST*/\n"
    "          // Outputs\n"
    "          .q (q),   // Templated\n"
    "          // Inputs\n"
    "          .a (a));  // Templated\n"
    "endmodule\n"
    "\n"
    "// Local Variables:\n"
    "// verilog-auto-inst-sort: t\n"
    "// End:\n"
)
_SAMPLE_WANT = (
    "module m (\n"
    "   q,\n"
    "   a);\n"
    "\n"
    "   output q;  // From u of sub.v\n"
    "\n"
    "   sub u (\n"
    "          .q (q),\n"
    "          .a (a));\n"
    "endmodule\n"
)


def test_sample_no_doubled_blank_lines():
    out = _strip(_SAMPLE)
    assert out == _SAMPLE_WANT
    assert "\n\n\n" not in out


def test_crlf_preserved():
    out = _strip(_SAMPLE.replace("\n", "\r\n"))
    assert out == _SAMPLE_WANT.replace("\n", "\r\n")


def test_lf_stays_lf():
    assert "\r" not in strip_autos(_SAMPLE)


def test_idempotent():
    once = strip_autos(_SAMPLE)
    assert strip_autos(once) == once


def test_untouched_text_is_returned_unchanged():
    src = "module m;\r\n   wire a;\n   assign a = 1'b0; // comment\r\nendmodule"   # mixed EOL
    assert strip_autos(src) == src
    assert strip_autos("") == ""


def test_no_trailing_newline():
    assert _strip("module m;\nendmodule /*AUTOARG*/") == "module m;\nendmodule"


def test_find_active_markers_kinds():
    kinds = {k for _, _, k in find_active_markers(_SAMPLE)}
    assert {"marker", "fence", "auto_template", "header", "pin_comment", "local_variables"} <= kinds


# ----------------------------------------------------------------------
# Corpus property test
# ----------------------------------------------------------------------

def _load_golden_module():
    spec = importlib.util.spec_from_file_location("_strip_golden_cases", TESTS_DIR / "test_golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GOLDEN = _load_golden_module()
_BASELINE = set(re.findall(r"\[([^\]]+)\]", (TESTS_DIR / "baseline_failures.txt").read_text()))
_EXCLUDED = _BASELINE | _GOLDEN._SKIP_CASES | _GOLDEN._LABEL_CASES | _GOLDEN._INJECT_CASES
CORPUS_CASES = [c for c in _GOLDEN.ALL_CASES if c not in _EXCLUDED]

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)


def _code_tokens(text: str) -> list[str]:
    """Code tokens with comments removed, whitespace dropped and ``.*`` normalized away."""
    code = _COMMENT_OR_STRING.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else " ", text)
    toks = re.findall(r"[A-Za-z0-9_$']+|\S", code)
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] == "." and toks[i + 1:i + 2] == ["*"]:
            if toks[i + 2:i + 3] == [","]:
                i += 3
                continue
            if out and out[-1] == ",":
                out.pop()
            i += 2
            continue
        out.append(toks[i])
        i += 1
    return out


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory):
    """One copy of the test corpus (library files) shared by every case."""
    d = tmp_path_factory.mktemp("strip_corpus")
    for entry in TESTS_DIR.iterdir():
        if entry.is_file() and entry.suffix in (".v", ".vc", ".vh", ".sv"):
            shutil.copy(entry, d / entry.name)
        elif entry.is_dir() and not entry.name.startswith(("__", ".")):
            shutil.copytree(entry, d / entry.name)
    return d


def _expand(case: str, corpus: Path) -> str:
    text = _GOLDEN._strip_trailing_ws((corpus / case).read_text())
    buf = VerilogBuffer.from_string(text, str(corpus / case))
    config = VerilogConfig(library_directories=[".", str(corpus)])
    local_vars = parse_local_vars(buf)
    if local_vars:
        config = apply_local_vars(config, local_vars)
    try:
        AutoEngine(config).run(buf, config)
    except Exception as exc:  # pragma: no cover - engine failures belong to test_golden
        pytest.skip(f"engine raised {type(exc).__name__}: {exc}")
    return buf.buffer_string()


def test_corpus_cases_selected():
    assert len(CORPUS_CASES) > 300
    assert not set(CORPUS_CASES) & _BASELINE


@pytest.mark.parametrize("case", CORPUS_CASES)
def test_corpus_strip_properties(case, corpus_dir):
    expanded = _expand(case, corpus_dir)
    stripped = strip_autos(expanded)
    assert find_active_markers(stripped) == [], f"{case}: active AUTO attribute left"
    assert _code_tokens(stripped) == _code_tokens(expanded), f"{case}: code tokens changed"
    assert strip_autos(stripped) == stripped, f"{case}: not idempotent"
    crlf = expanded.replace("\n", "\r\n")
    assert strip_autos(crlf) == stripped.replace("\n", "\r\n"), f"{case}: CRLF result differs"


def test_raw_sources_strip_properties():
    """Strip also behaves on unexpanded sources (every tests/*.v, no engine)."""
    bad = []
    for path in sorted(TESTS_DIR.glob("*.v")):
        text = path.read_text(errors="replace")
        stripped = strip_autos(text)
        if (find_active_markers(stripped) or strip_autos(stripped) != stripped
                or _code_tokens(stripped) != _code_tokens(text)):
            bad.append(path.name)
    assert not bad, f"strip properties violated for {bad[:10]}"
