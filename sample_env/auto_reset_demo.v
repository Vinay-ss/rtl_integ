// AUTORESET demo — auto-generates reset assignments
module auto_reset_demo (
   input        clk,
   input        rst_n,
   input  [7:0] data_in,
   output [7:0] data_out,
   output       data_valid
);

   reg [7:0] data_out;
   reg       data_valid;
   reg [3:0] counter;

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         /*AUTORESET*/
         // Beginning of autoreset for uninitialized flops
         counter <= 4'h0;
         data_out <= 8'h0;
         data_valid <= 1'h0;
         // End of automatics
      end else begin
         data_out   <= data_in;
         data_valid <= 1'b1;
         counter    <= counter + 1;
      end
   end

endmodule
