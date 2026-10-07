// Fully hand-written instance: several pins on one line, a long port
// name, a user comment and a fixed comment column.
module handwritten;

   logic        clk, rst_n, valid, busy, status;
   logic [7:0]  c_in;
   logic [15:0] data;

   lu_core #(.W                         (8),                    // parameter int
             .DEPTH                     (16)) u_core
     (.clk                              (clk),                  // input             logic
      .rst_n                            (rst_n),                // input             logic
      .c_in                             (c_in),                 // input  [7:0]      logic
      .s_in                             (8'sd0),                // input  [W-1:0]    logic signed
      .mem_in                           ('{default: '0}),       // input  [3:0][0:1] wire
      .pk_in                            ('0),                   // input  [1:0][7:0] logic
      .data_out                         (data),                 // output [15:0]     logic
      .valid                            (valid),                // output            logic
      .busy                             (busy),                 // output            reg           // input from ctrl
      .a_really_long_status_port_name   (status));              // output            logic

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// verilog-auto-inst-comment-column: 64
// End:
