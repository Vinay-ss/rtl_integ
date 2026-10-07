"""AutoEngine — main dispatch loop for AUTO expansion.

Ported from ``verilog-auto`` (lines 14799–14985) and
``verilog-auto-re-search-do`` (around line 14800) of ``verilog-mode.el``.

Phase 5 complete dispatch: AUTOINST, AUTOINSTPARAM, AUTOWIRE, AUTOLOGIC,
AUTOARG, AUTOINPUT, AUTOOUTPUT, AUTOINOUT, AUTOINOUTMODULE,
AUTOINOUTCOMP, AUTOINOUTIN, AUTOINOUTPARAM, AUTOREG, AUTOREGINPUT,
AUTOTIEOFF, AUTOOUTPUTEVERY, AUTOSTAR, AUTOASCIIENUM,
AUTOINOUTMODPORT, AUTOASSIGNMODPORT, AUTOUNDEF, AUTOUNUSED,
AUTOSENSE, AUTORESET, AUTOARG.
"""

from __future__ import annotations

import os
import re
import warnings
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from ..buffer import VerilogBuffer
    from ..config import VerilogConfig


class AutoEngine:
    """Drives AUTO expansion / deletion / injection on a single file."""

    def __init__(
        self,
        config: "VerilogConfig | None" = None,
        db_factory: "Callable[[VerilogConfig, str], object] | None" = None,
    ) -> None:
        from ..config import VerilogConfig

        self.config = config or VerilogConfig()
        # Optional factory ``(config, current_file) -> ModuleDatabase`` used by
        # design integration to share a pyslang-backed module index.  ``None``
        # keeps the classic per-file ``ModuleDatabase`` (golden behavior).
        self._db_factory = db_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        buf: "VerilogBuffer",
        config: "VerilogConfig | None" = None,
        inject: bool = False,
    ) -> None:
        """Main AUTO expansion.  Mirrors the elisp dispatch order exactly.

        Port of ``verilog-auto``.
        """
        from ..auto.delete import AutoDeleter
        from ..auto.inst import AutoInst, AutoInstParam
        from ..auto.wire import AutoLogic, AutoWire
        from ..library.module_db import ModuleDatabase
        from ..local_vars import apply_local_vars, parse_local_vars

        cfg = config or self.config

        # Apply per-file local variables
        local_vars = parse_local_vars(buf)
        if local_vars:
            cfg = apply_local_vars(cfg, local_vars)

        # Parse getopt flags from library_flags
        if cfg.library_flags and any(cfg.library_flags):
            from ..library.getopt import VerilogGetopt

            getopt = VerilogGetopt(cfg)
            for flagset in cfg.library_flags:
                if flagset:
                    cfg = getopt.parse_flags(flagset.split())

        # Add current file directory to library search
        if buf.filepath:
            file_dir = os.path.dirname(os.path.abspath(buf.filepath))
            if file_dir not in cfg.library_directories:
                cfg.library_directories = [file_dir] + list(cfg.library_directories)

        # Scan buffer for `define / `undef / parameter values
        from ..parser.defines_parser import DefinesParser

        dp = DefinesParser(cfg)
        if buf.filepath and cfg.auto_read_includes:
            # Follow `include directives to pick up defines/parameters
            defs = dp.parse_file(buf.filepath, recurse=True)
        else:
            # When auto_read_includes is off, only read parameters —
            # not `define macros.  This matches the Emacs behaviour
            # where verilog-read-defines is only invoked when
            # verilog-auto-read-includes is set.
            defs = dp.parse_buffer(buf, include_macros=False)
        for k, v in defs.items():
            if k not in cfg.defines:
                cfg.defines[k] = v
            # Also store with vh- prefix (Emacs convention for parameters)
            vh_key = f"vh-{k}"
            if vh_key not in cfg.defines:
                cfg.defines[vh_key] = v

        # Build module database (or the design-level one when integrating)
        db = (self._db_factory or ModuleDatabase)(cfg, buf.filepath or ".")

        # ----------------------------------------------------------
        # Step 0: AUTO_LISP handling
        #
        # Port of verilog-read-auto-lisp which evaluates from
        # point-min to point for each AUTO handler.
        #
        # We store a flag on the engine; _search_do will call
        # _eval_auto_lisp_to_pos() before each handler to give
        # position-dependent variable values.
        # ----------------------------------------------------------
        from ..auto.lisp_eval import AutoLispEval

        lisp = AutoLispEval()
        self._lisp = lisp
        text = buf.buffer_string()
        self._has_auto_lisp = "AUTO_LISP" in text

        # Save config defaults BEFORE any AUTO_LISP modifies them,
        # so positional evaluation can restore to baseline.
        from dataclasses import fields as dc_fields

        self._cfg_defaults = {f.name: getattr(cfg, f.name) for f in dc_fields(cfg)}

        # Initial full evaluation (for config-level setq's like
        # verilog-auto-inst-param-value that affect delete/setup)
        if self._has_auto_lisp:
            lisp.eval_auto_lisp_block(text)
            _apply_lisp_env_to_config(cfg, lisp)
            for k, v in lisp.env.items():
                if isinstance(v, (str, int, float)):
                    cfg.defines[k] = str(v)
                elif isinstance(v, tuple) and len(v) == 2:
                    params, body = v
                    if isinstance(params, list):
                        cfg.user_functions[k] = (params, body)
        # Store config reference for _search_do to update
        self._cfg = cfg

        # ----------------------------------------------------------
        # Step 1: Setup AUTOLOGIC (sets auto_wire_type to "logic")
        # Must happen before delete so we know the wire type
        # ----------------------------------------------------------
        self._search_do(buf, r"/\*AUTOLOGIC\*/", lambda: _autologic_setup(cfg))

        # ----------------------------------------------------------
        # Step 2: Delete previous AUTO expansions
        # ----------------------------------------------------------
        deleter = AutoDeleter(buf, cfg)
        deleter.delete()

        # ----------------------------------------------------------
        # Step 2b: Inject AUTO markers (if inject mode)
        # ----------------------------------------------------------
        if inject:
            from ..auto.inject import AutoInjector

            injector = AutoInjector(buf, cfg, db)
            injector.inject_inst()
            injector.inject_sense()
            injector.inject_arg()

        # ----------------------------------------------------------
        # Step 3: Expansion — order matters!
        # Matches the exact dispatch order from verilog-auto
        # ----------------------------------------------------------

        # Re-check for AUTO_LISP after delete (buffer may have changed)
        self._has_auto_lisp = "AUTO_LISP" in buf.buffer_string()

        # AUTOINSERTLISP
        self._search_do(
            buf,
            r"/\*AUTOINSERTLISP\(.*?\)\*/",
            lambda: self._handle_autoinsertlisp(buf, cfg, lisp),
        )

        # AUTOINSTPARAM
        self._search_do(
            buf,
            r"/\*AUTOINSTPARAM(?:\(.*?\))?\*/",
            lambda: AutoInstParam(buf, cfg, db).expand(),
        )

        # AUTOINST
        self._search_do(
            buf,
            r"/\*AUTOINST(?:\(.*?\))?\*/",
            lambda: AutoInst(buf, cfg, db).expand(),
        )

        # AUTOSTAR (.*)
        from ..auto.star import AutoStar

        self._search_do(
            buf,
            r"\.\*",
            lambda: AutoStar(buf, cfg, db).expand(),
        )

        # AUTOASCIIENUM
        from ..auto.ascii_enum import AutoAsciiEnum

        self._search_do(
            buf,
            r"/\*AUTOASCIIENUM\(.*?\)\*/",
            lambda: AutoAsciiEnum(buf, cfg, db).expand(),
        )

        # AUTOINOUTMODPORT
        from ..auto.modport import AutoInoutModport

        self._search_do(
            buf,
            r"/\*AUTOINOUTMODPORT\(.*?\)\*/",
            lambda: AutoInoutModport(buf, cfg, db).expand(),
        )

        # AUTOINOUTMODULE
        from ..auto.inout import (
            AutoInoutComp,
            AutoInoutIn,
            AutoInoutModule,
            AutoInoutParam,
        )

        self._search_do(
            buf,
            r"/\*AUTOINOUTMODULE\b.*?\*/",
            lambda: AutoInoutModule(buf, cfg, db).expand(),
        )

        # AUTOINOUTCOMP
        self._search_do(
            buf,
            r"/\*AUTOINOUTCOMP\b.*?\*/",
            lambda: AutoInoutComp(buf, cfg, db).expand(),
        )

        # AUTOINOUTIN
        self._search_do(
            buf,
            r"/\*AUTOINOUTIN\b.*?\*/",
            lambda: AutoInoutIn(buf, cfg, db).expand(),
        )

        # AUTOINOUTPARAM
        self._search_do(
            buf,
            r"/\*AUTOINOUTPARAM\b.*?\*/",
            lambda: AutoInoutParam(buf, cfg, db).expand(),
        )

        # AUTOOUTPUT
        from ..auto.io import AutoInout, AutoInput, AutoOutput

        self._search_do(
            buf,
            r"/\*AUTOOUTPUT(?:\(.*?\))?\*/",
            lambda: AutoOutput(buf, cfg, db).expand(),
        )

        # AUTOINPUT
        self._search_do(
            buf,
            r"/\*AUTOINPUT(?:\(.*?\))?\*/",
            lambda: AutoInput(buf, cfg, db).expand(),
        )

        # AUTOINOUT (must come after AUTOINOUTMODULE etc.)
        self._search_do(
            buf,
            r"/\*AUTOINOUT(?:\(.*?\))?\*/",
            lambda: AutoInout(buf, cfg, db).expand(),
        )

        # AUTOTIEOFF
        from ..auto.tieoff import AutoTieoff

        self._search_do(
            buf,
            r"/\*AUTOTIEOFF\*/",
            lambda: AutoTieoff(buf, cfg, db).expand(),
        )

        # AUTOUNDEF
        from ..auto.undef import AutoUndef

        self._search_do(
            buf,
            r"/\*AUTOUNDEF(?:\(.*?\))?\*/",
            lambda: AutoUndef(buf, cfg, db).expand(),
        )

        # AUTOASSIGNMODPORT
        from ..auto.modport import AutoAssignModport

        self._search_do(
            buf,
            r"/\*AUTOASSIGNMODPORT\(.*?\)\*/",
            lambda: AutoAssignModport(buf, cfg, db).expand(),
        )

        # AUTOLOGIC
        self._search_do(
            buf,
            r"/\*AUTOLOGIC\*/",
            lambda: AutoLogic(buf, cfg, db).expand(),
        )

        # AUTOWIRE
        self._search_do(
            buf,
            r"/\*AUTOWIRE\*/",
            lambda: AutoWire(buf, cfg, db).expand(),
        )

        # AUTOREG
        from ..auto.reg import AutoReg, AutoRegInput

        self._search_do(
            buf,
            r"/\*AUTOREG\*/",
            lambda: AutoReg(buf, cfg, db).expand(),
        )

        # AUTOREGINPUT
        self._search_do(
            buf,
            r"/\*AUTOREGINPUT\*/",
            lambda: AutoRegInput(buf, cfg, db).expand(),
        )

        # AUTOOUTPUTEVERY
        from ..auto.io import AutoOutputEvery

        self._search_do(
            buf,
            r"/\*AUTOOUTPUTEVERY(?:\(.*?\))?\*/",
            lambda: AutoOutputEvery(buf, cfg, db).expand(),
        )

        # AUTOSENSE / AS
        from ..auto.sense import AutoSense

        self._search_do(
            buf,
            r"/\*(?:AUTOSENSE|AS)\*/",
            lambda: AutoSense(buf, cfg, db).expand(),
        )

        # AUTORESET
        from ..auto.reset import AutoReset

        self._search_do(
            buf,
            r"/\*AUTORESET\*/",
            lambda: AutoReset(buf, cfg, db).expand(),
        )

        # AUTOUNUSED
        from ..auto.tieoff import AutoUnused

        self._search_do(
            buf,
            r"/\*AUTOUNUSED\*/",
            lambda: AutoUnused(buf, cfg, db).expand(),
        )

        # AUTOARG (last before AUTOINSERTLAST)
        from ..auto.arg import AutoArg

        self._search_do(
            buf,
            r"/\*AUTOARG\*/",
            lambda: AutoArg(buf, cfg, db).expand(),
        )

        # AUTOINSERTLAST (stub)
        self._search_do(
            buf,
            r"/\*AUTOINSERTLAST\(.*?\)\*/",
            lambda: warnings.warn("AUTOINSERTLAST not yet supported", stacklevel=2),
        )

        # ----------------------------------------------------------
        # Step 4: Star cleanup — remove implicit .* pins if not saving
        # Port of verilog-star-cleanup
        # TODO: Enable once delete_auto_star handles all edge cases
        # ----------------------------------------------------------
        # if not cfg.auto_star_save:
        #     AutoStar(buf, cfg, db).delete()

        # ----------------------------------------------------------
        # Step 5: instance lineup / port comments — after ALL AUTO
        # expansions so the elisp order above is unchanged.  Off by
        # default (Emacs goldens stay byte-identical).
        # ----------------------------------------------------------
        if cfg.auto_inst_lineup or cfg.auto_inst_port_comment:
            self._lineup_instances(buf, cfg, db)

    # ------------------------------------------------------------------
    # Instance lineup (auto/inst_lineup.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _lineup_instances(buf: "VerilogBuffer", cfg: "VerilogConfig", db) -> None:
        """Run :func:`lineup_instances` over the whole buffer.

        The lookup mirrors ``AutoInst`` (``db.lookup(name, ignore_error=True)``
        then ``db.get_decls``); results are cached and any lookup error
        yields ``None`` (the instance is then only aligned).
        """
        from .inst_lineup import lineup_instances

        cache: dict = {}

        def lookup(name: str):
            if name in cache:
                return cache[name]
            decls = None
            try:
                modi = db.lookup(name, ignore_error=True)
                if modi is not None:
                    decls = db.get_decls(modi)
            except Exception:
                decls = None
            cache[name] = decls
            return decls

        text = buf.buffer_string()
        new = lineup_instances(text, lookup, cfg)
        if new == text:
            return
        point = buf.point()
        buf.widen()
        buf.delete_region(0, len(text))
        buf.goto_char(0)
        buf.insert(new)
        buf.goto_char(min(point, len(new)))

    # ------------------------------------------------------------------
    # AUTOINSERTLISP handler
    # ------------------------------------------------------------------

    def _handle_autoinsertlisp(
        self,
        buf: "VerilogBuffer",
        cfg: "VerilogConfig",
        lisp: "AutoLispEval",
    ) -> None:
        """Evaluate the AUTOINSERTLISP expression and insert the result.

        Port of the AUTOINSERTLISP dispatch in ``verilog-auto``.
        The expression is extracted from the ``/*AUTOINSERTLISP(expr)*/``
        marker that was just matched (the point is right after the match).
        """
        # Find the AUTOINSERTLISP marker we just matched by scanning back
        text = buf.buffer_string()
        pt = buf.point()
        # Search backward for the marker
        marker_re = re.compile(r"/\*AUTOINSERTLISP\((.*?)\)\*/", re.DOTALL)
        # Find the most recent match ending at or before pt
        best = None
        for m in marker_re.finditer(text):
            if m.end() <= pt + 1:
                best = m
        if best is None:
            return

        expr = best.group(1).strip()
        if not expr:
            return

        # Make user_functions available to the lisp evaluator
        for fname, fdef in cfg.user_functions.items():
            lisp.env[fname] = fdef
        # Also make defines available
        for k, v in cfg.defines.items():
            if k not in lisp.env:
                lisp.env[k] = v

        # AUTOINSERTLISP expression is a Lisp form — wrap it in
        # parens if not already an s-expression, since the marker
        # syntax is AUTOINSERTLISP(expr) where expr is already the
        # body of a (expr) call.
        eval_expr = expr if expr.startswith("(") else f"({expr})"

        # In Emacs, AUTOINSERTLISP is evaluated for side effects:
        # (insert ...) calls inject text into the buffer.
        # We capture "inserted" text via lisp._insert_buffer.
        lisp._insert_buffer = []
        lisp.eval(eval_expr)
        text_to_insert = "".join(lisp._insert_buffer)
        lisp._insert_buffer = []

        if not text_to_insert:
            return

        # Insert after the marker (point is already positioned there)
        indent = self._get_line_indent(text, best.start())
        lines = text_to_insert.rstrip("\n").split("\n")
        insertion = "\n"
        insertion += f"{indent}// Beginning of automatic insert lisp\n"
        for line in lines:
            insertion += f"{indent}{line}\n"
        insertion += f"{indent}// End of automatics"
        buf.insert(insertion)

    @staticmethod
    def _get_line_indent(text: str, pos: int) -> str:
        """Return the whitespace prefix of the line containing *pos*."""
        line_start = text.rfind("\n", 0, pos)
        line_start = 0 if line_start < 0 else line_start + 1
        line_end = text.find("\n", line_start)
        if line_end < 0:
            line_end = len(text)
        line = text[line_start:line_end]
        return line[: len(line) - len(line.lstrip())]

    # ------------------------------------------------------------------
    # Search-and-dispatch (port of verilog-auto-re-search-do)
    # ------------------------------------------------------------------

    def _search_do(
        self,
        buf: "VerilogBuffer",
        pattern: str,
        handler: Callable,
    ) -> None:
        """Find all matches of *pattern* and call *handler* at each
        match point.

        Port of ``verilog-auto-re-search-do``.
        Skips matches inside ``//`` line comments.
        Before each handler call, evaluates AUTO_LISP blocks up to
        the current position (position-dependent evaluation).
        """
        compiled = re.compile(pattern, re.IGNORECASE)

        buf.goto_char(buf.point_min())
        while True:
            text = buf.buffer_string()
            pos = buf.point()
            m = compiled.search(text, pos)
            if m is None:
                break
            # Skip matches inside // line comments
            line_start = text.rfind("\n", 0, m.start())
            line_start = 0 if line_start < 0 else line_start + 1
            line_prefix = text[line_start:m.start()]
            if "//" in line_prefix:
                buf.goto_char(m.end())
                continue
            # Position point right after the match
            buf.goto_char(m.end())
            # Evaluate AUTO_LISP from buffer start to this position
            # (port of verilog-read-auto-lisp (point-min) pt)
            if getattr(self, "_has_auto_lisp", False):
                self._eval_auto_lisp_to_pos(buf, m.start())
            handler()

    def _eval_auto_lisp_to_pos(
        self,
        buf: "VerilogBuffer",
        pos: int,
    ) -> None:
        """Evaluate all AUTO_LISP blocks from buffer start to *pos*.

        Resets the lisp evaluator and re-evaluates from scratch each
        time, exactly like the elisp ``verilog-read-auto-lisp``
        which calls ``(goto-char start) ... (while (re-search-forward
        ... end t) (eval-region ...))``.
        """
        text = buf.buffer_string()
        region = text[:pos]
        self._lisp._env.clear()
        for m in re.finditer(r"\bAUTO_LISP\s*\(", region):
            start = m.end() - 1
            end = self._lisp._find_matching_paren(region, start)
            if end is not None:
                self._lisp._eval_inner(region[start:end])
        # Restore config fields to defaults, then apply lisp env.
        # This ensures position-dependent config changes are correct.
        cfg = self._cfg
        for field_name, default_val in self._cfg_defaults.items():
            setattr(cfg, field_name, default_val)
        _apply_lisp_env_to_config(cfg, self._lisp)
        for k, v in self._lisp.env.items():
            if isinstance(v, (str, int, float)):
                cfg.defines[k] = str(v)
            elif isinstance(v, tuple) and len(v) == 2:
                params, body = v
                if isinstance(params, list):
                    cfg.user_functions[k] = (params, body)


