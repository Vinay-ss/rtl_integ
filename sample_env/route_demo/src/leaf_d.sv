// Leaf D (instance instD under coreA): exposes its status at the very top as 'status_d'.
module leaf_d
  (input  logic       clk,
   input  logic       rst_n,
   input  logic [7:0] d_in,
   output logic [7:0] d_out,
   output logic       d_status);

   //auto_route d_status :: to :: top:status_d

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        d_out    <= '0;
        d_status <= 1'b0;
     end else begin
        d_out    <= d_in + 8'd1;
        d_status <= |d_in;
     end
endmodule
