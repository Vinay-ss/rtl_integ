// AUTOINST with hand-written pins before the marker; all comment fields.
module autoinst_mix
  (input logic clk,
   input logic rst_n);

   /*AUTOWIRE*/

   lu_core u_core (.clk(clk),
                   .rst_n (rst_n),   // async reset
                   /*AUTOINST*/);

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
