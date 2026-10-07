" Syntax for the rtl_integ hierarchy panel (most colouring is done with extmarks).
if exists('b:current_syntax')
  finish
endif
syntax match rtlhierIcon /[▾▸]/
highlight default link rtlhierIcon RtlIntegIcon
let b:current_syntax = 'rtlhier'
