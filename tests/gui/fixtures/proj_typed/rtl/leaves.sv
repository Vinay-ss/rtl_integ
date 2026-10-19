// Leaves with typed ports: an enum (also as a parameter-sized array), int,
// int unsigned, a package vector typedef, an unpacked package struct, a
// signed vector and a scalar.
module t_src #(parameter int N = 3) (
  input  logic              clk,
  output tp::e_t            e,
  output tp::e_t [N-1:0]    ea,
  output int                i,
  output int unsigned       u,
  output tp::v_t            v,
  output tp::r_t            r,
  output logic signed [7:0] s,
  output logic              f,
  output int                cnt
);
  always_ff @(posedge clk) begin
    e   <= tp::S1;
    ea  <= '0;
    i   <= -1;
    u   <= 1;
    v   <= '0;
    r   <= '{a: 4'h1, b: 2};
    s   <= -8'sd2;
    f   <= 1'b1;
    cnt <= cnt + 1;
  end
endmodule

module t_dst #(parameter int N = 3) (
  input  tp::e_t            e,
  input  tp::e_t [N-1:0]    ea,
  input  int                i,
  input  int unsigned       u,
  input  tp::v_t            v,
  input  tp::r_t            r,
  input  logic signed [7:0] s,
  input  logic              f,
  output logic [7:0]        y
);
  assign y = (f && e == tp::S1 && ea[0] == tp::S0) ? v[0] ^ s ^ r.a : u[7:0] ^ i[7:0];
endmodule
