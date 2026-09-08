// A second top (for the different-tops error case and unrouted-instance warnings)
module sim_top;
   logic clk, rst_n;
   mem u_mem (.clk (clk), .rst_n (rst_n), .rd (), .wr (8'd0));
endmodule
