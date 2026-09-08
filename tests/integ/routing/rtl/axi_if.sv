interface axi_if #(parameter DW = 32) (input logic clk, input logic rst_n);
   logic          valid;
   logic          ready;
   logic [DW-1:0] data;
   modport master (output valid, data, input ready);
   modport slave  (input valid, data, output ready);
endinterface
