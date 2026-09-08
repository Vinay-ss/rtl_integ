// ANSI AUTO-style wrapper (AUTOINPUT/AUTOOUTPUT in the port list)
module core
  (
   axi_if.master m_axi,   // routed: axi
   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input logic [7:0]    a,                      // To u_acc of acc.v
   input logic [3:0]    cfg,                    // To u_dma of dma.v
   input logic          clk,                    // To u_dma of dma.v, ...
   input logic          rst_n,                  // To u_dma of dma.v, ...
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output logic         busy,                   // From u_dma of dma.v
   output logic [DW-1:0] data,                  // From u_dma of dma.v
   output logic         irq,                    // From u_dma of dma.v
   output req_pkg::req_t req,                   // From u_dma of dma.v
   output logic [7:0]   y                      // From u_acc of acc.v
   // End of automatics
   );

   /*AUTOWIRE*/

   dma u_dma (/*AUTOINST*/
              // Interfaces
              .m_axi                    (m_axi.master),
              // Outputs
              .irq                      (irq),
              .busy                     (busy),
              .req                      (req),
              .data                     (data[DW-1:0]),
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n),
              .cfg                      (cfg[3:0]));

   acc u_acc (/*AUTOINST*/
              // Outputs
              .y                        (y[7:0]),
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n),
              .a                        (a[7:0]));
endmodule

// Local Variables:
// verilog-typedef-regexp: "_t$"
// End:
