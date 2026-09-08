#!/usr/bin/env bash
# ------------------------------------------------------------------
# pyverilog-auto routing demo (see README.md in this directory)
#   ./run_route_demo.sh                       uses "uv run pyverilog-auto"
#   PVA="python -m pyverilog_auto" ./run_route_demo.sh
# ------------------------------------------------------------------
set -e
cd "$(dirname "$0")"
PVA="${PVA:-uv run pyverilog-auto}"
F="-f work/design.f --relative-to filelist"

echo "=== 0. fresh working copy: src/ -> work/"
rm -rf work && mkdir work && cp src/* work/

echo; echo "=== 1. hierarchy (instance tree, file levels, leaf-first order)"
$PVA hierarchy $F --view all

echo; echo "=== 2. collect the //auto_route annotations into work/routes.toml (nothing applied yet)"
$PVA route $F --collect-only --routes-out work/routes.toml
echo "--- work/routes.toml:"; cat work/routes.toml

echo; echo "=== 3. dry run: show what the routes would change"
$PVA route $F --routes work/routes.toml --dry-run

echo; echo "=== 4. apply the routes, then expand all AUTOs leaf-first"
$PVA route $F --routes work/routes.toml --then-expand

echo; echo "=== 5. results"
for f in top.v core_a.sv core_b.sv; do echo "--- work/$f"; cat "work/$f"; echo; done

echo "=== 6. run again: no edits (idempotent)"
$PVA route $F --routes work/routes.toml --then-expand
