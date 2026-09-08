// Hand-written ANSI leaf: the source of most routes
module dma #(parameter DW = 32)
  (input  logic          clk,
   input  logic          rst_n,
   input  logic [3:0]    cfg,
   output logic          irq,
   output logic          busy,
   output req_pkg::req_t req,
   output logic [DW-1:0] data,
   axi_if.master         m_axi);

   typedef struct packed { logic q; } loc_t;
   loc_t lv;

   always_ff @(posedge clk or negedge rst_n)
     if (!rst_n) begin
        irq  <= 1'b0;
        busy <= 1'b0;
        req  <= '0;
        data <= '0;
     end else begin
        busy <= m_axi.ready;
        irq  <= |cfg;
        req  <= '{id: cfg, len: 8'd1};
        data <= {DW{m_axi.valid}};
     end
   assign m_axi.valid = busy;
   assign m_axi.data  = data;
   assign lv.q = irq;
endmodule
