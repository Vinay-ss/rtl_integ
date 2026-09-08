// Simple request/acknowledge bus interface with modports and a clocking block
interface bus_if (input logic clk);
   logic        req;
   logic        ack;
   logic [15:0] addr;
   modport master (output req, addr, input ack);
   modport slave  (input req, addr, output ack);
   clocking cb @(posedge clk);
      input  ack;
      output req, addr;
   endclocking
   modport mon (clocking cb);
endinterface
