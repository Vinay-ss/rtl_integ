// Hand-written ANSI wrapper without any AUTO markers
module mem
  (input  logic       clk,
   input  logic       rst_n,
   output logic [7:0] rd,
   input  logic [7:0] wr,
   axi_if.slave m_axi_0,   // routed: axi
   axi_if.slave m_axi_1,   // routed: axi
   input logic tick,   // routed: tick
   input req_pkg::req_t req,   // routed: req
   output logic [3:0] cfg   // routed: cfg
  );

   logic err;   // routed: err
   ctrl u_ctrl0 (.clk (clk), .rst_n (rst_n), .rd (rd), .wr (wr),
                 .s_axi                 (m_axi_0),   // routed: axi
                 .tick_in               (tick),   // routed: tick
                 .err                   (err),   // routed: err
                 .req_in                (req),   // routed: req
                 .cfg_o                 (cfg)   // routed: cfg
                );
   ctrl u_ctrl1 (.clk (clk), .rst_n (rst_n), .rd (), .wr (wr),
                 .s_axi                 (m_axi_1),   // routed: axi
                 .err_in                (err)   // routed: err
                );
   phy  u_phy (.a (clk));
endmodule
