// SPI Master
module spi_master (
   input        clk,
   input        rst_n,
   input        spi_start,
   input  [7:0] mosi_data,
   output [7:0] miso_data,
   output       spi_done,
   // SPI bus
   output       sclk,
   output       mosi,
   input        miso,
   output       cs_n
);

   reg [7:0] miso_data;
   reg       spi_done;
   reg       sclk;
   reg       mosi;
   reg       cs_n;
   reg [2:0] bit_cnt;
   reg [7:0] tx_shift;
   reg [7:0] rx_shift;

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         miso_data <= 8'd0;
         spi_done  <= 1'b0;
         sclk      <= 1'b0;
         mosi      <= 1'b0;
         cs_n      <= 1'b1;
         bit_cnt   <= 3'd0;
         tx_shift  <= 8'd0;
         rx_shift  <= 8'd0;
      end else begin
         spi_done <= 1'b0;
         if (spi_start && cs_n) begin
            cs_n     <= 1'b0;
            tx_shift <= mosi_data;
            bit_cnt  <= 3'd0;
         end else if (!cs_n) begin
            sclk     <= ~sclk;
            if (sclk) begin
               rx_shift <= {rx_shift[6:0], miso};
               tx_shift <= {tx_shift[6:0], 1'b0};
               bit_cnt  <= bit_cnt + 1;
               if (bit_cnt == 3'd7) begin
                  cs_n      <= 1'b1;
                  miso_data <= {rx_shift[6:0], miso};
                  spi_done  <= 1'b1;
               end
            end
            mosi <= tx_shift[7];
         end
      end
   end

endmodule
