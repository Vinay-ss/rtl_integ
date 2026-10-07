// Simple valid/ready data-channel interface used by the routing demo.
interface axi_if #(parameter DW = 32) (input logic clk, input logic rst_n);
   logic [DW-1:0] wdata;
   logic          wvalid;
   logic          wready;
   modport master (output wdata, wvalid, input  wready);
   modport slave  (input  wdata, wvalid, output wready);
endinterface
