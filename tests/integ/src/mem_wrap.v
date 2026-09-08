// Non-ANSI wrapper in AUTOARG style with a deliberate black box (pad_cell has
// no source anywhere) and parameter-value substitution for AUTOINST.
module mem_wrap (/*AUTOARG*/);
   parameter DEPTH = 32;

   /*AUTOINPUT*/
   /*AUTOOUTPUT*/
   inout pad;

   /*AUTOWIRE*/

   sram_leaf #(.DEPTH(DEPTH), .DW(8)) u_sram (/*AUTOINST*/);

   pad_cell u_pad (.pad(pad), .core(dout[0]));
endmodule

// Local Variables:
// verilog-auto-inst-param-value:t
// End:
