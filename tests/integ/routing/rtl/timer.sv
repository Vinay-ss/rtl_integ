module timer (input logic clk, input logic rst_n, output logic tick);
   logic [3:0] cnt;
   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) cnt <= '0; else cnt <= cnt + 1'b1;
   assign tick = &cnt;
endmodule
