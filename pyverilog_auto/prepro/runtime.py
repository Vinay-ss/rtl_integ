# Python 3 port of prepro, the Perl/Python preprocessor for text files.
#
# prepro: Copyright (c) 2006 Adrian Lewis <indproj@yahoo.com>
# Port and changes: Copyright (c) 2026 Vinay S -- rewritten for Python 3,
# Windows and Linux; line maps, template constructs, variable capture and
# the backtick template syntax added.
#
# prepro is free software under the GNU General Public License version 2 or
# (at your option) any later version.  This port is distributed under the
# GNU General Public License version 3 or later; see the LICENSE file.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.

"""Script prologues: the ``prepro_pad`` helper plus an output hook.

The hook replaces standard output inside the generated script.  For every
output line it records the script line of the ``print`` that started it
(Python: the calling frame, Perl: ``caller``), and optionally snapshots the
values of named template variables when given script lines print.  The
records go to the file named by ``PREPRO_MAP_OUT`` when the script exits:

* ``L<TAB>script_line``            one per output line, in order
* ``C<TAB>script_line<TAB>name<TAB>value``  a captured variable (value in the
  template's language: ``repr`` for Python, ``Data::Dumper`` for Perl)
"""

from __future__ import annotations

MAP_ENV = "PREPRO_MAP_OUT"
MISSING = "!missing"

_PY_HOOK = r'''import sys as _prepro_sys, atexit as _prepro_atexit, os as _prepro_os
_PREPRO_FILE = __file__
_PREPRO_CAPS = @@CAPS@@
class _PreproOut(object):
    encoding = "utf-8"
    errors = "strict"
    def __init__(self):
        self._raw = _prepro_sys.__stdout__.buffer
        self._recs = []
        self._bol = True
    def write(self, s):
        if not s:
            return 0
        f = _prepro_sys._getframe(1)
        while f is not None and f.f_code.co_filename != _PREPRO_FILE:
            f = f.f_back
        ln = f.f_lineno if f is not None else 0
        parts = s.split("\n")
        last = len(parts) - 1
        for i, part in enumerate(parts):
            if i:
                self._bol = True
            if i == last and part == "":
                break
            if self._bol:
                self._bol = False
                self._recs.append("L\t%d" % ln)
                for name in _PREPRO_CAPS.get(ln, ()):
                    v = f.f_locals.get(name, f.f_globals.get(name, _PreproOut)) if f is not None else _PreproOut
                    self._recs.append("C\t%d\t%s\t%s" % (ln, name, "@@MISSING@@" if v is _PreproOut else repr(v)))
        self._raw.write(s.encode("utf-8"))
        return len(s)
    def flush(self):
        self._raw.flush()
    def isatty(self):
        return False
    def _dump(self):
        self.flush()
        path = _prepro_os.environ.get("@@ENV@@")
        if path:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(self._recs) + "\n")
_prepro_out = _PreproOut()
_prepro_sys.stdout = _prepro_out
_prepro_atexit.register(_prepro_out._dump)
def prepro_pad (text, target):
  if target < 0:
    while len(text) < -target:
      text = " "+text
  else:
    while len(text) < target:
      text = text+" "
  return text
'''

_PERL_HOOK = r'''BEGIN {
  package PreproOut;
  sub TIEHANDLE { my ($c, %a) = @_; return bless { %a, bol => 1, recs => [] }, $c; }
  sub _origin {
    my ($self) = @_;
    for (my $i = 0; my @c = caller($i); $i++) {
      return $c[2] if $c[0] ne 'PreproOut' && $c[1] eq $self->{script};
    }
    return 0;
  }
  sub _cap {
    my ($n) = @_;
    no strict 'refs';
    require Data::Dumper;
    my $d = sub { Data::Dumper->new([$_[0]])->Terse(1)->Indent(0)->Useqq(1)->Sortkeys(1)->Dump };
    (my $s = $n) =~ s/^[\$\@\%]//;
    return $d->([@{"main::$s"}]) if $n =~ /^\@/;
    return $d->({%{"main::$s"}}) if $n =~ /^\%/;
    return $d->(${"main::$s"}) if defined ${"main::$s"};
    return $d->([@{"main::$s"}]) if @{"main::$s"};
    return $d->({%{"main::$s"}}) if %{"main::$s"};
    return '@@MISSING@@';
  }
  sub _w {
    my ($self, $t) = @_;
    return unless defined $t && length $t;
    my $ln = $self->_origin;
    my @parts = split /\n/, $t, -1;
    for my $i (0 .. $#parts) {
      $self->{bol} = 1 if $i;
      last if $i == $#parts && $parts[$i] eq '';
      if ($self->{bol}) {
        $self->{bol} = 0;
        push @{$self->{recs}}, "L\t$ln";
        if (my $names = $self->{caps}{$ln}) {
          push @{$self->{recs}}, "C\t$ln\t$_\t" . _cap($_) for @$names;
        }
      }
    }
    my $fh = $self->{fh};
    print {$fh} $t;
  }
  sub PRINT { my $self = shift; my $t = join(defined $, ? $, : '', @_); $t .= $\ if defined $\; $self->_w($t); return 1; }
  sub PRINTF { my $self = shift; my $fmt = shift; $self->_w(sprintf($fmt, @_)); return 1; }
  sub WRITE { my ($self, $buf, $len, $off) = @_; $off ||= 0; $len = length($buf) - $off unless defined $len; $self->_w(substr($buf, $off, $len)); return $len; }
  sub BINMODE { return 1; }
  sub FILENO { return fileno($_[0]{fh}); }
  sub CLOSE { return 1; }
  package main;
  open(my $prepro_real, '>&', \*STDOUT) or die "prepro: cannot duplicate STDOUT: $!";
  binmode($prepro_real);
  tie *STDOUT, 'PreproOut', fh => $prepro_real, script => __FILE__, caps => @@CAPS@@;
}
END {
  my $o = tied(*STDOUT);
  if ($o && $ENV{@@ENV@@}) {
    if (open(my $m, '>', $ENV{@@ENV@@})) { binmode($m); print {$m} join("\n", @{$o->{recs}}), "\n"; close($m); }
  }
}
sub prepro_pad {
  my $string = shift;
  my $target = shift;

  if ($target < 0) {
    while (length($string) < -$target) {
      $string = " $string";
    }
  } else {
    while (length($string) < $target) {
      $string = "$string ";
    }
  }
  return $string;
}
'''


def _py_caps(caps: dict[int, list[str]]) -> str:
    return "{" + ", ".join(f"{ln}: {tuple(names)!r}" for ln, names in sorted(caps.items())) + "}"


def _perl_caps(caps: dict[int, list[str]]) -> str:
    items = []
    for ln, names in sorted(caps.items()):
        quoted = ", ".join("'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'" for n in names)
        items.append(f"{ln} => [{quoted}]")
    return "{" + ", ".join(items) + "}"


def _template(language: str) -> str:
    return _PY_HOOK if language == "python" else _PERL_HOOK


def prologue_length(language: str) -> int:
    return _template(language).count("\n")


def prologue(language: str, caps: dict[int, list[str]]) -> list[str]:
    """Prologue lines for *language*; ``caps`` maps absolute script lines to
    the variable names to snapshot when that line prints."""
    template = _template(language)
    render = _py_caps if language == "python" else _perl_caps
    text = (template.replace("@@CAPS@@", render(caps))
            .replace("@@ENV@@", MAP_ENV)
            .replace("@@MISSING@@", MISSING))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    assert len(lines) == prologue_length(language)
    return lines
