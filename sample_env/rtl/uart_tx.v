// UART Transmitter
module uart_tx (
   input        clk,
   input        rst_n,
   input        tx_start,
   input  [7:0] tx_data,
   output       tx_out,
   output       tx_busy
);

   reg       tx_out;
   reg       tx_busy;
   reg [3:0] bit_cnt;
   reg [9:0] shift_reg;

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         tx_out   <= 1'b1;
         tx_busy  <= 1'b0;
         bit_cnt  <= 4'd0;
         shift_reg <= 10'd0;
      end else if (tx_start && !tx_busy) begin
         shift_reg <= {1'b1, tx_data, 1'b0};
         tx_busy   <= 1'b1;
         bit_cnt   <= 4'd0;
      end else if (tx_busy) begin
         tx_out    <= shift_reg[0];
         shift_reg <= {1'b1, shift_reg[9:1]};
         bit_cnt   <= bit_cnt + 1;
         if (bit_cnt == 4'd9)
           tx_busy <= 1'b0;
      end
   end

endmodule
