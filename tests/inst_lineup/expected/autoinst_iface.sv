// Interface / modport ports, packed + unpacked and signed ports.
module autoinst_iface
  (input logic clk);

   lu_bus_if bus ();

   lu_ifc u_ifc (.m_bus                 (bus.mst),               // interface            lu_bus_if.mst
                 .s_bus                 (bus),                   // interface            lu_bus_if
                 /*AUTOINST*/
                 // Outputs
                 .acc                   (acc[11:0]),             // output    [11:0]     logic signed
                 // Inouts
                 .pad                   (pad),                   // inout                wire
                 // Inputs
                 .clk                   (clk),                   // input                logic
                 .lanes                 (lanes/*[3:0].[0:3]*/)); // input     [3:0][0:3] logic

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
