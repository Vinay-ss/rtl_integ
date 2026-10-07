# rtl-integ-gui

A Neovim front end for browsing and restructuring an integrated RTL design
whose sources are **prepro templates** (Perl `// pl` or Python `// py`) and
plain Verilog/SystemVerilog files.

```
+------------------------------------------------------------------------------+
| NEOVIM WINDOW   (Neovide or terminal nvim)                                   |
|  +------------------+  +--------------------------------------------------+  |
|  | Hierarchy tree   |  | Source panel: template (editable) or RTL (r/o)   |  |
|  |                  |  +--------------------------------------------------+  |
|  |                  |  | Console: log, diffs, diagnostics, commands       |  |
|  +------------------+  +--------------------------------------------------+  |
+------------------------------------------------------------------------------+
                                       |  JSON lines over stdio
+------------------------------------------------------------------------------+
| PYTHON BACKEND  build (prepro3 + AUTO expansion) - source map - operations   |
+------------------------------------------------------------------------------+
```

Every structural edit is written to the **templates** (never to generated
RTL), previewed as a diff, journaled for undo, rebuilt, and checked: the
leaf instances must stay connected exactly as before.

## Starting

* Bundle (no installation): from the
  [Releases](https://github.com/Vinay-ss/rtl_integ/releases) page, unpack
  `rtl-integ-gui-<ver>-<target>` and run `rtl-integ-gui PROJECT_DIR`
  (Windows: `rtl-integ-gui.cmd`), or install the Linux `.rpm`/`.deb`.
* From the repository: `pip install -e ".[gui]"`, Neovim 0.10+ on PATH (or
  `RTL_INTEG_NVIM`), optionally Neovide; then `rtl-integ-gui PROJECT_DIR`.

```
rtl-integ-gui PROJECT_DIR | rtl_integ_project.toml   open a project
rtl-integ-gui -f design.f [--top TOP]                browse a filelist (view only)
rtl-integ-gui --demo [prepro]                        a copy of the demo (backtick or // py templates)
rtl-integ-gui --selftest [--demo prepro]             headless check of the installation
rtl-integ-gui --ui tui|neovide                       force terminal Neovim / Neovide
rtl-integ-gui --version                              tool and component versions
```

With `--ui auto` (the default) Neovide is used when there is a display and it
can start; otherwise (SSH, or a Linux older than Neovide needs: glibc 2.35)
Neovim runs in the terminal with the same layout and mouse support.

Neovim runs with `--clean -u <package>/gui/nvim/init.lua`: your own config is
not loaded and no plugins are needed.  To use the plugin from your own config
instead, add `<package>/gui/nvim` to `runtimepath` and run `:RtlIntegOpen DIR`.

## Project file (`rtl_integ_project.toml`)

```toml
[project]
top = "top"
build_dir = "build"            # gen/ (prepro output + line maps) and integ/ (AUTO-expanded)
include_dirs = ["inc"]
defines = { SYNTH = "1" }
libexts = [".sv", ".v"]
perl = ""                      # empty: PATH, then Git-for-Windows / Strawberry perl
perl_lib = ["tools/perl"]      # PERL5LIB for `use` in Perl templates
route = "none"                 # "collect": apply //auto_route annotations after expansion
syntax = "prepro"              # template delimiters of every template (see below)

[[template]]
src = "tpl/top.svp"
out = "top.sv"
lang = "python"                # or "perl"
syntax = "backtick"            # this template only (default: project.syntax)
args = []                      # prepro "++ ARGS"
defines = []                   # prepro -d lines
replace = [["#$", "none"], [">#;>", "right"]]   # prepro -r/-rl/-rr/-rn; empty: the syntax's

[[source]]
path = "rtl/stage.sv"          # used as it is (edited directly)

[gui.wrap]
port_naming = "net"            # or "inst_port"
wrapper_dir = "tpl"            # default: next to the parent template
```

Wrappers made by the GUI are added as `[[template]]` entries with
`created_by_gui = true`; carried template variables appear as
`bind = { from = "tpl/top.svp", line = 21, vars = ["W"] }` (their values are
taken from that line of the parent template at every build).

## Template syntax

| | `syntax = "prepro"` (default) | `syntax = "backtick"` |
|---|---|---|
| code line | `// py CODE` | `` ` CODE `` |
| reset the print indentation | `// py` alone at column 0 | `` ` `` alone at column 0 |
| code block | `/* py-begin` ... `py-end */` | `[*` ... `*]` |
| variable | `#$var` or `#$var#` | `` `var` `` |
| expression, padded left/right | `#[expr]#`, `#<expr<#`, `#>expr>#` | the same |

(Perl templates: `// pl`, `/* pl-begin` ... `pl-end */`; the backtick markers
are the same for both languages.)

In the backtick syntax:

- `` `var` `` needs both backticks and a name between them (Python also
  `` `cfg.width` ``), so macros stay macros: `` `define ``, `` `WIDTH-1 ``,
  `` `SUM(`WIDTH, 1) `` are copied unchanged.  The one clash is a macro
  directly followed by a backtick (`` `A`B ``), which reads as variable `A`.
  Backticks in comments count too: `` // see `W` `` prints the value of `W`.
