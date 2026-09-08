// ANSI wrapper: AUTOINPUT/AUTOOUTPUT inside the port list, an interface port,
// a parameter override on a leaf, and a library cell found through -y.
module core_wrap
  #(parameter WIDTH = 16)
  (bus_if.master m_bus,
   /*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   /*AUTOWIRE*/

   alu_leaf #(.WIDTH(WIDTH)) u_alu (/*AUTOINST*/);

   regfile_leaf u_rf (/*AUTOINST*/);

   clk_gate u_cg (/*AUTOINST*/);

   assign m_bus.req  = zero;
   assign m_bus.addr = {16{1'b0}};
endmodule
