// Non-ANSI leaf in AUTOARG style
module regfile_leaf (/*AUTOARG*/
                     // Outputs
                     rdata,
                     // Inputs
                     clk, we, waddr, wdata, raddr
                     );
   input        clk;
   input        we;
   input [3:0]  waddr;
   input [7:0]  wdata;
   input [3:0]  raddr;
   output [7:0] rdata;

   reg [7:0] mem [0:15];
   always @(posedge clk)
     if (we) mem[waddr] <= wdata;
   assign rdata = mem[raddr];
endmodule
