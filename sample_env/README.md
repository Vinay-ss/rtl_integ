# pyverilog-auto Sample Environment

A self-contained test environment with a mini SoC (UART, SPI, GPIO) to
exercise every major `pyverilog-auto` command and AUTO type.

## Directory layout

```
sample_env/
├── rtl/                    # Leaf modules (submodules)
│   ├── uart_tx.v           #   UART transmitter
│   ├── uart_rx.v           #   UART receiver
│   ├── spi_master.v        #   SPI master
│   └── gpio_port.v         #   GPIO port (has inout)
├── uart_wrap.v             # UART wrapper   — AUTOARG, AUTOINST, AUTOWIRE, AUTOINPUT, AUTOOUTPUT
├── soc_top.v               # Full SoC top   — all of the above + AUTOINOUT
├── auto_reg_demo.v         # AUTOREG
├── auto_sense_demo.v       # AUTOSENSE
├── auto_reset_demo.v       # AUTORESET
├── auto_tieoff_demo.v      # AUTOTIEOFF
├── auto_ascii_demo.v       # AUTOASCIIENUM
├── run_demo.sh             # Bash runner (runs all demos)
├── run_demo.bat            # Windows runner
└── README.md               # This file
```

## Quick start

```bash
# 1. Install (from project root)
cd <project-root>
uv pip install -e .

# 2. Run all demos
cd sample_env
bash run_demo.sh        # Linux/Mac/Git Bash
run_demo.bat            # Windows cmd
```

## Whole-design flow (filelist)

`design.f` lists the two top files, the standalone demos and `-y rtl`:

```bash
# instance tree + dependency levels (rtl/ leaves are level 0, read-only)
uv run pyverilog-auto hierarchy -f sample_env/design.f --relative-to filelist --view all

# expand everything leaves first (the committed files are already expanded: 0 changes)
uv run pyverilog-auto integrate -f sample_env/design.f --relative-to filelist --diff
```

## Try it yourself — individual commands

```bash
cd sample_env

# Preview what would change (non-destructive)
pyverilog-auto diff -y rtl/ uart_wrap.v

# Expand all AUTOs in-place
pyverilog-auto expand -y rtl/ uart_wrap.v

# Expand the full SoC
pyverilog-auto expand -y rtl/ soc_top.v

# Expand standalone demos (no -y needed, self-contained)
pyverilog-auto expand auto_reg_demo.v
pyverilog-auto expand auto_sense_demo.v
pyverilog-auto expand auto_reset_demo.v
pyverilog-auto expand auto_tieoff_demo.v

# Strip AUTO sections back to markers
pyverilog-auto delete -y rtl/ uart_wrap.v
pyverilog-auto delete -y rtl/ soc_top.v

# Re-indent
pyverilog-auto indent auto_reg_demo.v

# Print to stdout instead of overwriting
pyverilog-auto expand --no-save -y rtl/ uart_wrap.v
```

## What each file demonstrates

| File | AUTO types used | What to look for |
|---|---|---|
| `uart_wrap.v` | AUTOARG, AUTOINST, AUTOWIRE, AUTOINPUT, AUTOOUTPUT | Full wrapper: port list, wire decls, and instance connections all auto-generated |
| `soc_top.v` | All of the above + AUTOINOUT | Multi-instance SoC; gpio_pins shows up as `inout` |
| `auto_reg_demo.v` | AUTOREG | `reg` declarations generated for outputs driven by always blocks |
| `auto_sense_demo.v` | AUTOSENSE | Sensitivity list `@(a or b or sel)` auto-filled |
| `auto_reset_demo.v` | AUTORESET | Reset assignments `counter <= 4'h0` etc. auto-generated |
| `auto_tieoff_demo.v` | AUTOTIEOFF | Unused outputs `port_b`, `port_c` tied to zero |
| `auto_ascii_demo.v` | AUTOASCIIENUM | ASCII decode string for FSM state names |

## Resetting the files

To get back to the unexpanded state, use `delete` on each file:

```bash
pyverilog-auto delete -y rtl/ uart_wrap.v soc_top.v
pyverilog-auto delete auto_reg_demo.v auto_sense_demo.v auto_reset_demo.v auto_tieoff_demo.v auto_ascii_demo.v
```

Or just `git checkout sample_env/` if you've committed the originals.
