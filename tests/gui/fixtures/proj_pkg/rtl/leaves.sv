module leaf_op
  import p::*;
#(
  parameter op_e DEF = OP_A,
  parameter type T   = pkt_t
) (
  input  logic clk,
  input  T     din,
  output T     dout,
  output op_e  op
);
  always_ff @(posedge clk) dout <= din;
  assign op = DEF;
endmodule

module leaf_sink (
  input  logic           clk,
  input  p::pkt_t        pin,
  output logic [p::W-1:0] y
);
  assign y = pin.d;
endmodule
