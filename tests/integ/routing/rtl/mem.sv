// Hand-written ANSI wrapper without any AUTO markers
module mem
  (input  logic       clk,
   input  logic       rst_n,
   output logic [7:0] rd,
   input  logic [7:0] wr);

   ctrl u_ctrl0 (.clk (clk), .rst_n (rst_n), .rd (rd), .wr (wr));
   ctrl u_ctrl1 (.clk (clk), .rst_n (rst_n), .rd (), .wr (wr));
   phy  u_phy (.a (clk));
endmodule
