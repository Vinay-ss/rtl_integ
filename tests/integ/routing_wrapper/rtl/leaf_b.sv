// Leaf B (instB, AUTOINST in wrap): sinks A's data, sync and bus; err is cross-coupled with instC.
module leaf_b
  (input  logic       clk,
   input  logic       rst_n,
   input  logic [7:0] b_data,
   input  logic       b_sync,
   input  logic       b_go,
   input  logic       err_in,
   output logic       err,
   axi_if.slave       s_bus);

   assign s_bus.ready = b_go & ~err_in;

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) err <= 1'b0;
     else        err <= b_sync & s_bus.valid & (b_data == s_bus.data[7:0]);
endmodule
