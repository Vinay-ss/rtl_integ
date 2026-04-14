@echo off
REM ============================================================
REM pyverilog-auto sample environment — demo runner (Windows)
REM ============================================================
REM Run from the sample_env\ directory:
REM   run_demo.bat
REM
REM Prerequisites:
REM   cd <project-root>
REM   uv pip install -e .
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo  pyverilog-auto  Demo Runner
echo ============================================================
echo.

echo --- 1. DIFF: preview changes on uart_wrap.v ---
uv run pyverilog-auto diff -y rtl/ uart_wrap.v
echo.

echo --- 2. EXPAND: uart_wrap.v (AUTOINST + AUTOWIRE + AUTOINPUT + AUTOOUTPUT + AUTOARG) ---
uv run pyverilog-auto expand -y rtl/ uart_wrap.v
echo Done.
type uart_wrap.v
echo.

echo --- 3. EXPAND: soc_top.v (full SoC) ---
uv run pyverilog-auto expand -y rtl/ soc_top.v
echo Done.
type soc_top.v
echo.

echo --- 4. EXPAND: auto_reg_demo.v (AUTOREG) ---
uv run pyverilog-auto expand auto_reg_demo.v
echo Done.
type auto_reg_demo.v
echo.

echo --- 5. EXPAND: auto_sense_demo.v (AUTOSENSE) ---
uv run pyverilog-auto expand auto_sense_demo.v
echo Done.
type auto_sense_demo.v
echo.

echo --- 6. EXPAND: auto_reset_demo.v (AUTORESET) ---
uv run pyverilog-auto expand auto_reset_demo.v
echo Done.
type auto_reset_demo.v
echo.

echo --- 7. EXPAND: auto_tieoff_demo.v (AUTOTIEOFF) ---
uv run pyverilog-auto expand auto_tieoff_demo.v
echo Done.
type auto_tieoff_demo.v
echo.

echo --- 8. EXPAND: auto_ascii_demo.v (AUTOASCIIENUM) ---
uv run pyverilog-auto expand auto_ascii_demo.v
echo Done.
type auto_ascii_demo.v
echo.

echo --- 9. DELETE: strip uart_wrap.v back to markers ---
uv run pyverilog-auto delete -y rtl/ uart_wrap.v
echo Done.
type uart_wrap.v
echo.

echo --- 10. INDENT: re-indent auto_reg_demo.v ---
uv run pyverilog-auto indent auto_reg_demo.v
echo Done.
type auto_reg_demo.v
echo.

echo ============================================================
echo  All demos complete!
echo ============================================================
