// Non-ANSI AUTOARG-style top with two clusters and one memory block
module top (/*AUTOARG*/);
   input clk;
   input rst_n;

   /*AUTOINPUT*/
   /*AUTOOUTPUT*/
   /*AUTOLOGIC*/

   cluster u_cluster0 (/*AUTOINST*/);

   cluster u_cluster1 (/*AUTOINST*/);

   mem u_mem (/*AUTOINST*/);
endmodule

// Local Variables:
// verilog-typedef-regexp: "_t$"
// End:
