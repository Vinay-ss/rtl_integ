// Non-ANSI wrapper in AUTOARG style with a deliberate black box (pad_cell has
// no source anywhere) and parameter-value substitution for AUTOINST.
module mem_wrap (
                 dout,
                 pad,
                 we, din, clk, addr
                 );
   parameter DEPTH = 32;

   input [AW-1:0]       addr;                   // To u_sram of sram_leaf.v
   input                clk;                    // To u_sram of sram_leaf.v
   input [7:0]          din;                    // To u_sram of sram_leaf.v
   input                we;                     // To u_sram of sram_leaf.v
   output [7:0]         dout;                   // From u_sram of sram_leaf.v
   inout pad;

   sram_leaf #(.DEPTH(DEPTH), .DW(8)) u_sram (
                                              .dout             (dout[7:0]),
                                              .clk              (clk),
                                              .addr             (addr[AW-1:0]),
                                              .din              (din[7:0]),
                                              .we               (we));

   pad_cell u_pad (.pad(pad), .core(dout[0]));
endmodule
