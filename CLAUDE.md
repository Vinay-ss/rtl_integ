# pyverilog-auto Project

## What this project is
Converting `verilog-mode.el` (15,690-line Emacs Lisp Verilog editing mode) to
`pyverilog_auto/` — a standalone Python CLI package that runs Verilog AUTO
code generation without Emacs.

Source: `verilog-mode.el` (this directory)
Full plan + implementation prompts: `CONVERSION_PROMPTS.md` (this directory)

## CLI target
```
pyverilog-auto expand  -y rtl/ top.v   # expand AUTOs in place
pyverilog-auto delete  top.v           # strip AUTO sections
pyverilog-auto inject  top.v           # add AUTO markers
pyverilog-auto indent  top.v           # re-indent
pyverilog-auto diff    top.v           # preview changes
```

## Package layout
```
pyverilog_auto/
├── buffer.py          # VerilogBuffer — Emacs buffer emulation
├── scanner.py         # Comment/string region scanner
├── regex_compat.py    # Emacs→Python regex translation
├── config.py          # VerilogConfig dataclass
├── local_vars.py      # "// Local Variables:" parser
├── signal.py          # Signal, ModDecls, SubDecls, Modi dataclasses
├── parser/            # decl_parser, inst_parser, defines_parser, etc.
├── library/           # module_db, resolver, getopt
├── auto/              # engine + one file per AUTO type
├── indent/            # indentation engine
└── cli.py             # argparse CLI
```

## Test suite
- `tests/` — 483 Verilog input files
- `tests_ok/` — 483 golden output files (ground truth)
- `tests_batch_ok/` — golden indented files
- Run: `pytest tests/`

Python is managed by uv in C:\Users\vinay\.local\bin

## Key design decisions (do not relitigate)
1. `VerilogBuffer` wraps a mutable char list + integer `point` cursor,
   mimicking Emacs buffer primitives as methods.
2. `translate_emacs_regex()` in `regex_compat.py` converts Emacs regex
   syntax before compiling — all patterns go through this.
3. `VerilogConfig` is a dataclass with one instance per file (not global).
4. Module cache key: `(filepath, os.path.getmtime(filepath))`.
5. AUTO expansion order mirrors the elisp exactly (order matters).
6. Emacs UI features (font-lock, menus, keybindings) are NOT ported.

## Current phase
<!-- Update this line at the start of each phase -->
Phase: 2 — Parser Core (complete)
Status: All 8 deliverables implemented and tested
Last test run: 113 passed, 1 skipped (ExampUndef.v has no module keyword)
