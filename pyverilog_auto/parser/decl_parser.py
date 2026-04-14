"""DeclParser — parse signal declarations inside a Verilog module.

Ported from ``verilog-read-decls`` (lines 9296–9581 of
``verilog-mode.el``).  This is a hand-written state machine that scans
a module body and builds a :class:`ModDecls` object.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from ..signal import ModDecls, Modport, Signal

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


# ── Verilog keywords (signal names must NOT be in this set) ──────────
VERILOG_KEYWORDS: set[str] = {
    "after", "alias", "always", "always_comb", "always_ff", "always_latch",
    "analog", "and", "assert", "assign", "assume", "automatic", "before",
    "begin", "bind", "bins", "binsof", "bit", "break", "buf", "bufif0",
    "bufif1", "byte", "case", "casex", "casez", "cell", "chandle", "class",
    "clocking", "cmos", "config", "const", "constraint", "context",
    "continue", "cover", "covergroup", "coverpoint", "cross", "deassign",
    "default", "defparam", "design", "disable", "dist", "do", "edge",
    "else", "end", "endcase", "endclass", "endclocking", "endconfig",
    "endfunction", "endgenerate", "endgroup", "endinterface", "endmodule",
    "endpackage", "endprimitive", "endprogram", "endproperty", "endspecify",
    "endsequence", "endtable", "endtask", "enum", "event", "expect",
    "export", "extends", "extern", "final", "first_match", "for", "force",
    "foreach", "forever", "fork", "forkjoin", "function", "generate",
    "genvar", "highz0", "highz1", "if", "iff", "ifnone", "ignore_bins",
    "illegal_bins", "import", "incdir", "include", "initial", "inout",
    "input", "inside", "instance", "int", "integer", "interface",
    "intersect", "join", "join_any", "join_none", "large", "liblist",
    "library", "local", "localparam", "logic", "longint", "macromodule",
    "mailbox", "matches", "medium", "modport", "module", "nand", "negedge",
    "new", "nmos", "nor", "noshowcancelled", "not", "notif0", "notif1",
    "null", "or", "output", "package", "packed", "parameter", "pmos",
    "posedge", "primitive", "priority", "program", "property", "protected",
    "pull0", "pull1", "pulldown", "pullup", "pulsestyle_onevent",
    "pulsestyle_ondetect", "pure", "rand", "randc", "randcase",
    "randsequence", "rcmos", "real", "realtime", "ref", "reg", "release",
    "repeat", "return", "rnmos", "rpmos", "rtran", "rtranif0", "rtranif1",
    "scalared", "semaphore", "sequence", "shortint", "shortreal",
    "showcancelled", "signed", "small", "solve", "specify", "specparam",
    "static", "string", "strong0", "strong1", "struct", "super", "supply0",
    "supply1", "table", "tagged", "task", "this", "throughout", "time",
    "timeprecision", "timeunit", "tran", "tranif0", "tranif1", "tri",
    "tri0", "tri1", "triand", "trior", "trireg", "type", "typedef",
    "union", "unique", "unsigned", "use", "uwire", "var", "vectored",
    "virtual", "void", "wait", "wait_order", "wand", "weak0", "weak1",
    "while", "wildcard", "wire", "with", "within", "wor", "xnor", "xor",
    # 1800-2009
    "accept_on", "checker", "endchecker", "eventually", "global", "implies",
    "let", "nexttime", "reject_on", "restrict", "s_always", "s_eventually",
    "s_nexttime", "s_until", "s_until_with", "strong", "sync_accept_on",
    "sync_reject_on", "unique0", "until", "until_with", "untyped", "weak",
    # 1800-2012
    "implements", "interconnect", "nettype", "soft",
    # AMS
    "connectmodule", "endconnectmodule",
    # Compiler directives
    "`celldefine", "`endcelldefine", "`unconnected_drive",
    "`nounconnected_drive", "`default_nettype", "`suppress_faults",
    "`nosuppress_faults", "`resetall", "`timescale", "`define", "`undef",
    "`ifdef", "`ifndef", "`elsif", "`else", "`endif", "`include",
    "`pragma", "`line",
}

# Net / variable type keywords that signal a new declaration section
_TYPE_KEYWORDS: set[str] = {
    "wire", "reg",
    # net_type
    "tri", "tri0", "tri1", "triand", "trior", "trireg", "uwire", "wand", "wor",
    # integer_atom_type
    "byte", "shortint", "int", "longint", "integer", "time",
    "supply0", "supply1",
    # integer_vector_type (reg is above)
    "bit", "logic",
    # non_integer_type
    "shortreal", "real", "realtime",
    # data_type
    "string", "event", "chandle",
}

# Module-level definition keywords
_DEFUN_RE = re.compile(
    r"\b(?:macromodule|connectmodule|module|class|program"
    r"|interface|package|primitive|config)\b"
)
_END_DEFUN_RE = re.compile(
    r"\b(?:endconnectmodule|endmodule|endclass|endprogram"
    r"|endinterface|endpackage|endprimitive|endconfig)\b"
)

# Identifier pattern (Python regex, NOT Emacs)
_IDENT_RE = re.compile(r"[a-zA-Z0-9`_$]+|\\[^ \t\n\f]+")
_WS_RE = re.compile(r"[ \t\n\r\f]+")
_WS_IDENT_RE = re.compile(r"[ \t\n\r\f]*([a-zA-Z0-9`_$]+|\\[^ \t\n\f]+)")
_ENUM_RE = re.compile(r"[^\n]*(auto|synopsys)\s+enum\s+([a-zA-Z0-9_]+)")
_PACKAGE_SEP_RE = re.compile(r"[ \t\n\r\f]*::[ \t\n\r\f]*(\*|[a-zA-Z0-9`_$]+|\\[^ \t\n\f]+)")
_INTERFACE_DOT_RE = re.compile(r"[ \t\n\r\f]*\.[ \t\n\r\f]*([a-zA-Z`_$][a-zA-Z0-9`_$]*)")
_INTERFACE_AHEAD_RE = re.compile(
    r"[ \t\n\r\f]*(?:#|(?:\.[ \t\n\r\f]*[a-zA-Z`_$][a-zA-Z0-9`_$]*)?[ \t\n\r\f]*[a-zA-Z`_$][a-zA-Z0-9`_$]*)"
)
_DOT_AHEAD_RE = re.compile(r"[ \t\n\r\f]*\.")


class DeclParser:
    """Parse signal declarations from a :class:`VerilogBuffer`.

    Scans the module body from point and returns a :class:`ModDecls`
    populated with all found signals.

    NOTE: This parser operates on the raw text string directly (via
    ``buf.buffer_string()``) using Python ``re`` patterns, rather than
    going through the Emacs-regex translation in ``VerilogBuffer.looking_at``.
    """

    def __init__(self, buf: "VerilogBuffer", config: "VerilogConfig") -> None:
        self._buf = buf
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, limit: Optional[int] = None) -> ModDecls:
        """Scan *buf* from current point to *limit* (or module end).

        Return :class:`ModDecls` populated with all found signals.
        """
        text = self._buf.buffer_string()
        n = len(text)

        # Find end of module
        if limit is not None:
            end_mod = limit
        else:
            end_mod = self._find_end_defun(text, self._buf.point())
            if end_mod is None:
                end_mod = n

        # Find beginning of current module definition
        pos = self._find_beg_defun(text, self._buf.point())

        # Read AUTO_CONSTANTs
        sigs_const = self._read_auto_constants(text, pos, end_mod)

        # State variables
        functask = 0
        paren = 0
        sig_paren = 0
        v2kargs_ok = True

        in_modport: object = False  # False, True, or Modport
        in_clocking: object = False
        in_ign_to_semi = False
        ptype = False
        ign_prop = False

        # Signal accumulator lists
        sigs_in: list[Signal] = []
        sigs_out: list[Signal] = []
        sigs_inout: list[Signal] = []
        sigs_var: list[Signal] = []
        sigs_assign: list[Signal] = []
        sigs_gparam: list[Signal] = []
        sigs_intf: list[Signal] = []
        sigs_modports: list[Modport] = []

        # Current declaration state
        vec: Optional[str] = None
        expect_signal: Optional[str] = None
        keywd: str = ""
        last_keywd: str = ""
        newsig: Optional[Signal] = None
        rvalue = False
        enum: Optional[str] = None
        io = False
        signed: Optional[str] = None
        typedefed: Optional[str] = None
        multidim: Optional[list[str]] = None
        modport: Optional[str] = None

        varstack: list[tuple[list[Signal], list[Signal], list[Signal]]] = []

        sig_lists: dict[str, list[Signal]] = {
            "sigs_in": sigs_in,
            "sigs_out": sigs_out,
            "sigs_inout": sigs_inout,
            "sigs_var": sigs_var,
            "sigs_assign": sigs_assign,
            "sigs_const": sigs_const,
            "sigs_gparam": sigs_gparam,
            "sigs_intf": sigs_intf,
        }

        while pos < end_mod:
            ch = text[pos] if pos < n else ""
            if not ch:
                break

            # ── Skip whitespace ──
            if ch in " \t\n\r\f":
                m = _WS_RE.match(text, pos)
                if m:
                    pos = m.end()
                else:
                    pos += 1
                continue

            # ── Line comment ──
            if ch == "/" and pos + 1 < n and text[pos + 1] == "/":
                em = _ENUM_RE.match(text, pos)
                if em:
                    enum = em.group(2)
                eol = text.find("\n", pos)
                pos = (eol + 1) if eol >= 0 else end_mod
                continue

            # ── Block comment ──
            if ch == "/" and pos + 1 < n and text[pos + 1] == "*":
                pos += 2
                em = _ENUM_RE.match(text, pos - 2)
                if em:
                    enum = em.group(2)
                close = text.find("*/", pos)
                pos = (close + 2) if close >= 0 else end_mod
                continue

            # ── Protected sections ──
            if ch == "`":
                if text[pos:pos + 30].lstrip().startswith("`pragma") and "protect" in text[pos:pos + 60]:
                    prot_end = text.find("`pragma", pos + 7)
                    if prot_end >= 0 and "end_protected" in text[prot_end:prot_end + 40]:
                        eol = text.find("\n", prot_end)
                        pos = (eol + 1) if eol >= 0 else end_mod
                        continue
                if text[pos:].startswith("`protected"):
                    ep = text.find("`endprotected", pos)
                    if ep >= 0:
                        eol = text.find("\n", ep)
                        pos = (eol + 1) if eol >= 0 else end_mod
                    else:
                        eol = text.find("\n", pos)
                        pos = (eol + 1) if eol >= 0 else end_mod
                    continue

            # ── Attribute (* ... *) ──
            if ch == "(" and pos + 1 < n and text[pos + 1] == "*":
                close = text.find("*)", pos + 2)
                pos = (close + 2) if close >= 0 else end_mod
                continue

            # ── Quoted string ──
            if ch == '"':
                pos += 1
                while pos < end_mod:
                    c = text[pos]
                    if c == "\\" and pos + 1 < end_mod:
                        pos += 2
                        continue
                    pos += 1
                    if c == '"':
                        break
                continue

            # ── Semicolon ──
            if ch == ";":
                if in_ign_to_semi:
                    in_ign_to_semi = False
                    rvalue = False
                elif in_modport is not False and in_modport is not True:
                    if varstack:
                        saved = varstack.pop()
                        sigs_out[:] = saved[0]
                        sigs_inout[:] = saved[1]
                        sigs_in[:] = saved[2]
                    vec = None; io = False; expect_signal = None
                    newsig = None; paren = 0; rvalue = False
                    v2kargs_ok = False; in_modport = False; ign_prop = False
                else:
                    vec = None; io = False; expect_signal = None
                    newsig = None; paren = 0; rvalue = False
                    v2kargs_ok = False; in_modport = False; ign_prop = False
                pos += 1
                continue

            # ── Equals sign ──
            if ch == "=":
                rvalue = True
                newsig = None
                pos += 1
                continue

            # ── Comma at same paren level ──
            if ch == "," and paren == sig_paren:
                rvalue = False
                pos += 1
                continue

            # ── Open brace/paren ──
            if ch in ("{", "("):
                paren += 1
                pos += 1
                continue

            # ── Close brace/paren ──
            if ch in ("}", ")"):
                paren -= 1
                pos += 1
                if paren < sig_paren:
                    expect_signal = None
                    rvalue = False
                continue

            # ── Bit width [...] ──
            if ch == "[":
                bracket_end = self._find_matching_bracket(text, pos, end_mod)
                if bracket_end is not None:
                    bracket_content = text[pos:bracket_end]
                    cleaned = re.sub(r"\s+", "", bracket_content)
                    if newsig:
                        if newsig.memory:
                            newsig.memory += cleaned
                        else:
                            newsig.memory = cleaned
                    elif vec is not None:
                        if multidim is None:
                            multidim = []
                        multidim.append(vec)
                        vec = cleaned
                    else:
                        vec = cleaned
                    pos = bracket_end
                else:
                    pos += 1
                continue

            # ── Type cast: int'(a) ──
            if ch == "'" and not rvalue:
                pos += 1
                expect_signal = None
                rvalue = False
                continue

            # ── Identifier or keyword ──
            m = _WS_IDENT_RE.match(text, pos)
            if m:
                pos = m.end()
                last_keywd = keywd
                keywd = m.group(1)

                # Escaped identifiers need trailing space
                if keywd.startswith("\\"):
                    keywd = keywd + " "

                # Handle package::name syntax
                while True:
                    pm = _PACKAGE_SEP_RE.match(text, pos)
                    if not pm:
                        break
                    pos = pm.end()
                    suffix = pm.group(1)
                    keywd = keywd + "::" + suffix
                    if suffix.startswith("\\"):
                        keywd = keywd + " "

                # ── Keyword dispatch ──
                if keywd == "input":
                    vec = None; enum = None; rvalue = False; newsig = None
                    signed = None; typedefed = None; multidim = None
                    ptype = False; modport = None
                    expect_signal = "sigs_in"; io = True; sig_paren = paren

                elif keywd == "output":
                    vec = None; enum = None; rvalue = False; newsig = None
                    signed = None; typedefed = None; multidim = None
                    ptype = False; modport = None
                    expect_signal = "sigs_out"; io = True; sig_paren = paren

                elif keywd == "inout":
                    vec = None; enum = None; rvalue = False; newsig = None
                    signed = None; typedefed = None; multidim = None
                    ptype = False; modport = None
                    expect_signal = "sigs_inout"; io = True; sig_paren = paren

                elif keywd == "parameter":
                    vec = None; enum = None; rvalue = False
                    signed = None; typedefed = None; multidim = None
                    ptype = False; modport = None
                    expect_signal = "sigs_gparam"; io = True; sig_paren = paren

                elif keywd in _TYPE_KEYWORDS:
                    if io:
                        typedefed = (
                            (typedefed + " " + keywd) if typedefed else keywd
                        )
                    else:
                        vec = None; enum = None; rvalue = False; signed = None
                        typedefed = None; multidim = None; sig_paren = paren
                        expect_signal = "sigs_var"; modport = None

                elif keywd == "assign":
                    vec = None; enum = None; rvalue = False; signed = None
                    typedefed = None; multidim = None; ptype = False
                    modport = None
                    expect_signal = "sigs_assign"; sig_paren = paren

                elif keywd in ("localparam", "genvar"):
                    vec = None; enum = None; rvalue = False; signed = None
                    typedefed = None; multidim = None; ptype = False
                    modport = None
                    expect_signal = "sigs_const"; sig_paren = paren

                elif keywd in ("signed", "unsigned"):
                    signed = keywd

                elif keywd in ("assert", "assume", "cover", "expect", "restrict"):
                    ign_prop = True

                elif keywd in ("class", "covergroup", "function",
                               "property", "randsequence", "sequence", "task"):
                    if not ign_prop:
                        functask += 1

                elif keywd in ("endclass", "endgroup", "endfunction",
                               "endproperty", "endsequence", "endtask"):
                    functask -= 1

                elif keywd == "modport":
                    in_modport = True

                elif keywd == "clocking" and last_keywd != "default":
                    in_clocking = True

                elif keywd == "import":
                    if v2kargs_ok:
                        in_ign_to_semi = True
                        rvalue = True

                elif keywd == "type":
                    ptype = True

                elif keywd == "var":
                    pass

                elif keywd in ("`ifdef", "`ifndef", "`elsif"):
                    rvalue = True

                elif keywd == "`line":
                    eol = text.find("\n", pos)
                    pos = (eol + 1) if eol >= 0 else end_mod

                elif not ptype and self._typedef_name_p(keywd):
                    if io:
                        typedefed = (
                            (typedefed + " " + keywd) if typedefed else keywd
                        )
                    else:
                        vec = None; enum = None; rvalue = False; signed = None
                        typedefed = keywd; multidim = None; sig_paren = paren
                        expect_signal = "sigs_var"; modport = None

                elif (v2kargs_ok and paren == 1 and not rvalue
                      and keywd not in VERILOG_KEYWORDS
                      and _INTERFACE_AHEAD_RE.match(text, pos)
                      and not self._ahead_is_keyword(text, pos)):
                    # Interface with optional modport in v2k arglist
                    dm = _INTERFACE_DOT_RE.match(text, pos)
                    iface_modport = None
                    if dm:
                        iface_modport = dm.group(1)
                        pos = dm.end()
                    vec = None; enum = None; rvalue = False; signed = None
                    typedefed = keywd; multidim = None; ptype = False
                    modport = iface_modport
                    newsig = None; sig_paren = paren
                    expect_signal = "sigs_intf"; io = True

                elif _DOT_AHEAD_RE.match(text, pos):
                    # Dotted LHS: assign foo.bar = z;
                    dm = _DOT_AHEAD_RE.match(text, pos)
                    if dm:
                        pos = dm.end()
                    if not rvalue:
                        expect_signal = None

                elif in_modport is True and keywd not in VERILOG_KEYWORDS:
                    mp = Modport(name=keywd)
                    in_modport = mp
                    sigs_modports.append(mp)
                    varstack.append((list(sigs_out), list(sigs_inout), list(sigs_in)))
                    sigs_in.clear(); sigs_inout.clear(); sigs_out.clear()

                elif in_modport and in_clocking:
                    in_clocking = False

                elif in_clocking and keywd == "endclocking":
                    if in_clocking is not True and isinstance(in_clocking, Modport):
                        if varstack:
                            saved = varstack.pop()
                            sigs_out[:] = saved[0]
                            sigs_inout[:] = saved[1]
                            sigs_in[:] = saved[2]
                    in_clocking = False

                elif in_clocking is True and keywd not in VERILOG_KEYWORDS:
                    mp = Modport(name=keywd)
                    in_clocking = mp
                    sigs_modports.append(mp)
                    varstack.append((list(sigs_out), list(sigs_inout), list(sigs_in)))
                    sigs_in.clear(); sigs_inout.clear(); sigs_out.clear()

                elif (expect_signal and not rvalue and functask == 0
                      and keywd not in VERILOG_KEYWORDS
                      and (not io or paren == sig_paren)):
                    # ── New signal ──
                    newsig = Signal(
                        name=keywd,
                        bits=vec,
                        comment=None,
                        memory=None,
                        enum=enum,
                        signed=signed,
                        type=typedefed,
                        multidim=list(multidim) if multidim else None,
                        modport=modport,
                    )
                    target = sig_lists.get(expect_signal)
                    if target is not None:
                        target.append(newsig)

                continue

            # ── Default: unrecognized character ──
            pos += 1

        return ModDecls(
            outputs=sigs_out,
            inouts=sigs_inout,
            inputs=sigs_in,
            vars=sigs_var,
            modports=sigs_modports,
            assigns=sigs_assign,
            consts=sigs_const,
            gparams=sigs_gparam,
            interfaces=sigs_intf,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_end_defun(text: str, pos: int) -> Optional[int]:
        """Find the ``endmodule`` etc. position from *pos*."""
        m = _END_DEFUN_RE.search(text, pos)
        return m.end() if m else None

    @staticmethod
    def _find_beg_defun(text: str, pos: int) -> int:
        """Find the ``module`` etc. keyword position before *pos*."""
        last_match = None
        for m in _DEFUN_RE.finditer(text, 0, pos + 1):
            last_match = m
        return last_match.start() if last_match else 0

    @staticmethod
    def _find_matching_bracket(text: str, pos: int, limit: int) -> Optional[int]:
        """Find the matching ``]`` for ``[`` at *pos*. Return pos after ``]``."""
        if pos >= len(text) or text[pos] != "[":
            return None
        depth = 0
        i = pos
        while i < limit and i < len(text):
            c = text[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None

    @staticmethod
    def _ahead_is_keyword(text: str, pos: int) -> bool:
        """True if the next identifier after whitespace is a Verilog keyword.

        Used to prevent the interface-ahead check from matching keywords
        like ``output`` or ``input`` as instance names.
        """
        m = _WS_RE.match(text, pos)
        p = m.end() if m else pos
        if p >= len(text):
            return False
        # '#' or '.' indicate interface parameterization/modport — not a keyword
        if text[p] in ("#", "."):
            return False
        im = _IDENT_RE.match(text, p)
        if im:
            return im.group(0) in VERILOG_KEYWORDS
        return False

    def _typedef_name_p(self, name: str) -> bool:
        """Return True if *name* matches a typedef regexp."""
        if self._config.typedef_regexp:
            try:
                return bool(re.search(self._config.typedef_regexp, name))
            except re.error:
                return False
        return False

    @staticmethod
    def _read_auto_constants(text: str, beg: int, end: int) -> list[Signal]:
        """Read AUTO_CONSTANT declarations between *beg* and *end*."""
        result: list[Signal] = []
        for m in re.finditer(r"\bAUTO_CONSTANT\s*\(([^)]*)\)", text[beg:end]):
            inner = m.group(1)
            for cm in re.finditer(r"\s*([\"a-zA-Z0-9$_.%`]+)\s*,?", inner):
                name = cm.group(1).strip('"')
                result.append(Signal(name=name))
        return result
