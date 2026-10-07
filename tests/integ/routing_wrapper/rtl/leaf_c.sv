// Leaf C (instC, hand-written pins in wrap): fan-out sink, consumes u_sub.u_leaf's x, drives instB's b_go.
module leaf_c
  (input  logic       clk,
   input  logic       rst_n,
   input  logic       c_sync,
   input  logic [3:0] c_x,
   input  logic       err_in,
   output logic       err,
   output logic       c_done);

   assign c_done = c_sync & (c_x != 4'd0);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) err <= 1'b0;
     else        err <= err_in ^ c_sync;
endmodule
