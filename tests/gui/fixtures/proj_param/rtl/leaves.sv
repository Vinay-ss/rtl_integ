module leaf_mk #(
  parameter type P  = logic,
  parameter type A  = logic,
  parameter int  AW = 1
) (
  input  logic                    clk,
  input  A                        a,
  input  logic signed [$bits(A)-1:0] s,
  output P                        dout,
  output logic signed [$bits(A):0]   sum
);
  logic [AW-1:0] cnt;
  always_ff @(posedge clk) begin
    cnt  <= cnt + 1'b1;
    dout <= P'({a, a});
    sum  <= s + s;
  end
endmodule

module leaf_use #(
  parameter type P = logic,
  parameter int  W = 8
) (
  input  logic         clk,
  input  P             din,
  output logic [W-1:0] y
);
  always_ff @(posedge clk) y <= din[W-1:0];
endmodule

module leaf_lvl #(
  parameter int W = 8,
  parameter int L = 0
) (
  output logic [W-1:0] y
);
  assign y = W'(L);
endmodule
