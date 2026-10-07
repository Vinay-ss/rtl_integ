// .* instance next to explicit pins.
module dotstar
  (input  logic        clk,
   input  logic        rst_n,
   input  logic [7:0]  c_in,
   output logic [15:0] data_out);

   lu_core u_core (.valid                          (),                      // output            logic
                   .busy                           (),                      // output            reg
                   .a_really_long_status_port_name (),                      // output            logic
                   .*,
                   // Outputs
                   .data_out                       (data_out[15:0]),        // output [15:0]     logic         // Implicit .*
                   // Inputs
                   .clk                            (clk),                   // input             logic         // Implicit .*
                   .rst_n                          (rst_n),                 // input             logic         // Implicit .*
                   .c_in                           (c_in[7:0]),             // input  [7:0]      logic         // Implicit .*
                   .s_in                           (s_in[W-1:0]),           // input  [W-1:0]    logic signed  // Implicit .*
                   .mem_in                         (mem_in/*[3:0].[0:1]*/), // input  [3:0][0:1] wire          // Implicit .*
                   .pk_in                          (pk_in/*[1:0][7:0]*/));  // input  [1:0][7:0] logic         // Implicit .*

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
