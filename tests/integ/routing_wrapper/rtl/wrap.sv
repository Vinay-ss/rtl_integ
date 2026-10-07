// wrap: instantiated twice under top (u_w0, u_w1). instA, instB and u_sub use AUTOINST,
// instC has hand-written pins. The wrapper-level //auto_route lines below connect the
// children; each one applies to the children of both wrapper instances.
module wrap
  (/*AUTOINPUT*/
   input logic clk,
   input logic rst_n);

   /*AUTOWIRE*/

   leaf_a instA (/*AUTOINST*/);

   leaf_b instB (/*AUTOINST*/);

   leaf_c instC (.clk   (clk),
                 .rst_n (rst_n));

   sub u_sub (/*AUTOINST*/);

   // signal with renamed ports, a 'from', a fan-out and an interface
   //auto_route instA:a_data :: to :: instB:b_data
   //auto_route instB:b_go :: from :: instC:c_done
   //auto_route instA:a_sync :: to :: instB:b_sync, instC:c_sync
   //auto_route instA:m_bus :: to :: instB:s_bus
   // a deeper path, and a regex LEFT written from the top (full match)
   //auto_route u_sub.u_leaf:x :: to :: instC:c_x
   //auto_route re:top\.u_w.\.u_sub\.u_leaf:y :: from :: instA:a_y
   // cross-coupled pair: two different drivers, two different nets
   //auto_route instB:err :: to :: instC:err_in
   //auto_route instC:err :: to :: instB:err_in
endmodule
