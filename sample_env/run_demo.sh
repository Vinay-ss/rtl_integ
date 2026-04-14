#!/bin/bash
# ============================================================
# pyverilog-auto sample environment — demo runner
# ============================================================
# Run from the sample_env/ directory:
#   bash run_demo.sh
#
# Prerequisites:
#   cd <project-root>
#   uv pip install -e .
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SEP="============================================================"

echo "$SEP"
echo " pyverilog-auto  Demo Runner"
echo "$SEP"
echo ""

# ------------------------------------------------------------------
# 1) DIFF — preview what expand would do (non-destructive)
# ------------------------------------------------------------------
echo ">>> 1. DIFF — preview changes on uart_wrap.v"
echo "$SEP"
uv run pyverilog-auto diff -y rtl/ uart_wrap.v || true
echo ""

# ------------------------------------------------------------------
# 2) EXPAND — run AUTO expansion in-place
# ------------------------------------------------------------------
echo ">>> 2. EXPAND — uart_wrap.v  (AUTOINST + AUTOWIRE + AUTOINPUT + AUTOOUTPUT + AUTOARG)"
echo "$SEP"
uv run pyverilog-auto expand -y rtl/ uart_wrap.v
echo "Done.  Result:"
cat uart_wrap.v
echo ""
echo ""

echo ">>> 3. EXPAND — soc_top.v  (full SoC: AUTOINST + AUTOWIRE + AUTOINPUT + AUTOOUTPUT + AUTOINOUT + AUTOARG)"
echo "$SEP"
uv run pyverilog-auto expand -y rtl/ soc_top.v
echo "Done.  Result:"
cat soc_top.v
echo ""
echo ""

echo ">>> 4. EXPAND — auto_reg_demo.v  (AUTOREG)"
echo "$SEP"
uv run pyverilog-auto expand auto_reg_demo.v
echo "Done.  Result:"
cat auto_reg_demo.v
echo ""
echo ""

echo ">>> 5. EXPAND — auto_sense_demo.v  (AUTOSENSE)"
echo "$SEP"
uv run pyverilog-auto expand auto_sense_demo.v
echo "Done.  Result:"
cat auto_sense_demo.v
echo ""
echo ""

echo ">>> 6. EXPAND — auto_reset_demo.v  (AUTORESET)"
echo "$SEP"
uv run pyverilog-auto expand auto_reset_demo.v
echo "Done.  Result:"
cat auto_reset_demo.v
echo ""
echo ""

echo ">>> 7. EXPAND — auto_tieoff_demo.v  (AUTOTIEOFF)"
echo "$SEP"
uv run pyverilog-auto expand auto_tieoff_demo.v
echo "Done.  Result:"
cat auto_tieoff_demo.v
echo ""
echo ""

echo ">>> 8. EXPAND — auto_ascii_demo.v  (AUTOASCIIENUM)"
echo "$SEP"
uv run pyverilog-auto expand auto_ascii_demo.v
echo "Done.  Result:"
cat auto_ascii_demo.v
echo ""
echo ""

# ------------------------------------------------------------------
# 3) DELETE — strip AUTO expansions back to markers only
# ------------------------------------------------------------------
echo ">>> 9. DELETE — strip uart_wrap.v back to markers"
echo "$SEP"
uv run pyverilog-auto delete -y rtl/ uart_wrap.v
echo "Done.  Result:"
cat uart_wrap.v
echo ""
echo ""

# ------------------------------------------------------------------
# 4) INDENT — re-indent a file
# ------------------------------------------------------------------
echo ">>> 10. INDENT — re-indent auto_reg_demo.v"
echo "$SEP"
uv run pyverilog-auto indent auto_reg_demo.v
echo "Done.  Result:"
cat auto_reg_demo.v
echo ""

echo "$SEP"
echo " All demos complete!"
echo "$SEP"
