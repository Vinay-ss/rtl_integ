// wrap: instantiated twice under top (u_w0, u_w1). instA, instB and u_sub use AUTOINST,
// instC has hand-written pins. The wrapper-level //auto_route lines below connect the
// children; each one applies to the children of both wrapper instances.
module wrap
  (/*AUTOINPUT*/
   input logic clk,
   input logic rst_n);

   /*AUTOWIRE*/
   logic [7:0] b_data;   // routed: instA.a_data->instB.b_data
   logic c_done;   // routed: instC.c_done->instB.b_go
   logic a_sync;   // routed: instA.a_sync->instB.b_sync
   axi_if s_bus (.clk (clk), .rst_n (rst_n));   // routed: instA.m_bus->instB.s_bus
   logic [3:0] c_x;   // routed: u_sub.u_leaf.x->instC.c_x
   logic a_y;   // routed: instA.a_y->u_sub.u_leaf.y
   logic instB_err;   // routed: instB.err->instC.err_in
   logic instC_err;   // routed: instC.err->instB.err_in

   leaf_a instA (
                 .a_data                (b_data),   // routed: instA.a_data->instB.b_data
                 .a_sync                (a_sync),   // routed: instA.a_sync->instB.b_sync
                 .m_bus                 (s_bus),   // routed: instA.m_bus->instB.s_bus
                 .a_y                   (a_y),   // routed: instA.a_y->u_sub.u_leaf.y
                 /*AUTOINST*/
                 // Inputs
                 .clk                   (clk),
                 .rst_n                 (rst_n));

   leaf_b instB (
                 .b_data                (b_data),   // routed: instA.a_data->instB.b_data
                 .b_go                  (c_done),   // routed: instC.c_done->instB.b_go
                 .b_sync                (a_sync),   // routed: instA.a_sync->instB.b_sync
                 .s_bus                 (s_bus),   // routed: instA.m_bus->instB.s_bus
                 .err                   (instB_err),   // routed: instB.err->instC.err_in
                 .err_in                (instC_err),   // routed: instC.err->instB.err_in
                 /*AUTOINST*/
                 // Inputs
                 .clk                   (clk),
                 .rst_n                 (rst_n));

   leaf_c instC (.clk   (clk),
                 .rst_n (rst_n),
                 .c_done                (c_done),   // routed: instC.c_done->instB.b_go
                 .c_sync                (a_sync),   // routed: instA.a_sync->instC.c_sync
                 .c_x                   (c_x),   // routed: u_sub.u_leaf.x->instC.c_x
                 .err_in                (instB_err),   // routed: instB.err->instC.err_in
                 .err                   (instC_err)   // routed: instC.err->instB.err_in
                );

   sub u_sub (
              .x                        (c_x),   // routed: u_sub.u_leaf.x->instC.c_x
              .y                        (a_y),   // routed: instA.a_y->u_sub.u_leaf.y
              /*AUTOINST*/
              // Inputs
              .clk                      (clk),
              .rst_n                    (rst_n));

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
