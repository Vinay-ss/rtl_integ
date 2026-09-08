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
├── integ/             # design integration: filelist, hierarchy, leaf-first expansion, routing
│   ├── filelist.py    #   -f filelist parser -> Filelist; filelist_to_config()
│   ├── sources.py     #   SourceFile: bytes/text/version + byte<->char offset maps
│   ├── model.py       #   SrcRange, ModuleDef, ModuleRef, PortInfo, Instance, Diag
│   ├── graph.py       #   file DAG, Tarjan SCC, leaf-first Order
│   ├── textscan.py    #   markers/fences/pins/instantiations by regex (both backends)
│   ├── frontend_text.py   # backend without pyslang
│   ├── frontend_slang.py  # pyslang backend: SourceLoader, CST scan, elaboration, port_at
│   ├── reader.py      #   SlangModuleReader: CST -> ModDecls (DeclParser parity)
│   ├── database.py    #   DesignModuleDatabase / SlangReaderDatabase (ModuleDatabase subclasses)
│   ├── design.py      #   Design facade
│   ├── orchestrator.py    # Integrator: leaf-first AutoEngine runs with overlay/refresh
│   ├── edits.py       #   char-planned, byte-applied text edits (CRLF safe, fence guard)
│   ├── route.py       #   RouteSpec, endpoint grammar, backref pairing (pure)
│   ├── route_plan.py  #   RoutePlanner: LCA/chains, naming, AUTO-native vs explicit, edits
│   ├── routes_file.py #   TOML/JSON routes files
│   ├── auto_route.py  #   //auto_route PORT :: to|from :: TARGETS annotations -> RouteSpecs + routes.toml writer
│   └── cli_cmds.py    #   hierarchy / integrate / route handlers
└── cli.py             # argparse CLI
```

Design integration notes:
- `AutoEngine(config, db_factory=None)`: the factory is the only engine hook; `None` keeps golden behavior.
- The pyslang reader is CST-driven (never elaborated symbols): AUTOARG-style leaves elaborate as `module m ();`
  and evaluated types lose `[WIDTH-1:0]`. Widths are sliced from the file bytes by token offsets.
- Modules containing `` `ifdef``/`` `include`` or parse errors fall back to `DeclParser` (Emacs sees both branches).
- pyslang byte offsets index `SourceFile.data`; `VerilogBuffer` text is LF-normalized; convert with
  `SourceFile.char_offset/byte_offset`. Re-parses use a versioned buffer path (`path#vN`).
- Routing edits never touch AUTO fences; renames are explicit pins before `/*AUTOINST*/`, not AUTO_TEMPLATE.
- Tests: `tests/test_integ_*.py`, `tests/test_route_*.py`, `tests/test_routing.py`; fixtures in `tests/integ/`
  (goldens under `expected/`). Baseline golden failures (35, pre-existing) are listed in the plan file.
- `sample_env/route_demo/`: runnable routing walk-through (`run_route_demo.sh|.bat`, sources in `src/`,
  scratch `work/` is gitignored); `tests/test_route_demo.py` exercises it.

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
Phase: 6 — Design integration (pyslang front-end, leaf-first expansion, routing API) implemented
Status: `hierarchy` / `integrate` / `route` CLI + `pyverilog_auto.integ.Design` API; pyslang optional extra `[integ]`
Baseline: 35 pre-existing golden failures (19 in test_golden.py, 16 in test_golden_autoinst.py); everything else green
