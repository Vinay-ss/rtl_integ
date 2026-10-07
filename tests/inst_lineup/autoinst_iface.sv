// Interface / modport ports, packed + unpacked and signed ports.
module autoinst_iface
  (input logic clk);

   lu_bus_if bus ();

   lu_ifc u_ifc (.m_bus (bus.mst),
                 .s_bus (bus),
                 /*AUTOINST*/);

endmodule

// Local Variables:
// verilog-auto-inst-lineup: t
// verilog-auto-inst-port-comment: "dir width type"
// End:
