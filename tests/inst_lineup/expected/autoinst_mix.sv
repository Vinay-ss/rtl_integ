// AUTOINST with hand-written pins before the marker; all comment fields.
module autoinst_mix
  (input logic clk,
   input logic rst_n);

   /*AUTOWIRE*/
   // Beginning of automatic wires (for undeclared instantiated-module outputs)
   logic                a_really_long_status_port_name; // From u_core of lu_core.v
   wire                 busy;                   // From u_core of lu_core.v
   logic [15:0]         data_out;               // From u_core of lu_core.v
   logic                valid;                  // From u_core of lu_core.v
   // End of automatics

   lu_core u_core (.clk                            (clk),                            // input             logic
                   .rst_n                          (rst_n),                          // input             logic         // async reset
                   /*AUTOINST*/
                   // Outputs
                   .data_out                       (data_out[15:0]),                 // output [15:0]     logic
                   .valid                          (valid),                          // output            logic
                   .busy                           (busy),                           // output            reg
                   .a_really_long_status_port_name (a_really_long_status_port_name), // output            logic
                   // Inputs
                   .c_in                           (c_in[7:0]),                      // input  [7:0]      logic
                   .s_in                           (s_in[W-1:0]),                    // input  [W-1:0]    logic signed
                   .mem_in                         (mem_in/*[3:0].[0:1]*/),          // input  [3:0][0:1] wire
                   .pk_in                          (pk_in/*[1:0][7:0]*/));           // input  [1:0][7:0] logic

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
