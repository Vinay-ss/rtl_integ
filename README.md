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

## RTL integration GUI (rtl-integ-gui)

A Neovim-based GUI on top of the integration commands: the instance hierarchy
on the left, the module source on the right (the prepro template by default,
or the generated RTL), and a console.  It restructures the design where it is
written, in the templates: **wrap** marked sibling instances into a new
wrapper module, **hoist** an instance out of its wrapper (in every copy of the
wrapper), unroll template loops, with a diff preview, a connectivity check
after every change, and undo.  Templates use prepro's syntax or the backtick
syntax (`` ` CODE `` lines, `[*` ... `*]` blocks, `` `var` ``).

**Download** a self-contained build from the
[Releases](https://github.com/Vinay-ss/rtl_integ/releases) page; nothing else
needs to be installed (Perl only for Perl templates):

| File | For |
|---|---|
| `rtl-integ-gui-<ver>-windows-x86_64.zip` | Windows 10/11 |
| `rtl-integ-gui-<ver>-linux-x86_64.tar.gz` | any x86_64 Linux with glibc 2.17+ (CentOS/RHEL 7 and later, Ubuntu 18.04+, Debian 10+, SUSE 15), no root needed |
| `rtl-integ-gui-<ver>-1.x86_64.rpm`, `rtl-integ-gui_<ver>-1_amd64.deb` | the same, installed to `/opt/rtl-integ-gui` with `rtl-integ-gui` on PATH |

```bash
rtl-integ-gui --demo            # a copy of the demo project (rtl-integ-gui.cmd on Windows)
rtl-integ-gui path/to/project   # a directory with rtl_integ_project.toml
rtl-integ-gui --selftest        # check the installation
```

On Windows, unblock the downloaded zip (Properties, *Unblock*) before
extracting it; the files are not code-signed.  The Neovide window needs a
display and, on Linux, glibc 2.35+ and OpenGL; elsewhere (RHEL/CentOS 7-9,
SSH sessions) the GUI runs in the terminal.

Or install from source and bring your own Neovim (0.10+), on any OS:

```bash
pip install "pyverilog-auto[gui] @ git+https://github.com/Vinay-ss/rtl_integ"
rtl-integ-gui --demo
```

The user guide (project file, keys, operations, template syntax) is
[pyverilog_auto/gui/README.md](pyverilog_auto/gui/README.md); `prepro3` is
the bundled Python 3 port of the prepro template preprocessor.

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
| `strip`   | Remove AUTO attributes, keep the generated code (see [Strip mode](#strip-mode-clean-rtl-for-preprocessor-flows)) |

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

### Output Options (`expand`, `diff`, `integrate`, `route`)

| Flag                           | Description                                                         |
|--------------------------------|---------------------------------------------------------------------|
| `--inst-lineup`                | Align `.port`, `(net` and comments across every instance            |
| `--inst-port-comment FIELDS`   | Trailing pin comments; FIELDS is a subset of `dir,width,type`       |
| `--inst-comment-column N`      | Minimum column for the pin comments (default: right after the pins) |
| `--strip-autos`                | Remove AUTO attributes after everything else has run                |

The first three set the defaults; a file's Local Variables override them.

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

# Expand, align instances with direction/width/type comments, then drop the AUTO attributes
pyverilog-auto expand -y rtl/ --inst-lineup --inst-port-comment dir,width,type --strip-autos top.v

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
| `verilog-auto-inst-lineup`            | `nil`     | Align every instance's pins (see below)          |
| `verilog-auto-inst-port-comment`      | `nil`     | Pin comments, e.g. `"dir width type"`            |
| `verilog-auto-inst-comment-column`    | `0`       | Minimum pin-comment column (`0` = automatic)     |
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

### Instance lineup and pin comments

Two options, off by default, reformat every named-port instance in the
module: AUTOINST pins, hand-written pins and pins added by `route`.

```verilog
// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
```

```verilog
   leaf_c instC
     (.clk        (clk),          // input        logic
      .c_in       (c_in[7:0]),    // input  [7:0] logic
      .c_busy     (c_busy),       // output       logic
      .data_ch    (data_ch));     // interface    axi_if.master
```

How the formatting works:

* **One pin per line.** The `.` column is the first pin's column. When the
  `/*AUTOINST*/` marker comes first, it is the column after `(`.
* **`(` column.** It is the AUTOINST column (`verilog-auto-inst-column`),
  pushed right when a port name is longer, so long names no longer break
  the alignment.
* **Comment column.** Comments start one space after the longest pin, or
  at `verilog-auto-inst-comment-column` if that is larger. Each field
  (direction, width, type) gets its own sub-column. A field that is empty
  for every pin is dropped.
* **Field values.**
  * The direction is `input`, `output`, `inout`, `interface` or `parameter`.
  * The width is the packed range, followed by any unpacked range.
  * The type is the declared type (plus `signed`), or `iface.modport`. It
    is `wire` when nothing is declared.
* **Choosing fields.** `dir`, `width` and `type` can be listed in any
  subset; `t` means all three.
* **Using one option alone.** With only `lineup`, pins are aligned and get
  no comments. With only `port-comment`, the comments are added and the
  `(` spacing stays as written.
* **Parameter lists.** A `#(...)` parameter list is laid out separately
  from the port list.
* **Re-runs.** The tool's own comments are recognized and rewritten on
  every run. `// Templated`, `// routed:` and your own comments stay after
  them.
* **Instances left alone.** Positional connections, lists containing
  `` `ifdef `` and lists the tool cannot parse are not touched.

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
`modport_policy` (`carry` | `plain`), `check_types`, `comment`,
`create_dst` (default `true`; `false` makes a missing destination port an
`E_DST_PORT_MISSING` error instead of creating it; a port inside an AUTO fence
counts as existing).

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
* **The same connection in every instance of a module shares one net name.**
  For example, a wrapper that is instantiated twice gets one net for each
  route inside it.
  * The name defaults to the port of the driving side.
  * A capture group that only tells the wrapper instances apart is not used
    in the name.
  * If the copies ask for different `net` names, they collapse to one name:
    an explicit name wins, with `W_NET_NAME_MERGED`.
* **Different drivers never share a net.** When two drivers would get the
  same name in a module, each default name becomes
  `<driver path>_<port>`, for example `u_ctrl0_err` and `u_ctrl1_err`. If
  that still clashes, or the name was written by hand, the result is
  `E_NET_NAME_COLLISION`.
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

### Wrapper annotations (child to child)

A wrapper can connect its children without touching their sources. The left
side of the annotation names a child port as `INSTPATH:PORT`:

```verilog
module core_b (/*AUTOINPUT*/ /*AUTOOUTPUT*/);
   //auto_route instE:e_busy  :: to   :: instF:f_hold          // instE drives instF
   //auto_route instF:f_ack   :: from :: instE:e_ack           // instE's e_ack drives instF.f_ack
   //auto_route instE:e_irq   :: to   :: instF:f_irq, u_sub.u_leaf:irq   // fan-out, deeper path
   //auto_route re:top\.coreB\.inst[EF]:status :: to :: $top:status_{inst}   // instance path, full match
   leaf_e instE (/*AUTOINST*/);
   leaf_f instF (/*AUTOINST*/);
endmodule
```

How the left side is read:

* **Meaning.** An annotation in wrapper W with left side `instE:e_busy`
  behaves exactly like `//auto_route e_busy :: to :: ...` written inside
  instE's module, but only for the `instE` below each W instance. Targets,
  `$top`/`$parent` and `{inst}`-style placeholders all work as above,
  relative to that child.
* **Plain `INSTPATH`.** A dotted path below W, such as `instE` or
  `u_sub.u_leaf`.
* **Regex `INSTPATH`.** `re:REGEX`, or any regex metacharacter, is
  full-matched from the top and limited to W's subtree. Several matches
  give several origins.
* **Missing ports are errors.** A wrapper connects ports that already
  exist, so its routes use `create_dst = false`: a mistyped far-end port is
  an `E_DST_PORT_MISSING` error instead of a new port on a leaf. Routes
  towards `$top`/`$parent` still create the boundary port.
* **Net names.** As with any annotation, the net is named after the far-end
  port: `to :: instF:f_hold` names it `f_hold`, and `from :: instE:e_ack`
  names it `e_ack`.
  * A fan-out with differently named far ends uses the driver's port name.
  * The name is the same whether the wrapper is instantiated once or many
    times. One edit to the wrapper's text connects every instance.
  * Cross-coupled pairs (A→B and B→A) get two distinct nets, named
    `<inst>_<port>` (e.g. `instB_err` / `instC_err`).
* **Example.** `tests/integ/routing_wrapper/` has a wrapper instantiated
  twice, with renamed ports, `from`, fan-out, an interface, a deeper path, a
  regex left side and a cross-coupled pair.
* **Other errors.** `E_AUTOROUTE_SRC` means the left side matched no
  instance. `E_AUTOROUTE_SYNTAX` means an `auto_route ... :: ...` comment
  did not parse.

Python: `design.auto_route("core_b", "instE:e_busy", "to", "instF:f_hold")`
returns the same `CollectResult` without writing any comment; apply its
`.specs` with `design.apply_routes(...)`.

## Strip mode (clean RTL for preprocessor flows)

When the tool runs on files a preprocessor generated, the AUTO sources live in
the original files and the output should be plain RTL. Strip mode removes every
AUTO attribute and keeps the generated code:

```bash
pyverilog-auto expand -y rtl/ --strip-autos gen/top.v       # expand, then strip
pyverilog-auto integrate -f design.f --strip-autos          # all source files, after every pass
pyverilog-auto route -f design.f --collect --then-expand --strip-autos
pyverilog-auto strip gen/*.v                                # strip already-expanded files
```

**Removed:**
* AUTO marker comments: `/*AUTOINST*/`, `/*AUTOARG*/`, `/*AUTOSENSE*/`,
  `/*AS*/`, `/*AUTOWIRE*/` and the rest, plus `/*memory or*/`.
* The `// Beginning of automatic ...` / `// End of automatics` fence lines.
  The code between them is kept.
* `AUTO_TEMPLATE` blocks, `AUTO_LISP(...)`, `AUTO_CONSTANT(...)` and
  `auto enum` tags.
* `AUTONOHOOKUP` and `//auto_route` annotations.
* The `// Outputs` / `// Inputs` / `// Inouts` / `// Interfaces` /
  `// Parameters` headers in instance and AUTOARG lists.
* The `// Templated ...`, `// Implicit .*` and `// routed: ...` pin comments.
* The `Local Variables` block and the `-*- mode: Verilog -*-` cookie.
* The `.*` token when its pins were expanded.

**Kept:**
* All code.
* Pin comments from `--inst-port-comment`.
* `// From/To ...` provenance comments (turn those off at generation with
  `verilog-auto-wire-comment: nil`).
* Your own comments.
* Markers inside `//` comments, which are inactive.

`strip` and `design.strip_autos` keep CRLF files as CRLF; `expand` writes LF, as
it always has.

Strip runs once, last: after expansion, the instance lineup, routing and
every `integrate` pass. It covers every processed source file, so re-running
on files that are already expanded still strips them. With `--dry-run` or
`--diff`, the stripped result is shown and nothing is written. A stripped
file has no markers left, so it cannot be expanded again. Keep running the
tool on the original (or preprocessed) sources.

Python: `pyverilog_auto.auto.strip.strip_autos(text)`,
`design.expand_all(strip_autos=True)`, `design.strip_autos(files=None)`.

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
