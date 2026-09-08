// ANSI AUTO-style wrapper with one core and one control block
module cluster
  (
   axi_if.master m_axi,   // routed: axi
   output logic core_busy,   // routed: busy
   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input logic [7:0]    a,                      // To u_core of core.v
   input logic [3:0]    cfg,                    // To u_core of core.v
   input logic          clk,                    // To u_core of core.v, ...
   input logic          rst_n,                  // To u_core of core.v, ...
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output logic [DW-1:0] data,                  // From u_core of core.v
   output logic         irq,                    // From u_core of core.v
   output req_pkg::req_t req,                   // From u_core of core.v
   output logic         tick,                   // From u_ctl of cctl.v
   output logic [7:0]   y                      // From u_core of core.v
   // End of automatics
   );

   /*AUTOWIRE*/

   core u_core (
                .busy                   (core_busy),   // routed: busy
                /*AUTOINST*/
                // Interfaces
                .m_axi                  (m_axi.master),
                // Outputs
                .data                   (data[DW-1:0]),
                .irq                    (irq),
                .req                    (req),
                .y                      (y[7:0]),
                // Inputs
                .a                      (a[7:0]),
                .cfg                    (cfg[3:0]),
                .clk                    (clk),
                .rst_n                  (rst_n));

   cctl u_ctl (/*AUTOINST*/
               // Outputs
               .tick                    (tick),
               // Inputs
               .clk                     (clk),
               .rst_n                   (rst_n));
endmodule

// Local Variables:
// verilog-typedef-regexp: "_t$"
// End:
