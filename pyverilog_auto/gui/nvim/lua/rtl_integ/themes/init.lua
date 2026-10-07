-- Themes: the rtl-<id> colorschemes (palettes.lua) and the colours of the
-- GUI's own panels (toolbar, hierarchy, search box, console), which are also
-- derived for any other colorscheme so the panels fit every theme.
local M = {}

M.palettes = require('rtl_integ.themes.palettes')
M.PREFIX = 'rtl-'

-- -- colours ------------------------------------------------------------------

local function rgb(hex)
  hex = hex:gsub('#', '')
  return tonumber(hex:sub(1, 2), 16), tonumber(hex:sub(3, 4), 16), tonumber(hex:sub(5, 6), 16)
end

-- t * a + (1 - t) * b
function M.blend(a, b, t)
  local ar, ag, ab = rgb(a)
  local br, bg, bb = rgb(b)
  local function mix(x, y) return math.floor(t * x + (1 - t) * y + 0.5) end
  return string.format('#%02x%02x%02x', mix(ar, br), mix(ag, bg), mix(ab, bb))
end

-- WCAG relative luminance (0 black .. 1 white)
function M.luminance(hex)
  local r, g, b = rgb(hex)
  local function lin(c)
    c = c / 255
    return c <= 0.03928 and c / 12.92 or ((c + 0.055) / 1.055) ^ 2.4
  end
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
end

local function contrast(a, b)
  local la, lb = M.luminance(a), M.luminance(b)
  if la < lb then la, lb = lb, la end
  return (la + 0.05) / (lb + 0.05)
end

-- Text colour for a background *c*: the palette's bg or fg, whichever reads better.
local function on(c, p)
  local best, score = p.bg, contrast(c, p.bg)
  for _, cand in ipairs({ p.fg, '#ffffff', '#000000' }) do
    local s = contrast(c, cand)
    if s > score + 1.5 then best, score = cand, s end
  end
  return best
end

local function hl(name, spec)
  vim.api.nvim_set_hl(0, name, spec)
end

-- -- the editor (rtl-* colorschemes only) --------------------------------------

local function editor_groups(p)
  local s = p.syn
  local acc_fg = on(p.accent, p)
  hl('Normal', { fg = p.fg, bg = p.bg })
  hl('NormalNC', { fg = p.fg, bg = p.bg })
  hl('NormalFloat', { fg = p.fg, bg = p.bg_dark })
  hl('FloatBorder', { fg = p.accent, bg = p.bg_dark })
  hl('FloatTitle', { fg = p.accent, bg = p.bg_dark, bold = true })
  hl('Cursor', { fg = p.bg, bg = p.fg })
  hl('lCursor', { link = 'Cursor' })
  hl('CursorIM', { link = 'Cursor' })
  hl('TermCursor', { link = 'Cursor' })
  hl('CursorLine', { bg = p.bg_hl })
  hl('CursorColumn', { bg = p.bg_hl })
  hl('ColorColumn', { bg = p.bg_hl })
  hl('CursorLineNr', { fg = p.accent, bold = true })
  hl('LineNr', { fg = p.gutter })
  hl('LineNrAbove', { fg = p.gutter })
  hl('LineNrBelow', { fg = p.gutter })
  hl('SignColumn', { bg = p.bg })
  hl('FoldColumn', { fg = p.gutter, bg = p.bg })
  hl('Folded', { fg = p.fg_dim, bg = p.bg_hl })
  hl('Visual', { bg = p.bg_vis })
  hl('VisualNOS', { bg = p.bg_vis })
  hl('Search', { fg = p.fg, bg = M.blend(p.yellow, p.bg, 0.35) })
  hl('IncSearch', { fg = p.bg, bg = p.orange, bold = true })
  hl('CurSearch', { link = 'IncSearch' })
  hl('Substitute', { fg = p.bg, bg = p.red })
  hl('MatchParen', { fg = p.orange, bg = p.bg_hl, bold = true })
  hl('NonText', { fg = p.gutter })
  hl('EndOfBuffer', { fg = p.bg })
  hl('Whitespace', { fg = p.gutter })
  hl('SpecialKey', { fg = p.gutter })
  hl('Conceal', { fg = p.gutter })
  hl('Pmenu', { fg = p.fg, bg = p.bg_dark })
  hl('PmenuSel', { fg = p.fg, bg = p.bg_vis, bold = true })
  hl('PmenuSbar', { bg = p.bg_hl })
  hl('PmenuThumb', { bg = p.gutter })
  hl('PmenuKind', { fg = p.syn.type, bg = p.bg_dark })
  hl('PmenuExtra', { fg = p.comment, bg = p.bg_dark })
  hl('WildMenu', { link = 'PmenuSel' })
  hl('StatusLine', { fg = p.fg_dim, bg = p.bg_dark })
  hl('StatusLineNC', { fg = p.gutter, bg = p.bg_dark })
  hl('TabLine', { fg = p.fg_dim, bg = p.bg_dark })
  hl('TabLineSel', { fg = acc_fg, bg = p.accent, bold = true })
  hl('TabLineFill', { bg = p.bg_dark })
  hl('WinBar', { fg = p.fg_dim, bg = p.bg, bold = true })
  hl('WinBarNC', { fg = p.comment, bg = p.bg })
  hl('WinSeparator', { fg = p.border == p.bg and p.bg_hl or p.border, bg = p.bg })
  hl('VertSplit', { link = 'WinSeparator' })
  hl('Directory', { fg = p.blue })
  hl('Title', { fg = p.accent, bold = true })
  hl('ErrorMsg', { fg = p.red, bold = true })
  hl('WarningMsg', { fg = p.yellow })
  hl('MoreMsg', { fg = p.green })
  hl('ModeMsg', { fg = p.fg_dim, bold = true })
  hl('MsgArea', { fg = p.fg })
  hl('Question', { fg = p.blue })
  hl('QuickFixLine', { bg = p.bg_vis, bold = true })
  hl('DiffAdd', { bg = M.blend(p.green, p.bg, 0.2) })
  hl('DiffDelete', { bg = M.blend(p.red, p.bg, 0.2) })
  hl('DiffChange', { bg = M.blend(p.blue, p.bg, 0.15) })
  hl('DiffText', { bg = M.blend(p.blue, p.bg, 0.35) })
  hl('Added', { fg = p.green })
  hl('Removed', { fg = p.red })
  hl('Changed', { fg = p.blue })
  hl('diffAdded', { fg = p.green })
  hl('diffRemoved', { fg = p.red })
  hl('diffChanged', { fg = p.blue })
  hl('SpellBad', { sp = p.red, undercurl = true })
  hl('SpellCap', { sp = p.yellow, undercurl = true })
  hl('SpellLocal', { sp = p.cyan, undercurl = true })
  hl('SpellRare', { sp = p.purple, undercurl = true })
  for name, c in pairs({ Error = p.red, Warn = p.yellow, Info = p.blue, Hint = p.cyan, Ok = p.green }) do
    hl('Diagnostic' .. name, { fg = c })
    hl('DiagnosticUnderline' .. name, { sp = c, undercurl = true })
    hl('DiagnosticVirtualText' .. name, { fg = c, bg = M.blend(c, p.bg, 0.1) })
  end

  -- syntax
  hl('Comment', { fg = p.comment, italic = true })
  hl('Constant', { fg = s.const })
  hl('String', { fg = s.string })
  hl('Character', { fg = s.string })
  hl('Number', { fg = s.number })
  hl('Boolean', { fg = s.number })
  hl('Float', { fg = s.number })
  hl('Identifier', { fg = s.ident })
  hl('Function', { fg = s.func })
  hl('Statement', { fg = s.statement })
  hl('Conditional', { fg = s.cond })
  hl('Repeat', { fg = s.cond })
  hl('Label', { fg = s.label })
  hl('Operator', { fg = s.operator })
  hl('Keyword', { fg = s.keyword })
  hl('Exception', { fg = s.statement })
  hl('PreProc', { fg = s.preproc })
  hl('Include', { fg = s.preproc })
  hl('Define', { fg = s.preproc })
  hl('Macro', { fg = s.preproc })
  hl('PreCondit', { fg = s.preproc })
  hl('Type', { fg = s.type })
  hl('StorageClass', { fg = s.storage })
  hl('Structure', { fg = s.type })
  hl('Typedef', { fg = s.type })
  hl('Special', { fg = s.special })
  hl('SpecialChar', { fg = s.special })
  hl('Tag', { fg = s.special })
  hl('Delimiter', { fg = s.delim })
  hl('SpecialComment', { fg = p.comment, bold = true })
  hl('Debug', { fg = p.orange })
  hl('Underlined', { underline = true })
  hl('Ignore', { fg = p.gutter })
  hl('Error', { fg = p.red, bold = true })
  hl('Todo', { fg = p.bg, bg = p.yellow, bold = true })

  local term = { p.bg_hl, p.red, p.green, p.yellow, p.blue, p.magenta, p.cyan, p.fg_dim,
                 p.gutter, p.red, p.green, p.yellow, p.blue, p.magenta, p.cyan, p.fg }
  for i, c in ipairs(term) do vim.g['terminal_color_' .. (i - 1)] = c end
end

-- -- palette of an arbitrary colorscheme ---------------------------------------

local function get(name, attr)
  local ok, h = pcall(vim.api.nvim_get_hl, 0, { name = name, link = false })
  if ok and h and h[attr] then return string.format('#%06x', h[attr]) end
  return nil
end

-- A palette guessed from the highlight groups of the current colorscheme.
function M.derive()
  local dark = vim.o.background == 'dark'
  local bg = get('Normal', 'bg') or (dark and '#1c1c1c' or '#ffffff')
  local fg = get('Normal', 'fg') or (dark and '#d0d0d0' or '#202020')
  local p = { background = vim.o.background, bg = bg, fg = fg }
  p.bg_dark = dark and M.blend(bg, '#000000', 0.7) or M.blend(bg, '#000000', 0.95)
  p.bg_hl = get('CursorLine', 'bg') or M.blend(fg, bg, 0.08)
  p.bg_vis = get('Visual', 'bg') or M.blend(fg, bg, 0.2)
  p.fg_dim = M.blend(fg, bg, 0.75)
  p.comment = get('Comment', 'fg') or M.blend(fg, bg, 0.5)
  p.gutter = get('LineNr', 'fg') or M.blend(fg, bg, 0.35)
  p.border = get('WinSeparator', 'fg') or p.gutter
  p.accent = get('Function', 'fg') or get('Title', 'fg') or get('Statement', 'fg') or fg
  p.red = get('DiagnosticError', 'fg') or get('ErrorMsg', 'fg') or '#e06c75'
  p.yellow = get('DiagnosticWarn', 'fg') or get('WarningMsg', 'fg') or '#e5c07b'
  p.green = get('DiagnosticOk', 'fg') or get('String', 'fg') or '#98c379'
  p.blue = get('DiagnosticInfo', 'fg') or get('Function', 'fg') or '#61afef'
  p.cyan = get('DiagnosticHint', 'fg') or get('Special', 'fg') or '#56b6c2'
  p.orange = get('Constant', 'fg') or get('Number', 'fg') or p.yellow
  p.purple = get('Statement', 'fg') or p.blue
  p.magenta = p.purple
  p.syn = {
    type = get('Type', 'fg') or p.accent,
    ident = get('Identifier', 'fg') or fg,
    special = get('Special', 'fg') or p.cyan,
    tplvar = p.orange,
  }
  return p
end

