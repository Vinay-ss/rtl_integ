// Non-ANSI AUTOARG-style top with two clusters and one memory block
module top (/*AUTOARG*/
            // Outputs
            y, rd, irq, data,
            // Inputs
            wr, a, clk, rst_n
            );
   input clk;
   input rst_n;

   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input logic [7:0]    a;                      // To u_cluster0 of cluster.v, ...
   input logic [7:0]    wr;                     // To u_mem of mem.v
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output logic [DW-1:0] data;                  // From u_cluster0 of cluster.v, ...
   output logic         irq;                    // From u_cluster0 of cluster.v, ...
   output logic [7:0]   rd;                     // From u_mem of mem.v
   output logic [7:0]   y;                      // From u_cluster0 of cluster.v, ...
   // End of automatics
   /*AUTOLOGIC*/
   // Beginning of automatic wires (for undeclared instantiated-module outputs)
   logic [3:0]          cfg;                    // From u_mem of mem.v
   req_pkg::req_t       req;                    // From u_cluster0 of cluster.v
   logic                tick;                   // From u_cluster0 of cluster.v
   // End of automatics
   axi_if m_axi_0 (.clk (clk), .rst_n (rst_n));   // routed: axi
   axi_if m_axi_1 (.clk (clk), .rst_n (rst_n));   // routed: axi

   cluster u_cluster0 (
                       .m_axi           (m_axi_0),   // routed: axi
                       .core_busy       (),   // routed: unconnected here
                       /*AUTOINST*/
                       // Outputs
                       .data            (data[DW-1:0]),
                       .irq             (irq),
                       .req             (req),
                       .tick            (tick),
                       .y               (y[7:0]),
                       // Inputs
                       .a               (a[7:0]),
                       .cfg             (cfg[3:0]),
                       .clk             (clk),
                       .rst_n           (rst_n));

   cluster u_cluster1 (
                       .m_axi           (m_axi_1),   // routed: axi
                       .tick            (),   // routed: unconnected here
                       .req             (),   // routed: unconnected here
                       .core_busy       (),   // routed: unconnected here
                       /*AUTOINST*/
                       // Outputs
                       .data            (data[DW-1:0]),
                       .irq             (irq),
                       .y               (y[7:0]),
                       // Inputs
                       .a               (a[7:0]),
                       .cfg             (cfg[3:0]),
                       .clk             (clk),
                       .rst_n           (rst_n));

   mem u_mem (
              .m_axi_0                  (m_axi_0),   // routed: axi
              .m_axi_1                  (m_axi_1),   // routed: axi
              /*AUTOINST*/
              // Outputs
              .rd                       (rd[7:0]),
              .cfg                      (cfg[3:0]),
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n),
              .wr                       (wr[7:0]),
              .tick                     (tick),
              .req                      (req));
endmodule

// Local Variables:
// verilog-typedef-regexp: "_t$"
// End:
