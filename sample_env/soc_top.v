// Mini SoC Top — wires together UART, SPI, and GPIO
// Demonstrates: AUTOINST, AUTOWIRE, AUTOINPUT, AUTOOUTPUT, AUTOINOUT
module soc_top (/*AUTOARG*/
                // Outputs
                tx_out, tx_busy, spi_done, sclk, rx_valid, rx_data, mosi, miso_data, gpio_rd_data,
                cs_n,
                // Inouts
                gpio_pins,
                // Inputs
                tx_start, tx_data, spi_start, rx_in, rst_n, mosi_data, miso, gpio_wr_en,
                gpio_wr_data, clk
                );

   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input                clk;                    // To u_uart_tx of uart_tx.v, ...
   input [7:0]          gpio_wr_data;           // To u_gpio of gpio_port.v
   input                gpio_wr_en;             // To u_gpio of gpio_port.v
   input                miso;                   // To u_spi of spi_master.v
   input [7:0]          mosi_data;              // To u_spi of spi_master.v
   input                rst_n;                  // To u_uart_tx of uart_tx.v, ...
   input                rx_in;                  // To u_uart_rx of uart_rx.v
   input                spi_start;              // To u_spi of spi_master.v
   input [7:0]          tx_data;                // To u_uart_tx of uart_tx.v
   input                tx_start;               // To u_uart_tx of uart_tx.v
   // End of automatics

   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output               cs_n;                   // From u_spi of spi_master.v
   output [7:0]         gpio_rd_data;           // From u_gpio of gpio_port.v
   output [7:0]         miso_data;              // From u_spi of spi_master.v
   output               mosi;                   // From u_spi of spi_master.v
   output [7:0]         rx_data;                // From u_uart_rx of uart_rx.v
   output               rx_valid;               // From u_uart_rx of uart_rx.v
   output               sclk;                   // From u_spi of spi_master.v
   output               spi_done;               // From u_spi of spi_master.v
   output               tx_busy;                // From u_uart_tx of uart_tx.v
   output               tx_out;                 // From u_uart_tx of uart_tx.v
   // End of automatics

   /*AUTOINOUT*/
   // Beginning of automatic inouts (from unused autoinst inouts)
   inout [7:0]          gpio_pins;              // To/From u_gpio of gpio_port.v
   // End of automatics

   /*AUTOWIRE*/

   uart_tx u_uart_tx (/*AUTOINST*/
                      // Outputs
                      .tx_out           (tx_out),
                      .tx_busy          (tx_busy),
                      // Inputs
                      .clk              (clk),
                      .rst_n            (rst_n),
                      .tx_start         (tx_start),
                      .tx_data          (tx_data[7:0]));

   uart_rx u_uart_rx (/*AUTOINST*/
                      // Outputs
                      .rx_data          (rx_data[7:0]),
                      .rx_valid         (rx_valid),
                      // Inputs
                      .clk              (clk),
                      .rst_n            (rst_n),
                      .rx_in            (rx_in));

   spi_master u_spi (/*AUTOINST*/
                     // Outputs
                     .miso_data         (miso_data[7:0]),
                     .spi_done          (spi_done),
                     .sclk              (sclk),
                     .mosi              (mosi),
                     .cs_n              (cs_n),
                     // Inputs
                     .clk               (clk),
                     .rst_n             (rst_n),
                     .spi_start         (spi_start),
                     .mosi_data         (mosi_data[7:0]),
                     .miso              (miso));

   gpio_port u_gpio (/*AUTOINST*/
                     // Outputs
                     .gpio_rd_data      (gpio_rd_data[7:0]),
                     // Inouts
                     .gpio_pins         (gpio_pins[7:0]),
                     // Inputs
                     .clk               (clk),
                     .rst_n             (rst_n),
                     .gpio_wr_data      (gpio_wr_data[7:0]),
                     .gpio_wr_en        (gpio_wr_en));

endmodule

// Local Variables:
// verilog-library-directories:("." "rtl/")
// End:
