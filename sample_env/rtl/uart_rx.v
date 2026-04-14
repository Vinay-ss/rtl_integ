// UART Receiver
module uart_rx (
   input        clk,
   input        rst_n,
   input        rx_in,
   output [7:0] rx_data,
   output       rx_valid
);

   reg [7:0] rx_data;
   reg       rx_valid;
   reg [3:0] bit_cnt;
   reg [7:0] shift_reg;
   reg [1:0] state;

   localparam IDLE  = 2'd0;
   localparam START = 2'd1;
   localparam DATA  = 2'd2;
   localparam STOP  = 2'd3;

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         rx_data   <= 8'd0;
         rx_valid  <= 1'b0;
         bit_cnt   <= 4'd0;
         shift_reg <= 8'd0;
         state     <= IDLE;
      end else begin
         rx_valid <= 1'b0;
         case (state)
           IDLE:  if (!rx_in) state <= START;
           START: state <= DATA;
           DATA: begin
              shift_reg <= {rx_in, shift_reg[7:1]};
              bit_cnt   <= bit_cnt + 1;
              if (bit_cnt == 4'd7) state <= STOP;
           end
           STOP: begin
              rx_data  <= shift_reg;
              rx_valid <= 1'b1;
              state    <= IDLE;
           end
         endcase
      end
   end

endmodule
