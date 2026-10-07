// AUTO_TEMPLATE pins (// Templated) with only the dir and type fields.
module autoinst_template;

   /* lu_core AUTO_TEMPLATE (
      .c_in      (cfg_byte[7:0]),
      .data_out  (core_data[]),
      .busy      (),
      .a_really_long_status_port_name (status),
   ); */

   lu_core u_core (/*AUTOINST*/
                   // Outputs
                   .data_out                       (core_data[15:0]),       // output logic         // Templated
                   .valid                          (valid),                 // output logic
                   .busy                           (),                      // output reg           // Templated
                   .a_really_long_status_port_name (status),                // output logic         // Templated
                   // Inputs
                   .clk                            (clk),                   // input  logic
                   .rst_n                          (rst_n),                 // input  logic
                   .c_in                           (cfg_byte[7:0]),         // input  logic         // Templated
                   .s_in                           (s_in[W-1:0]),           // input  logic signed
                   .mem_in                         (mem_in/*[3:0].[0:1]*/), // input  wire
                   .pk_in                          (pk_in/*[1:0][7:0]*/));  // input  logic

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir type"
// End:
