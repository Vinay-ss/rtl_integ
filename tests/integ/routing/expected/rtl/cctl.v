// Non-ANSI AUTOARG-style wrapper
module cctl (/*AUTOARG*/
             // Outputs
             tick,
             // Inputs
             rst_n, clk
             );
   /*AUTOINPUT*/
   // Beginning of automatic inputs (from unused autoinst inputs)
   input logic          clk;                    // To u_timer of timer.v
   input logic          rst_n;                  // To u_timer of timer.v
   // End of automatics
   /*AUTOOUTPUT*/
   // Beginning of automatic outputs (from unused autoinst outputs)
   output logic         tick;                   // From u_timer of timer.v
   // End of automatics
   /*AUTOWIRE*/

   timer u_timer (/*AUTOINST*/
                  // Outputs
                  .tick                 (tick),
                  // Inputs
                  .clk                  (clk),
                  .rst_n                (rst_n));
endmodule
