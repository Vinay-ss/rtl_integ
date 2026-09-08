# pyverilog-auto

Standalone Python tool for Verilog AUTO code generation — a port of Emacs
[verilog-mode](https://www.veripool.org/verilog-mode/)'s AUTO expansion
engine.

Runs `/*AUTOINST*/`, `/*AUTOWIRE*/`, `/*AUTOINPUT*/`, and all other AUTO
markers without requiring Emacs.

## Installation

```bash
pip install .
```

Or for development:

```bash
pip install -e .
```

Requires Python 3.10+. No external dependencies.

Optional extras: `pip install -e ".[integ]"` adds pyslang for the design
integration commands (`hierarchy`, `integrate`, `route`); see
[Design integration](#design-integration-filelist-hierarchy-leaf-first-expansion).

## CLI Usage

```
pyverilog-auto <command> [options] FILE...
```

### Commands

| Command   | Description                                    |
|-----------|------------------------------------------------|
| `expand`  | Run AUTO expansion in place                    |
| `delete`  | Strip AUTO-generated sections                  |
| `inject`  | Add AUTO markers to instances that lack them   |
| `diff`    | Preview what `expand` would change (unified diff) |
| `indent`  | Re-indent Verilog files                        |

### Common Options

| Flag                 | Description                              |
|----------------------|------------------------------------------|
| `-y DIR`             | Add library directory                    |
| `-v FILE`            | Add library file                         |
| `-f FILE`            | Read flags from file (`-f` / `-F` format)|
| `-I DIR`             | Add include/library directory            |
| `--libext .v+.sv`    | Library file extensions                  |
| `--no-save`          | Print result to stdout instead of writing|
| `--warn-fatal`       | Treat warnings as errors                 |

### Indent-Specific Options

| Flag                    | Description                     |
|-------------------------|---------------------------------|
| `--indent-level N`      | Override indent level (default: 3) |
| `--align-declarations`  | Also align declaration columns  |

### Examples

```bash
# Expand AUTOs in top.v, searching rtl/ for submodules
pyverilog-auto expand -y rtl/ top.v

# Expand multiple files
pyverilog-auto expand -y lib/ -y rtl/ top.v sub.v

# Preview changes without modifying files
pyverilog-auto diff -y rtl/ top.v

# Strip all AUTO-generated code
pyverilog-auto delete top.v

# Re-indent a file
pyverilog-auto indent top.v
```

## Supported AUTO Types

| Marker                 | Description                                          |
|------------------------|------------------------------------------------------|
| `/*AUTOINST*/`         | Instantiation port connections                       |
| `/*AUTOINSTPARAM*/`    | Instantiation parameter connections                  |
| `/*AUTOWIRE*/`         | Wire declarations for undeclared instance outputs     |
| `/*AUTOLOGIC*/`        | Same as AUTOWIRE but declares `logic` type            |
| `/*AUTOREG*/`          | Reg declarations for undeclared outputs               |
| `/*AUTOINPUT*/`        | Input declarations from unused instance inputs        |
| `/*AUTOOUTPUT*/`       | Output declarations from unused instance outputs      |
| `/*AUTOINOUT*/`        | Inout declarations from unused instance inouts        |
| `/*AUTOOUTPUTEVERY*/`  | Output declarations for every assigned signal         |
| `/*AUTOARG*/`          | Module argument list (port names)                     |
| `/*AUTOSENSE*/` / `/*AS*/` | Sensitivity list for `always` blocks             |
| `/*AUTORESET*/`        | Reset assignments in `always` blocks                  |
| `/*AUTOTIEOFF*/`       | Tie-off assignments for unused outputs                |
| `/*AUTOUNUSED*/`       | Unused signal aggregation                             |
| `/*AUTOASCII_ENUM*/`   | ASCII decode of enum/parameter values                 |
| `/*AUTOINOUTMODULE*/`  | Copy all ports from another module                    |
| `/*AUTOINOUTCOMP*/`    | Complemented port directions from another module      |
| `/*AUTOINOUTPARAM*/`   | Copy parameters from another module                   |
| `/*AUTOMODPORT*/`      | Interface modport signal declarations                 |
| `/*AUTOINSERTLAST*/`   | Insert text at end of module (stub)                   |

## Configuration

### Local Variables

Configuration is set per-file using Emacs-style local variable blocks at the
end of the file:

```verilog
// Local Variables:
// verilog-auto-wire-type: "logic"
// verilog-library-directories: ("." "../rtl")
// verilog-auto-inst-sort: t
// verilog-auto-inst-param-value: t
// End:
```

### Supported Configuration Variables

| Variable                              | Default   | Description                                     |
|---------------------------------------|-----------|--------------------------------------------------|
| `verilog-auto-wire-type`              | `nil`     | Type for AUTOWIRE (`"wire"`, `"logic"`)          |
| `verilog-auto-inst-sort`              | `nil`     | Sort AUTOINST ports alphabetically               |
| `verilog-auto-inst-param-value`       | `nil`     | Include parameter values in AUTOINSTPARAM        |
| `verilog-auto-inst-dot-name`          | `nil`     | Use `.name` shorthand for matching connections   |
| `verilog-auto-inst-vector`            | `t`       | Include bit ranges in AUTOINST connections       |
| `verilog-auto-inst-column`            | `40`      | Column for AUTOINST comment alignment            |
| `verilog-auto-inst-template-numbers`  | `nil`     | Show template match info (`"lhs"`, `"lsb"`)     |
| `verilog-auto-arg-sort`               | `nil`     | Sort AUTOARG signals alphabetically              |
| `verilog-auto-arg-format`             | `"packed"`| AUTOARG format (`"packed"` or `"single"`)        |
| `verilog-auto-sense-include-inputs`   | `nil`     | Include inputs in AUTOSENSE lists                |
| `verilog-auto-read-includes`          | `nil`     | Parse `include files for defines                 |
| `verilog-auto-simplify-expressions`   | `t`       | Simplify range arithmetic expressions            |
| `verilog-auto-wire-comment`           | `t`       | Add `// From ...` comments to declarations       |
| `verilog-library-directories`         | `(".")`   | Directories to search for modules                |
| `verilog-library-files`               | `()`      | Explicit library files                           |
| `verilog-library-extensions`          | `(".v")`  | File extensions for library search               |
| `verilog-typedef-regexp`              | `nil`     | Regexp for user-defined types                    |

### AUTO Templates

Templates customize signal renaming in `/*AUTOINST*/` expansions:

```verilog
/* sub_i AUTO_TEMPLATE (
     .port_\(.*\)  (sig_\1[]),
   ); */

sub sub_i (/*AUTOINST*/);
```

Templates support Emacs-style regex with `\(` grouping and `\1`
back-references.

## Library Search

The tool locates submodule definitions using:

1. **`-y DIR`** — search directory for `<module>.v` files
2. **`-v FILE`** — use a specific library file
3. **`-f FILE`** — read `-y` / `-v` / `-f` flags from a flag file
4. **`-I DIR`** — include/library directory

Library search also respects `verilog-library-directories`,
`verilog-library-files`, and `verilog-library-extensions` set in
local variables.

## Python API

```python
from pyverilog_auto.auto.engine import AutoEngine
from pyverilog_auto.buffer import VerilogBuffer
from pyverilog_auto.config import VerilogConfig

# Configure
config = VerilogConfig(
    library_directories=[".", "rtl/"],
)

# Load and expand
buf = VerilogBuffer.from_file("top.v")
engine = AutoEngine(config)
engine.run(buf, config)

# Get result
result = buf.buffer_string()

# Or write back
buf.write_to_file()
```

### Delete AUTO sections

```python
from pyverilog_auto.auto.delete import AutoDeleter

buf = VerilogBuffer.from_file("top.v")
deleter = AutoDeleter(buf, config)
deleter.delete()
```

### Inject mode

```python
engine.run(buf, config, inject=True)
```

## Design integration (filelist, hierarchy, leaf-first expansion)

The single-file commands above expand one file at a time. A parent's
`/*AUTOINST*/` and `/*AUTOOUTPUT*/` only see the ports that its children
have *after* their own expansion, so on a real design the files must be
processed leaves first. The `hierarchy` and `integrate` commands do that
from a filelist.

Install the optional pyslang front-end for exact instance paths (generate
blocks, instance arrays) and for the routing API:

```bash
uv pip install -e ".[integ]"        # pyslang >= 11, < 12
```

Without pyslang a text backend handles filelists, hierarchy and ordering
(`--no-slang` forces it; `PYVERILOG_AUTO_NO_SLANG=1` disables pyslang globally).

### Filelist format

Standard `-f` dialect: one token or more per line, `//` `#` `/* */` comments,
quoted paths, `\` line continuation, `$VAR` expansion, and:

```
+incdir+src/inc          # or -I DIR
+define+SIM=1+FAST       # or -DNAME[=VAL]
+libext+.v+.sv
-y ylib                  # library dirs: modules found by <name><ext> are read-only
-v lib/cells.v           # library file: never expanded
--top top                # optional
src/top.v                # bare paths are source files (globs allowed); they ARE expanded
-f more.f                # nested, relative to cwd (--relative-to cwd, default)
-F sub.f                 # nested, relative to the nested file
```

`--relative-to filelist` resolves the bare paths of a `-f` file against
the filelist's own directory (handy for checked-in filelists).

### Commands

```bash
# instance tree, dependency levels (level 0 = leaves), leaf-first order
pyverilog-auto hierarchy -f design.f --relative-to filelist --view all
pyverilog-auto hierarchy -f design.f --json > design.json

# expand every source file, leaves first (library files are never touched)
pyverilog-auto integrate -f design.f --relative-to filelist --diff      # preview
pyverilog-auto integrate -f design.f --relative-to filelist             # write
```

`integrate` options: `--dry-run`/`--no-save`, `--diff`, `--only REGEX`
(files or module names), `--from LEVEL`, `--passes N|auto` (auto = one pass
plus a second pass over dependency cycles and files that define several
modules), `--strict` (fail on filelist/parse errors), `--lib REGEX` /
`--src REGEX` (override which files are read-only).

### Python API

```python
from pyverilog_auto.integ import Design

design = Design.from_filelist("design.f", relative_to="filelist")   # or Design.from_files([...], library_dirs=[...])
design.order().levels            # [[leaf files], [wrappers], [top]]
for root in design.hierarchy():  # Instance tree; paths like "top.gen_cores[1].u_core.u_dma"
    ...
design.find_instances(r"\.u_dma$")
design.lca("top.u_a.u_x", "top.u_b.u_y")
report = design.expand_all(dry_run=False)
print(report.summary())
```

## Routing (connect ports between instances by regex path)

`route` connects a **signal, struct-typed signal or SystemVerilog interface**
that already exists as a port on one module to any other module. Both ends
are regexes over hierarchical instance paths; `\1`-style backreferences pair
the matches. Requires pyslang.

```bash
pyverilog-auto route -f design.f --relative-to filelist \
    --route 'top\.u_cluster(\d+)\.u_core\.u_dma:m_axi' 'top\.u_mem\.u_ctrl\1:s_axi' \
    --dry-run                       # print the planned diff, write nothing
pyverilog-auto route -f design.f --routes routes.toml --then-expand   # write, then expand leaf-first
```

`routes.toml` (literal strings keep the backslashes):

```toml
[[route]]
name = "axi"
src  = 'top\.u_cluster(\d+)\.u_core\.u_dma:m_axi'   # PATH_REGEX:port (port must exist on the source)
dst  = 'top\.u_mem\.u_ctrl\1:s_axi'                 # PATH_TEMPLATE[:port]; port created if missing
iface_conn = { clk = "clk", rst_n = "rst_n" }       # interface instance ports at the common ancestor

[[route]]
src = 'top\.u_cluster0\.u_ctl\.u_timer:tick'
dst = 'top\.u_mem\.u_ctrl0:tick_in'
```

Spec keys: `src`, `dst`, `name`, `net` (net-name template, may use `\1`),
`dst_port`, `dst_modport`, `iface_conn`, `iface_params`,
`modport_policy` (`carry` | `plain`), `check_types`, `comment`.

How it works:

* The tool finds the lowest common ancestor of each pair, instantiates the
  interface there (or declares the net), and punches a port through every
  module in between. If one end is an ancestor of the other, the port is
  exposed at that module's boundary instead.
* **AUTO-native where markers exist, explicit edits otherwise.** A module
  with `/*AUTOINPUT*/` `/*AUTOOUTPUT*/` and an `/*AUTOINST*/` instantiation
  needs no edit for a plain signal: the following `integrate` (or
  `--then-expand`) creates the port and the pin. Modules without markers get
  an ANSI port entry (or a non-ANSI `input ... name;` declaration) and an
  explicit `.port (net)` pin. Interface ports are always written explicitly
  (AUTOINPUT never creates them); AUTOINST still emits `.p (p.modport)`.
* Renames are explicit pins placed before `/*AUTOINST*/` (never AUTO_TEMPLATE,
  which is module-scoped). Modules shared by several routed instances keep
  one port name; the renames happen at the parent that hosts the instances.
* A module instantiated elsewhere gets the new port too; such instances are
  reported as `W_UNROUTED`, and under `/*AUTOINST*/` they receive `.port ()`
  so nothing connects implicitly. `--strict` turns this into an error.
* Everything is validated first (multi-driver, direction/type mismatches,
  name collisions, black boxes, different tops, positional connections);
  nothing is written when any error is reported. Runs are idempotent.
* Inserted lines carry `// routed: <name>` comments (`--no-comment` to omit).

Python: `design.plan_routes(specs)`, `design.apply_routes(specs, dry_run=..., strict=..., then_expand=...)`,
`design.route("SRC -> DST")`.

A runnable walk-through lives in `sample_env/route_demo/` (`run_route_demo.sh`
or `run_route_demo.bat`): a top with two cores and four leaves, annotations in
the leaves, and a script that collects, dry-runs, applies and re-runs the routes.

### Patterns

Endpoints in `routes.toml` and `--route SRC DST` are Python regular expressions
full-matched against hierarchical instance paths from the top, followed by
`:port`. Anything regex works, including `.*`, classes and groups:

```toml
src = 'top\.core.*instE:sig'        # 'core', anything, then 'instE'
src = '.*instE:sig'                 # instE anywhere in the design
src = 'top\.u_core[01]\.u_dma:irq'  # a character class
```

The match always covers the whole path from the top, so `core.*instE:sig` alone
does not match `top.core.instA.instB.instE`; write `top.*core.*instE:sig`. In a
regex `.` matches any character, so use `\.` for a literal dot when it matters
(`core\.instA` versus `core.instA`). Capture groups pair the two ends: `\1` in
`dst` is replaced by the text `src` captured.

### In-source annotations (`//auto_route`)

Routes can be written next to the port they concern. The annotation lives in
the module that declares the port and applies to **every instance** of that
module:

```verilog
output axi_if  data_ch;
//auto_route data_ch :: to :: instE, instF        // this port drives instE and instF
//auto_route ctrl_ch :: from :: instE             // instE's ctrl_ch drives this port
//auto_route irq     :: to :: coreB.instE:irq_in  // dotted path suffix, other-end port name
//auto_route busy    :: to :: re:top\.u_mem\.u_ctrl[01]   // regex target
```

Targets are instance names (path suffixes) resolved to the **nearest**
matching instances (deepest common ancestor with the annotated instance);
equally near matches fan out. A target that contains a regex metacharacter
is a regular expression full-matched against the whole path from the top,
exactly like the `routes.toml` patterns: `top.*instE` finds
`top.core.instA.instB.instE` and `top\.u_mem\.u_ctrl[01]` finds both
controllers (`re:REGEX` is the explicit spelling of the same thing).

Routing to an ancestor exposes the port on that module's boundary. Two
keywords resolve relative to each annotated instance: `$top` (alias `$root`)
is the root of its tree and `$parent` its immediate parent. When the
annotated module has several instances, give each boundary port a distinct
name with placeholders in the far-end port name: `{inst}` (instance name),
`{parent}`, `{path}` (path below the root, dots as `_`) and `{n}` (index
among the module's instances in that tree). The far-end name also names the
intermediate nets:

```verilog
//auto_route err   :: to :: top:err_{n}    // top gets output err_0, err_1 ...
//auto_route m_axi :: to :: $parent        // parent gets an axi_if m_axi boundary port
```

Notes: a non-ANSI (`/*AUTOARG*/`) parent receives signal ports as body
declarations that AUTOARG then lists; interface ports need an ANSI header,
so routing an interface to an AUTOARG-style module is rejected before
anything is written. AUTO-style parents already export unconsumed sub-instance
outputs through AUTOOUTPUT, so `to :: top` mostly matters for hand-written
intermediates, renamed ports and interfaces. The tool collects the annotations in a first
pass, resolves them to exact paths, and writes a reviewable `routes.toml`:

```bash
pyverilog-auto route -f design.f --relative-to filelist --collect-only --routes-out routes.toml
pyverilog-auto route -f design.f --relative-to filelist --routes routes.toml --then-expand
# or in one go:
pyverilog-auto route -f design.f --relative-to filelist --collect --routes-out routes.toml --then-expand
```

Python: `design.collect_auto_routes()` returns the resolved specs (plus
warnings for annotations whose module is never instantiated or whose target
is unreachable). For symmetric pairings (cluster0 -> ctrl0, cluster1 -> ctrl1)
use a `routes.toml` with capture groups; annotations name instances, not
positions.

## Differences from Emacs verilog-mode

This is a faithful port of the AUTO expansion engine. Known limitations:

- **Escaped identifiers** (`\name `) — limited parser support
- **Gate-level primitives** (`buf`, `or`, etc.) — not recognized as modules
- **`.*` (dot-star) ports** — implicit port connections not fully supported
- **Multi-dimensional port arrays** — some 2D parameter cases unsupported
- **AUTOINSERTLAST** — stub only, not fully implemented
- **AUTO_LISP** — basic evaluator; complex Emacs Lisp expressions may not evaluate
- **Indentation** — separate engine; minor formatting differences vs Emacs
- **Font-lock / UI** — not ported (Emacs-specific features)

## Running the Test Suite

The test suite compares AUTO expansion output against 422 golden files
generated by Emacs verilog-mode:

```bash
# Run all golden tests
pytest tests/test_golden.py -q

# Run a specific test
pytest tests/test_golden.py::test_golden[autoinst_basic.v] -v

# Run with failure details
pytest tests/test_golden.py --tb=short
```

### Test Tools

| Tool                        | Description                           |
|-----------------------------|---------------------------------------|
| `tools/analyze_failures.py` | Categorize and summarize test failures|
| `tools/profile_run.py`      | Performance profiling of expansion    |
| `tools/batch_compare.py`    | Batch comparison with golden outputs  |

## License

See the original [verilog-mode](https://www.veripool.org/verilog-mode/)
for licensing terms.
