@echo off
REM ------------------------------------------------------------------
REM pyverilog-auto routing demo (see README.md in this directory)
REM   run_route_demo.bat                 uses "uv run pyverilog-auto"
REM   set PVA=python -m pyverilog_auto   before running to use a plain python
REM ------------------------------------------------------------------
cd /d "%~dp0"
if "%PVA%"=="" set PVA=uv run pyverilog-auto
set F=-f work\design.f --relative-to filelist

echo === 0. fresh working copy: src to work
if exist work rmdir /s /q work
mkdir work
copy /y src\* work\ >nul

echo.
echo === 1. hierarchy (instance tree, file levels, leaf-first order)
%PVA% hierarchy %F% --view all

echo.
echo === 2. collect the //auto_route annotations into work\routes.toml (nothing applied yet)
%PVA% route %F% --collect-only --routes-out work\routes.toml
echo --- work\routes.toml:
type work\routes.toml

echo.
echo === 3. dry run: show what the routes would change
%PVA% route %F% --routes work\routes.toml --dry-run

echo.
echo === 4. apply the routes, then expand all AUTOs leaf-first
%PVA% route %F% --routes work\routes.toml --then-expand

echo.
echo === 5. results
for %%f in (top.v core_a.sv core_b.sv) do (
  echo --- work\%%f
  type work\%%f
  echo.
)

echo === 6. run again: no edits (idempotent)
%PVA% route %F% --routes work\routes.toml --then-expand
