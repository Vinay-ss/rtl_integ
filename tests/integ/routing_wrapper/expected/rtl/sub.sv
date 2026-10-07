// sub: AUTO-style sub-wrapper (u_sub inside wrap) around u_leaf; x and y come from AUTOOUTPUT/AUTOINPUT.
module sub
  (/*AUTOINPUT*/
  // Beginning of automatic inputs (from unused autoinst inputs)
  input logic           y,                      // To u_leaf of leaf_x.v
  // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output logic [3:0]   x,                      // From u_leaf of leaf_x.v
   // End of automatics
   input logic clk,
   input logic rst_n);

   leaf_x u_leaf (/*AUTOINST*/
                  // Outputs
                  .x                    (x[3:0]),
                  // Inputs
                  .clk                  (clk),
                  .rst_n                (rst_n),
                  .y                    (y));
endmodule
