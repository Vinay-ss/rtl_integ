// AUTOSENSE demo — auto-generates sensitivity lists for combinational blocks
module auto_sense_demo (
   input  [3:0] a,
   input  [3:0] b,
   input        sel,
   output [3:0] y
);

   reg [3:0] y;

   always @(/*AUTOSENSE*/a or b or sel) begin
      if (sel)
        y = a + b;
      else
        y = a - b;
   end

endmodule
