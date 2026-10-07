// Leaf A (instA, AUTOINST in wrap): data/sync source, bus master, drives u_sub.u_leaf's y.
module leaf_a
  (input  logic       clk,
   input  logic       rst_n,
   output logic [7:0] a_data,
   output logic       a_sync,
   output logic       a_y,
   axi_if.master      m_bus);

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        a_data <= '0;
        a_sync <= 1'b0;
     end else begin
        a_data <= a_data + 8'd1;
        a_sync <= ~a_sync;
     end

   assign a_y         = a_sync & m_bus.ready;
   assign m_bus.valid = a_sync;
   assign m_bus.data  = {24'd0, a_data};
endmodule
