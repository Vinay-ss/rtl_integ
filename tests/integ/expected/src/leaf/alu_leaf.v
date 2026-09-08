// ANSI leaf: parameterized, signed ports, macro-defined width from an include
`include "alu_defs.vh"
module alu_leaf #(parameter WIDTH = 8)
  (input                          clk,
   input                          rst_n,
   input      [`ALU_OPW-1:0]      op,
   input      signed [WIDTH-1:0]  a,
   input      signed [WIDTH-1:0]  b,
   output reg signed [WIDTH-1:0]  y,
   output reg                     zero);

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         y    <= {WIDTH{1'b0}};
         zero <= 1'b0;
      end else begin
         case (op)
           `ALU_OPW'd0: y <= a + b;
           `ALU_OPW'd1: y <= a - b;
           `ALU_OPW'd2: y <= a & b;
           default:     y <= a | b;
         endcase
         zero <= (y == {WIDTH{1'b0}});
      end
   end
endmodule
