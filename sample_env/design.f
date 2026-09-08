// pyverilog-auto integration filelist for the sample SoC.
//   pyverilog-auto hierarchy -f sample_env/design.f --relative-to filelist --view all
//   pyverilog-auto integrate -f sample_env/design.f --relative-to filelist --diff
+libext+.v
-y rtl
uart_wrap.v
soc_top.v
auto_reg_demo.v
auto_sense_demo.v
auto_reset_demo.v
auto_tieoff_demo.v
auto_ascii_demo.v
