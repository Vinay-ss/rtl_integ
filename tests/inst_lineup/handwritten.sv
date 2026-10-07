// Fully hand-written instance: several pins on one line, a long port
// name, a user comment and a fixed comment column.
module handwritten;

   logic        clk, rst_n, valid, busy, status;
   logic [7:0]  c_in;
   logic [15:0] data;

   lu_core #(.W(8), .DEPTH(16)) u_core
     (.clk(clk), .rst_n(rst_n),
      .c_in(c_in), .s_in(8'sd0),
      .mem_in('{default: '0}), .pk_in('0),
      .data_out(data),
      .valid(valid), .busy(busy),   // input from ctrl
      .a_really_long_status_port_name(status));

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// verilog-auto-inst-comment-column: 64
// End:
