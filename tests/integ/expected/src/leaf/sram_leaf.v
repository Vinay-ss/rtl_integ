// ANSI leaf with a derived parameter
module sram_leaf #(parameter DEPTH = 16,
                   parameter AW = $clog2(DEPTH),
                   parameter DW = 8)
  (input           clk,
   input  [AW-1:0] addr,
   input  [DW-1:0] din,
   input           we,
   output [DW-1:0] dout);

   reg [DW-1:0] mem [0:DEPTH-1];
   always @(posedge clk)
     if (we) mem[addr] <= din;
   assign dout = mem[addr];
endmodule
