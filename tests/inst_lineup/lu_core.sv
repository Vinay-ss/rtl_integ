// Submodule for the instance lineup goldens (not a golden itself).
module lu_core
  #(parameter int W     = 8,
    parameter     DEPTH = 4)
   (input  logic                clk,
    input  logic                rst_n,
    input  logic [7:0]          c_in,
    input  logic signed [W-1:0] s_in,
    input  wire  [3:0]          mem_in [0:1],
    input  logic [1:0][7:0]     pk_in,
    output logic [15:0]         data_out,
    output logic                valid,
    output reg                  busy,
    output logic                a_really_long_status_port_name);
endmodule
