// Leaf F (instance instF under coreB): second sink of the data channel.
module leaf_f
  (input  logic       clk,
   input  logic       rst_n,
   axi_if.slave       data_ch,
   output logic [7:0] f_cnt);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        data_ch.wready <= 1'b0;
        f_cnt          <= '0;
     end else begin
        data_ch.wready <= 1'b1;
        f_cnt          <= f_cnt + data_ch.wvalid;
     end
endmodule
