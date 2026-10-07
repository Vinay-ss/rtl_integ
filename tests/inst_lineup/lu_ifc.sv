// Submodule with interface ports for the instance lineup goldens (not a golden itself).
module lu_ifc
   (input  logic               clk,
    lu_bus_if.mst              m_bus,
    lu_bus_if                  s_bus,
    output logic signed [11:0] acc,
    input  logic [3:0]         lanes [0:3],
    inout  wire                pad);
endmodule
