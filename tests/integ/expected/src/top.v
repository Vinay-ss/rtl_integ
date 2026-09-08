// Top level in AUTOARG style: two cores in a generate loop sharing one
// interface instance, a memory wrapper, and a -v library synchronizer.
module top (/*AUTOARG*/
            // Outputs
            zero, y, rdata, q, gclk, dout,
            // Inputs
            we, wdata, waddr, raddr, op, en, din, d, b, addr, a, clk, rst_n
            );
   input clk;
   input rst_n;

   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input signed [WIDTH-1:0] a;                  // To u_core of core_wrap.v
   input [AW-1:0]       addr;                   // To u_mem of mem_wrap.v
   input signed [WIDTH-1:0] b;                  // To u_core of core_wrap.v
   input                d;                      // To u_sync of sync_ff.v
   input [7:0]          din;                    // To u_mem of mem_wrap.v
   input                en;                     // To u_core of core_wrap.v
   input [`ALU_OPW-1:0] op;                     // To u_core of core_wrap.v
   input [3:0]          raddr;                  // To u_core of core_wrap.v
   input [3:0]          waddr;                  // To u_core of core_wrap.v
   input [7:0]          wdata;                  // To u_core of core_wrap.v
   input                we;                     // To u_core of core_wrap.v, ...
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output [7:0]         dout;                   // From u_mem of mem_wrap.v
   output               gclk;                   // From u_core of core_wrap.v
   output               q;                      // From u_sync of sync_ff.v
   output [7:0]         rdata;                  // From u_core of core_wrap.v
   output signed [WIDTH-1:0] y;                 // From u_core of core_wrap.v
   output               zero;                   // From u_core of core_wrap.v
   // End of automatics
   /*AUTOWIRE*/
   // Beginning of automatic wires (for undeclared instantiated-module outputs)
   wire                 pad;                    // To/From u_mem of mem_wrap.v
   // End of automatics

   bus_if m_bus (.clk(clk));

   genvar i;
   generate
      for (i = 0; i < 2; i = i + 1) begin : gen_cores
         core_wrap u_core (/*AUTOINST*/
                           // Interfaces
                           .m_bus               (m_bus.master),
                           // Outputs
                           .gclk                (gclk),
                           .rdata               (rdata[7:0]),
                           .y                   (y[WIDTH-1:0]),
                           .zero                (zero),
                           // Inputs
                           .a                   (a[WIDTH-1:0]),
                           .b                   (b[WIDTH-1:0]),
                           .clk                 (clk),
                           .en                  (en),
                           .op                  (op[`ALU_OPW-1:0]),
                           .raddr               (raddr[3:0]),
                           .rst_n               (rst_n),
                           .waddr               (waddr[3:0]),
                           .wdata               (wdata[7:0]),
                           .we                  (we));
      end
   endgenerate

   mem_wrap u_mem (/*AUTOINST*/
                   // Outputs
                   .dout                (dout[7:0]),
                   // Inouts
                   .pad                 (pad),
                   // Inputs
                   .addr                (addr[AW-1:0]),
                   .clk                 (clk),
                   .din                 (din[7:0]),
                   .we                  (we));

   sync_ff u_sync (/*AUTOINST*/
                   // Outputs
                   .q                   (q),
                   // Inputs
                   .clk                 (clk),
                   .rst_n               (rst_n),
                   .d                   (d));
endmodule
