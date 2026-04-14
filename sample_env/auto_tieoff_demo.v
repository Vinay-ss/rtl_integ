// AUTOTIEOFF demo — ties off unused outputs
module auto_tieoff_demo (
   input        clk,
   input        rst_n,
   output [7:0] port_a,
   output       port_b,
   output [3:0] port_c
);

   /*AUTOTIEOFF*/
   // Beginning of automatic tieoffs (for this module's unterminated outputs)
      wire                 port_b               = 1'h0;
      wire [3:0]           port_c               = 4'h0;
   // End of automatics

   // Only port_a is actually driven
   assign port_a = 8'hAB;

endmodule
