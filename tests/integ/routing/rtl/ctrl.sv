// Hand-written ANSI leaf: the destination of most routes
module ctrl
  (input  logic          clk,
   input  logic          rst_n,
   axi_if.slave          s_axi,
   input  logic          irq_in,
   input  logic          tick_in,
   input  req_pkg::req_t req_in,
   output logic          err,
   input  logic          err_in,
   output logic [3:0]    cfg_o,
   output logic [7:0]    rd,
   input  logic [7:0]    wr);

   assign s_axi.ready = 1'b1;
   assign err   = irq_in & tick_in & err_in;
   assign cfg_o = req_in.id;
   assign rd    = wr;
endmodule