- `[*` opens a block only when a space or the end of the line follows it, so
  assertion repetitions (`b[*3]`, `[*]`) are text.  Code may share the line
  with the markers: `[* W = 8 *]`, `[* a = 1` ... `b = 2 *]`.
- As in prepro, text after a block or a code line is indented like the last
  code line: a `for` body continues until a line with `` ` `` alone.

## The window

Tree keys: `<CR>` module source, `i` instantiation site, `t` template/RTL
view, `o` expand, `m` mark, `M` clear marks, `W` wrap marked, `H` hoist,
`U` unroll, `u` undo, `R` rebuild, `/` filter (regex matched against the whole
path from the top, e.g. `top.*core.*instE`), `K` info, `?` help.

Tags: `[S]` substituted template text, `[G]` inside a template `if`,
`[Lx4]` printed by a template loop (4 times), `[C]` printed by template code,
`[W]` wrapper made by the GUI, `[BB]` black box, `[IF]` interface.

Console: `<CR>` or `i` in the console asks for a command (`help`, `build`,
`diag`, `find REGEX`, `info PATH`); diagnostics also go to the quickfix list.

Commands: `:RtlIntegOpen`, `:RtlBuild[!]`, `:RtlView template|integ|gen`,
`:RtlWrap`, `:RtlHoist`, `:RtlUnroll`, `:RtlUndo`, `:RtlConsole CMD`.

## Operations

**Wrap** (mark siblings, `W`): names used outside the selection, and ports of
the parent, become wrapper ports; names used only inside become internal nets;
parameters are forwarded.  Template text moves verbatim, so tokens,
`/*AUTOINST*/` and template blocks stay live.  Template variables are carried
(bound to the parent's values) or, when that is not possible, frozen on
request.  Outputs that drive only part of an outside net get their own port.
Port naming: `net` (port = parent net name) or `inst_port` (`<inst>_<port>`).

**Hoist** (`H` on an instance inside a wrapper): applies to every instance of
the wrapper.  Wrapper ports and parameters it used are replaced by what each
parent connects; nets it shared with siblings become new wrapper ports; ports
nobody else uses are removed.  AUTOINPUT/AUTOOUTPUT headers regenerate by
themselves.  An emptied wrapper can be dissolved (file, project entry and
instances removed).

**Template structure**: instances under the same template `if` keep the
guard around the wrapper instance; a guard otherwise travels with its
statement.  A loop instance is wrapped with its whole loop (`group`) or the
loop is unrolled first; code-printed instances are frozen first (`U` does the
same on its own).  Unroll refuses when later template code still needs the
loop's variables.

**Undo** (`u`) restores the files of the last operation byte for byte (the
journal is in `.rtl_integ_gui/journal/`).

## prepro3

`prepro3` (`python -m pyverilog_auto.prepro`) is a Python 3 port of prepro
with the same options plus `--syntax prepro|backtick`, `-rn TOKEN` (a name-only
token, e.g. `` -rn "`;`" ``), `--linemap FILE` (JSON: template line of every
output line, template constructs), `--capture LINE:VAR,...` and
`--perl-lib DIR`.  Python templates run under Python 3.

## Bundles

```
python tools/bundle/build_bundle.py --target windows-x86_64|linux-x86_64 [--pyslang-wheel W] [--offline]
python tools/bundle/smoke_bundle.py dist/rtl-integ-gui-<ver>-<target>
python tools/bundle/package_linux.py dist/rtl-integ-gui-<ver>-linux-x86_64     # .rpm + .deb (nfpm)
python tools/bundle/third_party_sources.py                                    # LGPL sources archive
```

Component versions and sha256 pins are in `tools/bundle/versions.toml`;
downloads are cached in `build/bundle-cache`.  The Linux bundle reaches
glibc 2.17 (CentOS/RHEL 7) with Neovim from `neovim/neovim-releases` and a
pyslang wheel built in the manylinux2014 image by
`tools/bundle/build_pyslang_wheel.sh`; with PyPI's pyslang wheel it needs
glibc 2.28.  `tools/bundle/licenses/` holds the full third-party license
texts copied into every bundle (`--refresh-licenses` re-reads Python's from
the pinned full archive).

Releases are built by `.github/workflows/release.yml`: pushing a `v*` tag
builds both bundles and the packages, runs `--selftest` on CentOS 7, Rocky
8/9, Alma 10, openSUSE 15, Ubuntu 18.04/20.04/24.04 and Debian 11 (tarball
and package), and publishes them with `SHA256SUMS` and the third-party
sources; a push to a `release/**` branch, or running the workflow by hand,
is a dry run that keeps the files as workflow artifacts.  A Linux bundle assembled on Windows replaces symlinks
by copies and is not precompiled; build on Linux for release.

## Limits

* Instances inside SystemVerilog `generate` blocks, instance arrays,
  positional connections, and statements holding several instances are not
  moved.
* Template functions (`def`) that print instances cannot be unrolled.
* The leaf check joins nets by name (`{a, b}` joins both); assigns between
  two plain names count as connections.
