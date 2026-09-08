// Found through -y ylib +libext+.v
module clk_gate (input clk, input en, output gclk);
   assign gclk = clk & en;
endmodule
