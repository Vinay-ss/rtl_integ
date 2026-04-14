// AUTOREG demo — declares regs for outputs driven in always blocks
module auto_reg_demo (
                      input        clk,
                      input        rst_n,
                      input  [3:0] sel,
                      output [7:0] result,
                      output       valid
                      );

   /*AUTOREG*/
   // Beginning of automatic regs (for this module's undeclared outputs)
   reg [7:0]            result;
   reg                  valid;
   // End of automatics

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         result <= 8'd0;
         valid  <= 1'b0;
      end else begin
         result <= {4'd0, sel};
         valid  <= |sel;
      end
   end

endmodule
