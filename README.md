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
