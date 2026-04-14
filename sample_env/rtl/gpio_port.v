// GPIO Port
module gpio_port (
   input         clk,
   input         rst_n,
   input  [7:0]  gpio_wr_data,
   input         gpio_wr_en,
   output [7:0]  gpio_rd_data,
   inout  [7:0]  gpio_pins
);

   reg [7:0] gpio_rd_data;
   reg [7:0] gpio_out;
   reg [7:0] gpio_oe;

   assign gpio_pins = gpio_oe ? gpio_out : 8'bz;

   always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         gpio_out    <= 8'd0;
         gpio_oe     <= 8'd0;
         gpio_rd_data <= 8'd0;
      end else begin
         gpio_rd_data <= gpio_pins;
         if (gpio_wr_en)
           gpio_out <= gpio_wr_data;
      end
   end

endmodule
