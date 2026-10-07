-- Layout in its own tab page, under the toolbar (the tabline):
--   +-------------+---------------------+
--   | HIERARCHY   #  source             |
--   |  tree       #                     |
--   |             #---------------------+
--   |-------------#  CONSOLE            |
--   | SEARCH      #                     |
--   +-------------+---------------------+
-- (# is the thick border of the hierarchy column)
local M = {}

M.tab = nil
M.wins = { tree = nil, source = nil, console = nil, search = nil }

local VIEW_NAMES = { template = 'Template', gen = 'Generated', integ = 'Integrated' }

local function valid(win)
  return win ~= nil and vim.api.nvim_win_is_valid(win)
end

local function app() return require('rtl_integ') end

local function esc(text)
  return (tostring(text):gsub('%%', '%%%%'))
end

function M.is_open()
  return M.tab ~= nil and vim.api.nvim_tabpage_is_valid(M.tab) and valid(M.wins.tree) and valid(M.wins.source)
end

local function set_panel_opts(win)
  vim.wo[win].number = false
  vim.wo[win].relativenumber = false
  vim.wo[win].signcolumn = 'no'
  vim.wo[win].foldcolumn = '0'
  vim.wo[win].spell = false
  vim.wo[win].wrap = false
  vim.wo[win].cursorline = true
  vim.wo[win].statuscolumn = ''
end

-- The hierarchy column (tree and search box): panel colours, header band,
-- and a thick bar as the separator on its right.
local function panel_look(win)
  local bar = app().config.tree_border == 'thin' and '┃' or '█'
  vim.wo[win].fillchars = table.concat({
    'vert:' .. bar, 'vertleft:' .. bar, 'vertright:' .. bar, 'verthoriz:' .. bar,
    'horizup:' .. bar, 'horizdown:' .. bar, 'horiz:━', 'eob: ',
  }, ',')
  vim.wo[win].winhighlight = table.concat({
    'Normal:RtlIntegPanel', 'NormalNC:RtlIntegPanel', 'EndOfBuffer:RtlIntegPanel', 'SignColumn:RtlIntegPanel',
    'WinSeparator:RtlIntegPanelBorder', 'WinBar:RtlIntegPanelHeader', 'WinBarNC:RtlIntegPanelHeader',
  }, ',')
end

-- ('winbar' and 'fillchars' are global-local: their window values are lost
-- when the window shows another buffer, so this runs again on BufWinEnter)
local function source_look(win)
  vim.wo[win].winfixwidth = false
  vim.wo[win].winfixheight = false
  vim.wo[win].winhighlight = 'WinBar:RtlIntegSourceBar,WinBarNC:RtlIntegSourceBar'
  -- corners on the hierarchy's border keep the thick bar
  local bar = app().config.tree_border == 'thin' and '┃' or '█'
  vim.wo[win].fillchars = 'vertright:' .. bar .. ',verthoriz:' .. bar
  vim.wo[win].winbar = "%{%v:lua.require'rtl_integ.layout'.source_bar()%}"
end

local function console_look(win)
  set_panel_opts(win)
  vim.wo[win].cursorline = false
  vim.wo[win].winhighlight = table.concat({
    'Normal:RtlIntegConsole', 'NormalNC:RtlIntegConsole', 'EndOfBuffer:RtlIntegConsole',
    'SignColumn:RtlIntegConsole', 'CursorLine:RtlIntegConsoleCursorLine',
    'WinBar:RtlIntegConsoleTitle', 'WinBarNC:RtlIntegConsoleTitle',
  }, ',')
  local bar = app().config.tree_border == 'thin' and '┃' or '█'
  vim.wo[win].fillchars = 'eob: ,vertright:' .. bar .. ',verthoriz:' .. bar
  vim.wo[win].winbar = ' CONSOLE %#RtlIntegConsoleDim#  <CR> command   C clear '
end

-- -- window bars (evaluated by Neovim; see 'winbar') --------------------------

function M.tree_bar()
  local tree = require('rtl_integ.tree')
  local out = { ' HIERARCHY ' }
  if tree.filter_text then table.insert(out, ' / ' .. esc(tree.filter_text) .. ' ') end
  if tree.search then
    table.insert(out, string.format(' %d match%s ', tree.match_count, tree.match_count == 1 and '' or 'es'))
  end
  return table.concat(out)
end

function M.search_bar()
  local tb = require('rtl_integ.toolbar')
  local scope = app().state.search_scope
  local segs = { { ' SEARCH  ', 'RtlIntegPanelHeader' } }
  for i, s in ipairs(tb.SCOPES) do
    local active = s[1] == scope
    table.insert(segs, { ' ' .. s[2] .. ' ', active and 'RtlIntegHeaderActive' or 'RtlIntegPanelHeader', tb.ID.scope + i - 1 })
  end
  return (tb.format(segs))
end

-- (a %{%...%} item is evaluated with the window being drawn as the current one)
function M.source_bar()
  local buf = vim.api.nvim_get_current_buf()
  local name = vim.api.nvim_buf_get_name(buf)
  if name == '' then return '' end
  local view = vim.b[buf].rtl_integ_view
  local summary = app().state.summary
  local rel = vim.fs.normalize(name)
  if summary and summary.root and summary.root ~= vim.NIL then
    local root = vim.fs.normalize(summary.root):gsub('/$', '') .. '/'
    if vim.startswith(rel:lower(), root:lower()) then rel = rel:sub(#root + 1) end
  end
  local out = { '%#RtlIntegSourceFile# ' .. esc(vim.fs.basename(name)) .. ' ' }
  if view and VIEW_NAMES[view] then
    table.insert(out, '%#RtlIntegSourceView# ' .. VIEW_NAMES[view] .. ' ')
  end
  table.insert(out, '%#RtlIntegSourcePath#  ' .. esc(rel))
  if vim.bo[buf].modified then table.insert(out, ' [+]') end
  if vim.bo[buf].readonly then table.insert(out, ' [read-only]') end
  return table.concat(out) .. '%#RtlIntegSourceBar#'
end

function M.style_source(win)
  if valid(win) and win == M.wins.source then source_look(win) end
end

function M.redraw_bars()
  pcall(vim.cmd, 'redrawstatus!')
end

-- -- opening and closing ------------------------------------------------------

function M.open(tree_buf, console_buf, search_buf, opts)
  opts = opts or {}
  if M.is_open() then
    vim.api.nvim_set_current_tabpage(M.tab)
    return M.wins
  end
  local cur = vim.api.nvim_get_current_buf()
  local reuse = vim.api.nvim_buf_get_name(cur) == '' and not vim.bo[cur].modified
    and vim.api.nvim_buf_line_count(cur) <= 1 and #vim.api.nvim_list_tabpages() == 1
    and #vim.api.nvim_tabpage_list_wins(0) == 1
  if not reuse then vim.cmd('tabnew') end
  M.tab = vim.api.nvim_get_current_tabpage()
  M.wins.source = vim.api.nvim_get_current_win()

  vim.cmd('belowright split')
  M.wins.console = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(M.wins.console, console_buf)
  vim.api.nvim_win_set_height(M.wins.console, opts.console_height or 12)
  vim.wo[M.wins.console].winfixheight = true
  console_look(M.wins.console)

  vim.api.nvim_set_current_win(M.wins.source)
  source_look(M.wins.source)
  vim.cmd('topleft vsplit')
  M.wins.tree = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(M.wins.tree, tree_buf)
  vim.wo[M.wins.tree].winfixwidth = true
  set_panel_opts(M.wins.tree)
  panel_look(M.wins.tree)
  vim.wo[M.wins.tree].winbar = "%{%v:lua.require'rtl_integ.layout'.tree_bar()%}"

  vim.cmd('belowright split')
  M.wins.search = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(M.wins.search, search_buf)
  set_panel_opts(M.wins.search)
  vim.wo[M.wins.search].cursorline = false
  panel_look(M.wins.search)
  vim.wo[M.wins.search].winbar = "%{%v:lua.require'rtl_integ.layout'.search_bar()%}"
  vim.api.nvim_win_set_height(M.wins.search, 1)
  vim.wo[M.wins.search].winfixheight = true
  pcall(function() vim.wo[M.wins.search].winfixbuf = true end)

  vim.api.nvim_win_set_width(M.wins.tree, opts.tree_width or 42)
  require('rtl_integ.toolbar').install()
  vim.api.nvim_create_autocmd('BufWinEnter', {
    group = vim.api.nvim_create_augroup('rtl_integ_layout', { clear = true }),
    callback = function()
      local win = vim.api.nvim_get_current_win()
      if win == M.wins.source then source_look(win) end
    end,
  })
  vim.api.nvim_set_current_win(M.wins.tree)
  return M.wins
end

-- The window that shows sources; recreated above the console (or at the
-- right) if it was closed.
function M.source_win()
  if valid(M.wins.source) then return M.wins.source end
  if valid(M.wins.tree) then
    local back = vim.api.nvim_get_current_win()
    if valid(M.wins.console) then
      vim.api.nvim_set_current_win(M.wins.console)
      vim.cmd('aboveleft split')
    else
      vim.api.nvim_set_current_win(M.wins.tree)
      vim.cmd('botright vsplit')
    end
    M.wins.source = vim.api.nvim_get_current_win()
    vim.wo[M.wins.source].number = true
    vim.wo[M.wins.source].cursorline = false
    source_look(M.wins.source)
    vim.api.nvim_win_set_width(M.wins.tree, app().config.tree_width or 42)
    if valid(back) then vim.api.nvim_set_current_win(back) end
    return M.wins.source
  end
  return vim.api.nvim_get_current_win()
end

function M.close()
  require('rtl_integ.toolbar').uninstall()
  if M.tab and vim.api.nvim_tabpage_is_valid(M.tab) and #vim.api.nvim_list_tabpages() > 1 then
    local nr = vim.api.nvim_tabpage_get_number(M.tab)
    vim.cmd('tabclose ' .. nr)
  end
  M.tab = nil
  M.wins = { tree = nil, source = nil, console = nil, search = nil }
end

return M
