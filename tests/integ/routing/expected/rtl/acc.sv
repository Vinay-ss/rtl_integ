module acc (input logic clk, input logic rst_n, input logic [7:0] a, output logic [7:0] y);
   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) y <= '0; else y <= y + a;
endmodule
