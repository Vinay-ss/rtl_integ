// stage: a register slice with a busy flag (plain source, not templated).
module stage #(parameter W = 8)
  (input  logic         clk,
   input  logic         rst_n,
   input  logic [W-1:0] d,
   output logic [W-1:0] q,
   output logic         busy);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) q <= '0;
     else        q <= d;

   assign busy = |q;
endmodule
