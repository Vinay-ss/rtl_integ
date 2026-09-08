// Leaf E (instance instE under coreB): sinks the data channel, raises irq.
module leaf_e
  (input  logic       clk,
   input  logic       rst_n,
   axi_if.slave       data_ch,
   output logic       irq,
   output logic [7:0] e_cnt);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        data_ch.wready <= 1'b0;
        irq            <= 1'b0;
        e_cnt          <= '0;
     end else begin
        data_ch.wready <= 1'b1;
        irq            <= data_ch.wvalid & (data_ch.wdata[7:0] == 8'hFF);
        e_cnt          <= e_cnt + (data_ch.wvalid & data_ch.wready);
     end
endmodule
