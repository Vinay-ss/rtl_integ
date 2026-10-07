" Syntax for the rtl_integ hierarchy panel (most colouring is done with extmarks).
if exists('b:current_syntax')
  finish
endif
syntax match rtlhierIcon /[▾▸]/
syntax match rtlhierMark /^\s*\*/
highlight default link rtlhierIcon Comment
highlight default link rtlhierMark Search
let b:current_syntax = 'rtlhier'
