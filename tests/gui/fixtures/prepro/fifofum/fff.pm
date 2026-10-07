##  fifofum - FIFO generator
##  Copyright (c) 2007 Adrian Lewis <indproj@yahoo.com>
##
##  This source code is free software; you can redistribute it
##  and/or modify it in source code form under the terms of the GNU
##  General Public License as published by the Free Software
##  Foundation; either version 2 of the License, or (at your option)
##  any later version.
##
##  This program is distributed in the hope that it will be useful,
##  but WITHOUT ANY WARRANTY; without even the implied warranty of
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
##  GNU General Public License for more details.
##
##  You should have received a copy of the GNU General Public License
##  along with this program; if not, write to the Free Software
##  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA

# subroutine returns number of bits required to store integer

sub bits {
  my $number = shift;
  my $bits   = 0;
  
  while ($number != 0) {
    $number = $number >> 1;
    $bits   = $bits + 1;
  }
  return $bits;
}

# parse fifo specification <name>:[ff|dp|sp|as][lp|wr|rd]?<depth_n>x<width_b>[f|e|s|c|r|w]*

sub fifo_spec {
  my $spec = shift;

  $_ = $spec;
  if (/(\w+):(ff|dp|sp|as)(lp|wr|rd|)(\d+)x(\d+)([f|e|s|c|r|w]*)/) {
    return ($1, $2, $3, int($4), int($5), $6);
  } else {
    print "bad-spec: $_\n";
  }
}

# format fifo size

sub fifo_size {
  my $width_b = shift;
  my $depth_n = shift;

  my $width_r = '['.($width_b-1).':0]';
  my $depth_v = '['.($depth_n-1).':0]';
  my $depth_b = bits($depth_n-1);
  my $depth_r = '['.($depth_b-1).':0]';
  my $count_b = bits($depth_n);
  my $count_r = '['.($count_b-1).':0]';
  my $point_b = 1+$depth_b;
  my $point_r = '['.($point_b-1).':0]';

  return ($width_r, $depth_v, $depth_b, $depth_r, $count_b, $count_r, $point_b, $point_r);
}

# subroutine reads arguments and returns (<fifo_spec>, <clock_gate_module>)

sub fifo_args {
  my $spec = '';
  my $gate = '';
  my $arg;

  foreach $arg (@ARGV) {
    if ($arg =~ /\+(.*)/) { $gate = $1; }
    else { $spec = $arg; }
  }

  return ($spec, $gate);
}

1;
