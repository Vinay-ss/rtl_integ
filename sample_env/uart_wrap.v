// UART Wrapper — combines TX and RX into one block
// Demonstrates: AUTOINST, AUTOWIRE, AUTOINPUT, AUTOOUTPUT
module uart_wrap (/*AUTOARG*/
                  // Outputs
                  tx_out, tx_busy, rx_valid, rx_data,
                  // Inputs
                  tx_start, tx_data, rx_in, rst_n, clk
                  );

   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input                clk;                    // To u_tx of uart_tx.v, ...
   input                rst_n;                  // To u_tx of uart_tx.v, ...
   input                rx_in;                  // To u_rx of uart_rx.v
   input [7:0]          tx_data;                // To u_tx of uart_tx.v
   input                tx_start;               // To u_tx of uart_tx.v
   // End of automatics

   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output [7:0]         rx_data;                // From u_rx of uart_rx.v
   output               rx_valid;               // From u_rx of uart_rx.v
   output               tx_busy;                // From u_tx of uart_tx.v
   output               tx_out;                 // From u_tx of uart_tx.v
   // End of automatics

   /*AUTOWIRE*/

   uart_tx u_tx (/*AUTOINST*/
                 // Outputs
                 .tx_out                (tx_out),
                 .tx_busy               (tx_busy),
                 // Inputs
                 .clk                   (clk),
                 .rst_n                 (rst_n),
                 .tx_start              (tx_start),
                 .tx_data               (tx_data[7:0]));

   uart_rx u_rx (/*AUTOINST*/
                 // Outputs
                 .rx_data               (rx_data[7:0]),
                 .rx_valid              (rx_valid),
                 // Inputs
                 .clk                   (clk),
                 .rst_n                 (rst_n),
                 .rx_in                 (rx_in));

endmodule

// Local Variables:
// verilog-library-directories:("." "rtl/")
// End:
