" Vim syntax file
" Language:  Verilog / SystemVerilog (IEEE 1800-2017) and rtl_integ templates
" Used by:   rtl-integ-gui (pyverilog_auto/gui/nvim), as 'syntax' rtlsv
"
" Template code is highlighted as Python or Perl.  b:rtl_integ_tpl =
" {'syntax': 'prepro' | 'backtick', 'lang': 'python' | 'perl'} is set by the
" GUI for the templates of a project; without it the first lines are looked at.
"   prepro:    // py CODE    /* py-begin .. py-end */    #$var  #$var#
"   backtick:  ` CODE        [* .. *]                    `var`
"   both:      #[expr]#  #<expr<#  #>expr>#
" (Perl templates: // pl, /* pl-begin .. pl-end */.)

if exists('b:current_syntax')
  finish
endif
let s:cpo_save = &cpo
set cpo&vim

syntax case match
syntax iskeyword @,48-57,_,192-255

" -- operators and punctuation (defined first: everything else wins) ---------
syn match rtlsvOperator "[-+*/%<>=!&|^~?:]"
syn match rtlsvSvaOp    "|->\||=>"
syn match rtlsvDelay    "##\=\%(\d[0-9_]*\|\[\)\="
syn match rtlsvEvent    "@\*\="

" -- keywords --------------------------------------------------------------------
syn keyword rtlsvDesign module macromodule interface program package class checker primitive config
      \ nextgroup=rtlsvDesignName skipwhite
syn match   rtlsvDesignName "\h\w*" contained

syn keyword rtlsvEnd endmodule endinterface endprogram endpackage endclass endchecker endprimitive
      \ endconfig endfunction endtask endgenerate endclocking endspecify endtable endproperty
      \ endsequence endgroup
      \ nextgroup=rtlsvBlockLabel skipwhite
syn keyword rtlsvLabel begin end fork join join_any join_none forkjoin nextgroup=rtlsvBlockLabel skipwhite
syn match   rtlsvBlockLabel ":\s*\zs\h\w*" contained

syn keyword rtlsvStatement function task generate clocking specify table always always_comb always_ff
      \ always_latch initial final assign deassign force release alias defparam return break continue
      \ disable wait wait_order new this super null default bind import export extends implements
      \ modport let design instance cell liblist library use incdir include global ifnone
      \ pulsestyle_ondetect pulsestyle_onevent showcancelled noshowcancelled
syn keyword rtlsvConditional if else case casex casez endcase unique unique0 priority inside matches
      \ iff randcase
syn keyword rtlsvRepeat for foreach forever repeat while do
syn keyword rtlsvType logic bit byte shortint int longint integer time real realtime shortreal string
      \ chandle event reg wire tri tri0 tri1 triand trior trireg wand wor uwire supply0 supply1
      \ interconnect void enum struct union packed tagged type nettype signed unsigned genvar var untyped
syn keyword rtlsvTypedef typedef
syn keyword rtlsvStorage input output inout ref parameter localparam specparam const static automatic
      \ local protected rand randc virtual extern pure context scalared vectored strong0 strong1 pull0
      \ pull1 weak0 weak1 highz0 highz1 small medium large strong weak soft
syn keyword rtlsvAssert assert assume cover restrict expect property sequence covergroup coverpoint
      \ cross bins binsof illegal_bins ignore_bins wildcard first_match throughout within intersect
      \ until until_with s_until s_until_with nexttime s_nexttime eventually s_eventually s_always
      \ accept_on reject_on sync_accept_on sync_reject_on implies constraint solve before dist
      \ randsequence with
syn keyword rtlsvGate and or nand nor xor xnor not buf bufif0 bufif1 notif0 notif1 cmos rcmos nmos
      \ pmos rnmos rpmos tran tranif0 tranif1 rtran rtranif0 rtranif1 pullup pulldown
syn keyword rtlsvEdge posedge negedge edge
syn keyword rtlsvPreKw timeunit timeprecision

" -- names ---------------------------------------------------------------------
" module and instance names of an instantiation: "mod #(" / "mod u_x (" and
" ") u_x (" after a parameter list (a name may hold template tokens)
syn match rtlsvModuleRef "^\s*\zs\h\w*\ze\%(\s*#\s*(\|\s\+\h[0-9A-Za-z_`$#]*\s*\%(\[[^]]*\]\s*\)\=(\)"
      \ nextgroup=rtlsvInstName skipwhite
syn match rtlsvInstName  "\h[0-9A-Za-z_`$#]*\ze\s*\%(\[[^]]*\]\s*\)\=(" contained contains=@rtlTplSubst
syn match rtlsvInstName  ")\s*\zs\h[0-9A-Za-z_`$#]*\ze\s*\%(\[[^]]*\]\s*\)\=\%((\|$\)" contains=@rtlTplSubst
" .port( connections and .name, .W( parameter overrides
syn match rtlsvPort      "\%(^\|[(,]\)\s*\zs\.\h\w*"
syn match rtlsvScope     "\h\w*\ze::"
syn match rtlsvSystask   "\$\h[0-9A-Za-z_$]*"
syn match rtlsvDirective "`\h\w*"
syn match rtlsvEscIdent  "\\\S\+\ze\s"

" -- numbers -------------------------------------------------------------------
syn match rtlsvNumber "\<\d[0-9_]*\%(\.\d[0-9_]*\)\=\%([eE][-+]\=\d[0-9_]*\)\=\>"
syn match rtlsvNumber "\<\d[0-9_]*\%(\.\d[0-9_]*\)\=\%(fs\|ps\|ns\|us\|ms\|s\|step\)\>"
syn match rtlsvNumber "\%(\<\d[0-9_]*\s*\)\='[sS]\=\%([bB]\s*[01xXzZ?_]\+\|[oO]\s*[0-7xXzZ?_]\+\|[dD]\s*[0-9xXzZ?_]\+\|[hH]\s*[0-9a-fA-FxXzZ?_]\+\)"
syn match rtlsvNumber "'[01xXzZ]\>"

" -- strings, attributes, comments -----------------------------------------------
syn match  rtlsvEscape "\\." contained
syn match  rtlsvFormat "%[-0-9.]*[a-zA-Z]" contained
syn region rtlsvString start=+"+ skip=+\\\\\|\\"+ end=+"+ oneline
      \ contains=rtlsvEscape,rtlsvFormat,@rtlTplSubst
syn region rtlsvAttribute start="(\*\ze\s*\h" end="\*)" oneline contains=rtlsvString,rtlsvNumber,@rtlTplSubst
syn keyword rtlsvTodo TODO FIXME XXX NOTE BUG HACK contained
syn match  rtlsvComment "//.*$" contains=rtlsvTodo,@rtlTplSubst,@Spell
syn region rtlsvComment start="/\*" end="\*/" contains=rtlsvTodo,@rtlTplSubst,@Spell

" -- template code (defined last: wins over SV items starting at the same column)
syn cluster rtlTplSubst contains=NONE

function! s:Detect() abort
  let l:lang = expand('%:e') =~? '^\%(svpl\|vpl\|plv\)$' ? 'perl' : 'python'
  for l:line in getline(1, 200)
    if l:line =~# '^\s*\%(//\s\=py\%(\s\|$\)\|/\*\s\=py-begin\)'
      return {'syntax': 'prepro', 'lang': 'python'}
    elseif l:line =~# '^\s*\%(//\s\=pl\%(\s\|$\)\|/\*\s\=pl-begin\)'
      return {'syntax': 'prepro', 'lang': 'perl'}
    elseif l:line =~# '^\s*` \|^`$\|^\s*\[\*\%(\s\|$\)'
      return {'syntax': 'backtick', 'lang': l:lang}
    endif
  endfor
  return {}
endfunction

let s:tpl = get(b:, 'rtl_integ_tpl', 0)
if type(s:tpl) != v:t_dict
  let s:tpl = s:Detect()
endif

if !empty(s:tpl)
  let s:lang = get(s:tpl, 'lang', 'python') ==# 'perl' ? 'perl' : 'python'
  let s:mark = s:lang ==# 'perl' ? 'pl' : 'py'
  unlet! b:current_syntax
  if s:lang ==# 'perl'
    syn include @rtlTplCode syntax/perl.vim
  else
    syn include @rtlTplCode syntax/python.vim
  endif
  unlet! b:current_syntax

  syn region rtlTplExpr matchgroup=rtlTplDelim start="#\[" end="\]#" oneline keepend contains=@rtlTplCode
  syn region rtlTplExpr matchgroup=rtlTplDelim start="#<" end="<#" oneline keepend contains=@rtlTplCode
  syn region rtlTplExpr matchgroup=rtlTplDelim start="#>" end=">#" oneline keepend contains=@rtlTplCode
  syn cluster rtlTplSubst add=rtlTplExpr

  if get(s:tpl, 'syntax', 'prepro') ==# 'backtick'
    if s:lang ==# 'python'
      syn match rtlTplVar "`\h\w*\%(\.\h\w*\)*`"
    else
      syn match rtlTplVar "`\h\w*`"
    endif
    syn region rtlTplLine matchgroup=rtlTplDelim start="^\s*\zs`\ze " end="$" keepend contains=@rtlTplCode
    syn match  rtlTplDelim "^`$"
    syn region rtlTplBlock matchgroup=rtlTplDelim start="^\s*\zs\[\*\ze\%(\s\|$\)"
          \ end="\*\]\ze\s*$\|^\s*\zs\*\]" keepend contains=@rtlTplCode
  else
    syn match rtlTplVar "#\$\h\w*#\="
    execute 'syn region rtlTplLine matchgroup=rtlTplDelim start="^\s*\zs// ' . s:mark
          \ . '\ze " end="$" keepend contains=@rtlTplCode'
    execute 'syn match rtlTplDelim "^// ' . s:mark . '$"'
    execute 'syn region rtlTplBlock matchgroup=rtlTplDelim start="^\s*\zs/\* ' . s:mark
          \ . '-begin\ze\%(\s\|$\)" end="' . s:mark . '-end \*/\ze\s*$\|^\s*\zs' . s:mark
          \ . '-end \*/" keepend contains=@rtlTplCode'
  endif
  syn cluster rtlTplSubst add=rtlTplVar
endif

syn sync clear
syn sync minlines=300

hi def link rtlsvOperator     Operator
hi def link rtlsvSvaOp        Special
hi def link rtlsvDelay        Special
hi def link rtlsvEvent        Special
hi def link rtlsvDesign       Statement
hi def link rtlsvDesignName   Function
hi def link rtlsvEnd          Statement
hi def link rtlsvLabel        Keyword
hi def link rtlsvBlockLabel   Label
hi def link rtlsvStatement    Statement
hi def link rtlsvConditional  Conditional
hi def link rtlsvRepeat       Repeat
hi def link rtlsvType         Type
hi def link rtlsvTypedef      Typedef
hi def link rtlsvStorage      StorageClass
hi def link rtlsvAssert       Exception
hi def link rtlsvGate         Keyword
hi def link rtlsvEdge         Special
hi def link rtlsvPreKw        PreProc
hi def link rtlsvModuleRef    Function
hi def link rtlsvInstName     Label
hi def link rtlsvPort         Identifier
hi def link rtlsvScope        Type
hi def link rtlsvSystask      Function
hi def link rtlsvDirective    PreProc
hi def link rtlsvEscIdent     Identifier
hi def link rtlsvNumber       Number
hi def link rtlsvEscape       SpecialChar
hi def link rtlsvFormat       SpecialChar
hi def link rtlsvString       String
hi def link rtlsvAttribute    SpecialComment
hi def link rtlsvTodo         Todo
hi def link rtlsvComment      Comment
hi def link rtlTplDelim       PreProc
hi def link rtlTplVar         Special

let b:current_syntax = 'rtlsv'
let &cpo = s:cpo_save
unlet s:cpo_save
