// .* instance next to explicit pins.
module dotstar
  (input  logic        clk,
   input  logic        rst_n,
   input  logic [7:0]  c_in,
   output logic [15:0] data_out);

   lu_core u_core (.valid(),
                   .busy(),
                   .a_really_long_status_port_name(),
                   .*);

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
