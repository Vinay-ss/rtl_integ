// Leaves of the AUTO-style fixture (plain sources).
interface ifc (input logic clk);
   logic       v;
   logic [7:0] d;
   modport m (output v, d);
   modport s (input  v, d);
endinterface

module prod
  (input  logic       clk,
   input  logic       rst_n,
   input  logic [7:0] din,
   input  logic       ack,
   output logic [7:0] mid,
   ifc.m              bus);
   assign mid = din;
   assign bus.v = ~ack;
   assign bus.d = din;
endmodule

module cons
  (input  logic       clk,
   input  logic [7:0] mid,
   output logic [7:0] dout,
   output logic       ack,
   ifc.s              bus);
   assign dout = mid ^ bus.d;
   assign ack = bus.v;
endmodule

module sink
  (input  logic       clk,
   input  logic [7:0] mid);
endmodule
