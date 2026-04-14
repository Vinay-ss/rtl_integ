// AUTOASCIIENUM demo — generates ASCII decode for FSM states
module auto_ascii_demo (
   input      clk,
   input      rst_n
);

   localparam STATE_IDLE  = 3'd0;
   localparam STATE_FETCH = 3'd1;
   localparam STATE_EXEC  = 3'd2;
   localparam STATE_WRITE = 3'd3;
   localparam STATE_HALT  = 3'd4;

   reg [2:0] state;

   /*AUTOASCIIENUM("state", "state_ascii", "STATE_")*/

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n)
        state <= STATE_IDLE;
      else case (state)
        STATE_IDLE:  state <= STATE_FETCH;
        STATE_FETCH: state <= STATE_EXEC;
        STATE_EXEC:  state <= STATE_WRITE;
        STATE_WRITE: state <= STATE_HALT;
        STATE_HALT:  state <= STATE_IDLE;
        default:     state <= STATE_IDLE;
      endcase
   end

endmodule