-- The palette in use: an rtl-* theme's, or one derived from the colorscheme.
function M.current()
  local name = vim.g.colors_name or ''
  if vim.startswith(name, M.PREFIX) then
    local p = M.palettes[name:sub(#M.PREFIX + 1)]
    if p then return p end
  end
  return M.derive()
end

-- -- the GUI's panels -----------------------------------------------------------

-- The console is dark in every theme: a darker shade of a dark theme, a fixed
-- night palette under a light one.
local NIGHT = {
  bg = '#13141c', hl = '#222436', fg = '#c8d3f5', dim = '#7a88cf', red = '#ff757f', yellow = '#ffc777',
  green = '#c3e88d', cyan = '#86e1fc', blue = '#82aaff', accent = '#c099ff',
}

function M.console_palette(p)
  if M.luminance(p.bg) > 0.18 then return NIGHT end
  local bg = M.blend(p.bg_dark, '#000000', 0.6)
  return {
    bg = bg, hl = M.blend(p.bg_hl, bg, 0.7), fg = p.fg, dim = p.comment, red = p.red, yellow = p.yellow,
    green = p.green, cyan = p.cyan, blue = p.blue, accent = p.accent,
  }
end

function M.panel_groups(p)
  p = p or M.current()
  local acc_fg = on(p.accent, p)
  local syn = p.syn or {}
  -- toolbar (tabline)
  hl('RtlIntegToolbar', { fg = p.fg_dim, bg = p.bg_dark })
  hl('RtlIntegToolbarTitle', { fg = acc_fg, bg = p.accent, bold = true })
  hl('RtlIntegToolbarLabel', { fg = p.comment, bg = p.bg_dark })
  hl('RtlIntegToolbarButton', { fg = p.fg, bg = p.bg_hl })
  hl('RtlIntegToolbarActive', { fg = acc_fg, bg = p.accent, bold = true })
  hl('RtlIntegToolbarAction', { fg = p.accent, bg = p.bg_hl, bold = true })
  hl('RtlIntegToolbarSep', { fg = p.gutter, bg = p.bg_dark })
  -- hierarchy column: panel, header bands, thick border
  hl('RtlIntegPanel', { fg = p.fg, bg = p.bg_dark })
  hl('RtlIntegPanelHeader', { fg = acc_fg, bg = p.accent, bold = true })
  hl('RtlIntegHeaderActive', { fg = p.accent, bg = p.bg_dark, bold = true })
  hl('RtlIntegPanelBorder', { fg = p.accent, bg = p.bg_dark })
  -- tree
  hl('RtlIntegName', { fg = p.fg, bold = true })
  hl('RtlIntegModule', { fg = syn.type or p.accent })
  hl('RtlIntegFile', { fg = p.comment, italic = true })
  hl('RtlIntegPunct', { fg = p.gutter })
  hl('RtlIntegIcon', { fg = p.accent })
  hl('RtlIntegTag', { fg = syn.special or p.cyan })
  hl('RtlIntegTagCode', { fg = p.yellow })
  hl('RtlIntegMarked', { fg = p.bg, bg = p.yellow, bold = true })
  hl('RtlIntegMarkSign', { fg = p.yellow, bold = true })
  hl('RtlIntegMatch', { fg = p.bg, bg = p.orange, bold = true })
  -- search box
  hl('RtlIntegSearchHint', { fg = p.comment, italic = true })
  -- source panel
  hl('RtlIntegSpan', { bg = M.blend(p.accent, p.bg, 0.16) })
  hl('RtlIntegSourceFile', { fg = p.fg, bg = p.bg_dark, bold = true })
  hl('RtlIntegSourceView', { fg = acc_fg, bg = p.accent, bold = true })
  hl('RtlIntegSourcePath', { fg = p.comment, bg = p.bg_dark, italic = true })
  hl('RtlIntegSourceBar', { fg = p.fg_dim, bg = p.bg_dark })
  -- console
  local c = M.console_palette(p)
  hl('RtlIntegConsole', { fg = c.fg, bg = c.bg })
  hl('RtlIntegConsoleCursorLine', { bg = c.hl })
  hl('RtlIntegConsoleTitle', { fg = c.fg, bg = c.hl, bold = true })
  hl('RtlIntegConsoleDim', { fg = c.dim, bg = c.hl })
  hl('RtlIntegConsoleSep', { fg = c.hl, bg = c.bg })
  hl('RtlIntegConsoleError', { fg = c.red, bold = true })
  hl('RtlIntegConsoleWarn', { fg = c.yellow })
  hl('RtlIntegConsoleNote', { fg = c.cyan })
  hl('RtlIntegConsoleCmd', { fg = c.accent, bold = true })
  hl('RtlIntegConsoleDiffAdd', { fg = c.green })
  hl('RtlIntegConsoleDiffDel', { fg = c.red })
  hl('RtlIntegConsoleDiffHdr', { fg = c.blue, bold = true })
  -- template code in the source panel (syntax/rtlsv.vim)
  hl('rtlTplDelim', { fg = p.accent, bold = true })
  hl('rtlTplVar', { fg = syn.tplvar or p.orange, bold = true })
end

-- -- loading -------------------------------------------------------------------

-- Load the palette theme *id* (called by colors/rtl-<id>.lua).
function M.load(id)
  local p = M.palettes[id]
  if not p then error('rtl_integ: no theme ' .. tostring(id)) end
  if vim.g.colors_name then vim.cmd('hi clear') end
  if vim.fn.exists('syntax_on') == 1 then vim.cmd('syntax reset') end
  vim.o.background = p.background
  vim.g.colors_name = M.PREFIX .. id
  editor_groups(p)
  M.panel_groups(p)
end

-- Themes for the picker: the palette themes, then every other colorscheme
-- on the runtimepath (Neovim's own and installed ones).
function M.list()
  local out, seen = {}, {}
  for _, id in ipairs(M.palettes.order) do
    local p = M.palettes[id]
    table.insert(out, { id = M.PREFIX .. id, name = p.name, background = p.background, builtin = false })
    seen[M.PREFIX .. id] = true
  end
  for _, name in ipairs(vim.fn.getcompletion('', 'color')) do
    if not seen[name] and not vim.startswith(name, M.PREFIX) then
      table.insert(out, { id = name, name = name, builtin = true })
      seen[name] = true
    end
  end
  return out
end

function M.display_name(colors_name)
  colors_name = colors_name or vim.g.colors_name or 'default'
  if vim.startswith(colors_name, M.PREFIX) then
    local p = M.palettes[colors_name:sub(#M.PREFIX + 1)]
    if p then return p.name end
  end
  return colors_name
end

-- Panel colours for whatever colorscheme is active (also on ColorScheme).
function M.setup()
  M.panel_groups()
end

return M
