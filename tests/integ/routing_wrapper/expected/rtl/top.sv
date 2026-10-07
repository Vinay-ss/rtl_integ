// top: instantiates the wrapper twice; every //auto_route in wrap applies to u_w0 and u_w1.
module top
  (input logic clk,
   input logic rst_n);

   wrap u_w0 (/*AUTOINST*/
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n));

   wrap u_w1 (/*AUTOINST*/
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n));
endmodule
