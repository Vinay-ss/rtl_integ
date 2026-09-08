// Library file given with -v: only sync_ff is used by the design
module sync_ff (input clk, input rst_n, input d, output reg q);
   reg q_meta;
   always @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        q_meta <= 1'b0;
        q      <= 1'b0;
     end else begin
        q_meta <= d;
        q      <= q_meta;
     end
endmodule

module misc_unused (input a, output b);
   assign b = a;
endmodule
