// Hierarchical integration fixture (use --relative-to filelist).
// Filelist order is deliberately top-first: the tool must reorder leaf-first.
+incdir+src/inc
+define+SIM=1
+libext+.v+.sv
-y ylib
-v lib/misc_lib.v
src/top.v
src/core_wrap.sv
src/mem_wrap.v
-F sub.f