def _autologic_setup(cfg: "VerilogConfig") -> None:
    """Port of ``verilog-auto-logic-setup``."""
    if not cfg.auto_wire_type:
        cfg.auto_wire_type = "logic"


def _apply_lisp_env_to_config(cfg: "VerilogConfig", lisp: "AutoLispEval") -> None:
    """Map AUTO_LISP setq'd verilog-* variables to config fields."""
    from ..local_vars import _elisp_name_to_field, _parse_elisp_value

    _BOOL_FIELDS = {
        "auto_inst_sort", "auto_inst_param_value", "auto_inst_dot_name",
        "auto_inst_interfaced_ports", "auto_inst_template_required",
        "auto_arg_sort", "auto_sense_include_inputs",
        "auto_reset_blocking_in_non", "auto_wire_comment",
        "auto_star_expand", "auto_star_save", "auto_read_includes",
        "auto_ignore_concat", "auto_simplify_expressions",
        "case_fold", "auto_inst_lineup",
    }
    _STR_FIELDS = {
        "auto_wire_type", "auto_declare_nettype", "auto_tieoff_declaration",
        "auto_inst_template_numbers", "typedef_regexp",
        "auto_tieoff_ignore_regexp", "auto_unused_ignore_regexp",
        "auto_input_ignore_regexp", "auto_output_ignore_regexp",
        "auto_inout_ignore_regexp", "assignment_delay",
        "auto_inst_port_comment",
    }
    _INT_FIELDS = {
        "auto_inst_column", "indent_level", "indent_level_module",
        "indent_level_declaration", "indent_level_behavioral",
        "case_indent", "cexp_indent", "auto_inst_comment_column",
    }

    for name, val in lisp.env.items():
        py_field = _elisp_name_to_field(name)
        if not hasattr(cfg, py_field):
            continue

        if py_field in _BOOL_FIELDS:
            if val is None or val is False or val == "nil":
                setattr(cfg, py_field, False)
            else:
                setattr(cfg, py_field, True)
        elif py_field in _STR_FIELDS:
            if val is None or val == "nil":
                setattr(cfg, py_field, None)
            else:
                setattr(cfg, py_field, str(val))
        elif py_field in _INT_FIELDS:
            try:
                setattr(cfg, py_field, int(val))
            except (ValueError, TypeError):
                pass
        elif py_field in ("auto_inst_vector", "auto_reset_widths"):
            # Object fields: True, None, or string
            if val is None or val is False or val == "nil":
                setattr(cfg, py_field, None)
            elif val is True or val == "t":
                setattr(cfg, py_field, True)
            else:
                setattr(cfg, py_field, str(val))
