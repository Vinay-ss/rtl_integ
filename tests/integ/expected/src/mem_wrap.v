// Non-ANSI wrapper in AUTOARG style with a deliberate black box (pad_cell has
// no source anywhere) and parameter-value substitution for AUTOINST.
module mem_wrap (/*AUTOARG*/
                 // Outputs
                 dout,
                 // Inouts
                 pad,
                 // Inputs
                 we, din, clk, addr
                 );
   parameter DEPTH = 32;

   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input [AW-1:0]       addr;                   // To u_sram of sram_leaf.v
   input                clk;                    // To u_sram of sram_leaf.v
   input [7:0]          din;                    // To u_sram of sram_leaf.v
   input                we;                     // To u_sram of sram_leaf.v
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output [7:0]         dout;                   // From u_sram of sram_leaf.v
   // End of automatics
   inout pad;

   /*AUTOWIRE*/

   sram_leaf #(.DEPTH(DEPTH), .DW(8)) u_sram (/*AUTOINST*/
                                              // Outputs
                                              .dout             (dout[7:0]),
                                              // Inputs
                                              .clk              (clk),
                                              .addr             (addr[AW-1:0]),
                                              .din              (din[7:0]),
                                              .we               (we));

   pad_cell u_pad (.pad(pad), .core(dout[0]));
endmodule

// Local Variables:
// verilog-auto-inst-param-value:t
// End:
