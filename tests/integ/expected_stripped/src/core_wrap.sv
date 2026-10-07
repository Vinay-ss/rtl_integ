// ANSI wrapper: AUTOINPUT/AUTOOUTPUT inside the port list, an interface port,
// a parameter override on a leaf, and a library cell found through -y.
module core_wrap
  #(parameter WIDTH = 16)
  (bus_if.master m_bus,
   input signed [WIDTH-1:0] a,                  // To u_alu of alu_leaf.v
   input signed [WIDTH-1:0] b,                  // To u_alu of alu_leaf.v
   input                clk,                    // To u_alu of alu_leaf.v, ...
   input                en,                     // To u_cg of clk_gate.v
   input [`ALU_OPW-1:0] op,                     // To u_alu of alu_leaf.v
   input [3:0]          raddr,                  // To u_rf of regfile_leaf.v
   input                rst_n,                  // To u_alu of alu_leaf.v
   input [3:0]          waddr,                  // To u_rf of regfile_leaf.v
   input [7:0]          wdata,                  // To u_rf of regfile_leaf.v
   input                we,                     // To u_rf of regfile_leaf.v
   output               gclk,                   // From u_cg of clk_gate.v
   output [7:0]         rdata,                  // From u_rf of regfile_leaf.v
   output signed [WIDTH-1:0] y,                 // From u_alu of alu_leaf.v
   output               zero                   // From u_alu of alu_leaf.v
   );

   alu_leaf #(.WIDTH(WIDTH)) u_alu (
                                    .y                  (y[WIDTH-1:0]),
                                    .zero               (zero),
                                    .clk                (clk),
                                    .rst_n              (rst_n),
                                    .op                 (op[`ALU_OPW-1:0]),
                                    .a                  (a[WIDTH-1:0]),
                                    .b                  (b[WIDTH-1:0]));

   regfile_leaf u_rf (
                      .rdata            (rdata[7:0]),
                      .clk              (clk),
                      .we               (we),
                      .waddr            (waddr[3:0]),
                      .wdata            (wdata[7:0]),
                      .raddr            (raddr[3:0]));

   clk_gate u_cg (
                  .gclk                 (gclk),
                  .clk                  (clk),
                  .en                   (en));

   assign m_bus.req  = zero;
   assign m_bus.addr = {16{1'b0}};
endmodule
