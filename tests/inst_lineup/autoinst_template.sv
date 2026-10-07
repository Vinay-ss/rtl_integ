// AUTO_TEMPLATE pins (// Templated) with only the dir and type fields.
module autoinst_template;

   /* lu_core AUTO_TEMPLATE (
      .c_in      (cfg_byte[7:0]),
      .data_out  (core_data[]),
      .busy      (),
      .a_really_long_status_port_name (status),
   ); */

   lu_core u_core (/*AUTOINST*/);

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir type"
// End:
