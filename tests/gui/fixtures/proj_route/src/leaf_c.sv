// Leaf C (instance instC under coreA): drives the data channel, consumes a control line.
// The //auto_route annotations apply to EVERY instance of leaf_c and are collected by
//   pyverilog-auto route --collect
module leaf_c
  (input  logic       clk,
   input  logic       rst_n,
   input  logic [7:0] c_in,
   output logic       c_busy,
   input  logic       ctrl_ch,      // driven by instE's irq  (annotation below)
   axi_if.master      data_ch);     // fanned out to instE and instF (annotation below)

   //auto_route data_ch :: to :: instE, instF
   //auto_route ctrl_ch :: from :: instE:irq

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        data_ch.wvalid <= 1'b0;
        data_ch.wdata  <= '0;
        c_busy         <= 1'b0;
     end else begin
        data_ch.wvalid <= ~ctrl_ch;
        data_ch.wdata  <= {24'd0, c_in};
        c_busy         <= data_ch.wvalid & ~data_ch.wready;
     end
endmodule
