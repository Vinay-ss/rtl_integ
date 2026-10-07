/*  fifofum - FIFO generator
 *  Copyright (c) 2007 Adrian Lewis <indproj@yahoo.com>
 *
 *  This source code is free software; you can redistribute it
 *  and/or modify it in source code form under the terms of the GNU
 *  General Public License as published by the Free Software
 *  Foundation; either version 2 of the License, or (at your option)
 *  any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA
 */

// FIFO specification: ff8x16fesc

module myfifo
(
  // WRITE PORT

  input       [15:0] wr_data,
  input              wr_valid,
  output             wr_ready,
  output             wr_full,
  output       [3:0] wr_space,

  // READ PORT

  output      [15:0] rd_data,
  output             rd_valid,
  input              rd_ready,
  output             rd_empty,
  output       [3:0] rd_count,

  // CLOCK AND RESET

  input              clock,
  input              reset_n
);

// INTERNAL SIGNALS

  // WRITE POINTER

  reg          [2:0] n_wr_pointer;
  reg          [2:0] s_wr_pointer;
 
  reg                n_wr_toggle;
  reg                s_wr_toggle;

  // READ POINTER
 
  reg          [2:0] n_rd_pointer;
  reg          [2:0] s_rd_pointer;

  reg                n_rd_toggle;
  reg                s_rd_toggle;

  // INTERFACE

  reg                a_wr_ready;
  reg                a_wr_write;

  wire               n_wr_full;
  reg                s_wr_full;

  reg          [3:0] n_wr_space;
  reg          [3:0] s_wr_space;

  reg                a_rd_valid;
  reg                a_rd_read;

  wire               n_rd_empty;
  reg                s_rd_empty;

  reg          [3:0] n_rd_count;
  reg          [3:0] s_rd_count;

// IMPLEMENTATION

  // FULL/EMPTY

  assign n_wr_full  = n_rd_pointer == n_wr_pointer && n_wr_toggle != n_rd_toggle;
  assign n_rd_empty = n_rd_pointer == n_wr_pointer && n_wr_toggle == n_rd_toggle;

  always@*
    case({a_rd_read,a_wr_write})
    2'b01:   n_rd_count = s_rd_count + 1;
    2'b10:   n_rd_count = s_rd_count - 1;
    default: n_rd_count = s_rd_count;
    endcase

  always@*
    case({a_wr_write,a_rd_read})
    2'b01:   n_wr_space = s_wr_space + 1;
    2'b10:   n_wr_space = s_wr_space - 1;
    default: n_wr_space = s_wr_space;
    endcase

  // WRITE POINTER

  always@*
    begin
      // READY WHEN FIFO NOT FULL

      a_wr_ready = !s_wr_full;

      // WRITE WHEN READY AND WRITE DATA IS VALID

      a_wr_write = a_wr_ready && wr_valid;

      // INCREMENT POINTER WHEN WRITING

      if (a_wr_write)
        begin
          // IF POINTER MUST WRAP

          if (s_wr_pointer == 7)
            begin
              // WRAP POINTER

              n_wr_pointer = 0;
              n_wr_toggle  = !s_wr_toggle;
            end
          else
            begin
              // INCREMENT POINTER

              n_wr_pointer = s_wr_pointer + 1;
              n_wr_toggle  = s_wr_toggle;
            end
        end

      // ELSE HOLD CURRENT VALUE

      else
        begin
          n_wr_pointer = s_wr_pointer;
          n_wr_toggle  = s_wr_toggle;
        end
    end

  // READ POINTER

  always@*
    begin
      // DATA VALID WHEN FIFO NOT EMPTY

      a_rd_valid = !s_rd_empty;

      // READ WHEN READY AND READ DATA IS READY

      a_rd_read = a_rd_valid && rd_ready;

      // INCREMENT POINTER WHEN READING

      if (a_rd_read)
        begin
          // IF POINTER MUST WRAP

          if (s_rd_pointer == 7)
            begin
              // WRAP POINTER

              n_rd_pointer = 0;
              n_rd_toggle  = !s_rd_toggle;
            end
          else
            begin
              // INCREMENT POINTER

              n_rd_pointer = s_rd_pointer + 1;
              n_rd_toggle  = s_rd_toggle;
            end
        end

      // ELSE HOLD CURRENT VALUE

      else
        begin
          n_rd_pointer = s_rd_pointer;
          n_rd_toggle  = s_rd_toggle;
        end

    end

  // REGISTERS

  always@(posedge clock or negedge reset_n)
    if (!reset_n)
      begin
        s_wr_pointer <= 3'd0;
        s_wr_toggle  <= 1'd0;
        s_wr_full    <= 1'd0;
        s_wr_space   <= 4'd8;

        s_rd_pointer <= 3'd0;
        s_rd_toggle  <= 1'd0;
        s_rd_empty   <= 1'd1;
        s_rd_count   <= 4'd0;
      end
    else
      begin
        s_wr_pointer <= n_wr_pointer;
        s_wr_toggle  <= n_wr_toggle;
        s_wr_full    <= n_wr_full;
        s_wr_space   <= n_wr_space;

        s_rd_pointer <= n_rd_pointer;
        s_rd_toggle  <= n_rd_toggle;
        s_rd_empty   <= n_rd_empty;
        s_rd_count   <= n_rd_count;
      end

  // FIFO ENTRIES

  myfifo_storage u_myfifo_storage
  (
    .clock      (clock),

    .wr_address (s_wr_pointer),
    .wr_data    (wr_data),
    .wr_write   (a_wr_write),

    .rd_address (s_rd_pointer),
    .rd_data    (rd_data)
  );

  // ASSIGN OUTPUTS

  assign wr_ready   = a_wr_ready;
  assign wr_full    = s_wr_full;
  assign wr_space   = s_wr_space;

  assign rd_valid   = a_rd_valid;
  assign rd_empty   = s_rd_empty;
  assign rd_count   = s_rd_count;

endmodule

module myfifo_storage
(
  input              clock,
  
  input        [2:0] wr_address,
  input       [15:0] wr_data,
  input              wr_write,

  input        [2:0] rd_address,
  output      [15:0] rd_data
);

// INTERNAL SIGNALS

  // FIFO ENTRIES

  reg         [15:0] m_entry [7:0];

// IMPLEMENTATION

  always@(posedge clock)
      if (wr_write) m_entry[wr_address] <= wr_data;

  assign rd_data = m_entry[rd_address];

endmodule
