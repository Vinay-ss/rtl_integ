// Leaf X (u_sub.u_leaf in wrap): the deeper end of two wrapper routes.
module leaf_x
  (input  logic       clk,
   input  logic       rst_n,
   input  logic       y,
   output logic [3:0] x);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) x <= '0;
     else        x <= x + {3'd0, y};
endmodule
