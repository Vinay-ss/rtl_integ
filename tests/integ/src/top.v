// Top level in AUTOARG style: two cores in a generate loop sharing one
// interface instance, a memory wrapper, and a -v library synchronizer.
module top (/*AUTOARG*/);
   input clk;
   input rst_n;

   /*AUTOINPUT*/
   /*AUTOOUTPUT*/
   /*AUTOWIRE*/

   bus_if m_bus (.clk(clk));

   genvar i;
   generate
      for (i = 0; i < 2; i = i + 1) begin : gen_cores
         core_wrap u_core (/*AUTOINST*/);
      end
   endgenerate

   mem_wrap u_mem (/*AUTOINST*/);

   sync_ff u_sync (/*AUTOINST*/);
endmodule
