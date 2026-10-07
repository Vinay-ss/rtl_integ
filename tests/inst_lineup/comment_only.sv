// Port comments without lineup, dir and width only: multi-pin lines are
// split, the ( spacing stays as written.
module comment_only;

   logic        clk, rst_n, valid;
   logic [7:0]  c_in;
   logic [15:0] data;

   lu_core u_core (.clk(clk), .rst_n(rst_n), .c_in (c_in),
                   .data_out  (data),
                   .valid(valid));

endmodule

// Local Variables:
// verilog-auto-inst-port-comment: "dir width"
// End:
