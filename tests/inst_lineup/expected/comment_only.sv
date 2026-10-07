// Port comments without lineup, dir and width only: multi-pin lines are
// split, the ( spacing stays as written.
module comment_only;

   logic        clk, rst_n, valid;
   logic [7:0]  c_in;
   logic [15:0] data;

   lu_core u_core (.clk(clk),         // input
                   .rst_n(rst_n),     // input
                   .c_in (c_in),      // input  [7:0]
                   .data_out  (data), // output [15:0]
                   .valid(valid));    // output

endmodule

// Local Variables:
// verilog-auto-inst-port-comment: "dir width"
// End:
