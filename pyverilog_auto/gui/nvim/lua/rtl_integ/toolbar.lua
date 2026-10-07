-- Toolbar: the tabline of the GUI's tab page, with clickable buttons for the
-- right-panel view, the hierarchy labels, the theme, and Build / Undo / Help.
-- Clicks (here and in the search box header) go to RtlIntegClick(id, ...).
local M = {}

-- { value, label, short label (narrow windows) }
M.VIEWS = { { 'template', 'Template', 'Tpl' }, { 'gen', 'Generated', 'Gen' }, { 'integ', 'Integrated', 'Integ' } }
M.LABELS = { { 'name', 'Inst', 'Inst' }, { 'module', 'Inst (Mod)', '+Mod' }, { 'file', 'Inst : Mod : File', '+File' } }
M.SCOPES = { { 'both', 'Both' }, { 'inst', 'Inst' }, { 'module', 'Module' } }
M.ID = { view = 1, label = 11, theme = 21, build = 31, undo = 32, help = 33, scope = 41 }

M.saved = nil       -- tabline options before the toolbar was installed
M.cols = {}         -- click id -> screen column of the left-side buttons
M.theme_col = 0     -- screen column of the Theme button (for its menu)

vim.cmd([[
function! RtlIntegClick(minwid, clicks, button, mods) abort
  call v:lua.require'rtl_integ.toolbar'.click(a:minwid, a:clicks, a:button, a:mods)
endfunction
]])

local function esc(text)
  return (tostring(text):gsub('%%', '%%%%'))
end

-- A statusline-format string from segments { text, hl, click_id }; also
-- returns the display column where each click id starts, and the width.
function M.format(segs)
  local out, cols, col = {}, {}, 0
  for _, s in ipairs(segs) do
    local text, group, id = s[1], s[2], s[3]
    if id then
      cols[id] = cols[id] or col
      table.insert(out, '%' .. id .. '@RtlIntegClick@')
    end
    if group then table.insert(out, '%#' .. group .. '#') end
    table.insert(out, esc(text))
    if id then table.insert(out, '%X') end
    col = col + vim.fn.strdisplaywidth(text)
  end
  return table.concat(out), cols, col
end

local function button(segs, id, text, active)
  table.insert(segs, { ' ' .. text .. ' ', active and 'RtlIntegToolbarActive' or 'RtlIntegToolbarButton', id })
  table.insert(segs, { ' ', 'RtlIntegToolbar' })
end

local function project_name(app)
  local s = app.state.summary
  if not s then return 'no project' end
  local p = s.project
  if p and p ~= vim.NIL and p ~= '' then
    local dir = vim.fs.basename(vim.fs.dirname(vim.fs.normalize(p)))
    if dir and dir ~= '' then return dir end
  end
  if s.root and s.root ~= vim.NIL then return vim.fs.basename(vim.fs.normalize(s.root)) end
  return 'design'
end

local function plain_tabs()
  local out = {}
  local cur = vim.api.nvim_get_current_tabpage()
  local gui = require('rtl_integ.layout').tab
  for i, tp in ipairs(vim.api.nvim_list_tabpages()) do
    local name
    if tp == gui then
      name = 'rtl_integ'
    else
      local buf = vim.api.nvim_win_get_buf(vim.api.nvim_tabpage_get_win(tp))
      name = vim.fn.fnamemodify(vim.api.nvim_buf_get_name(buf), ':t')
      if name == '' then name = '[No Name]' end
    end
    table.insert(out, '%' .. i .. 'T' .. (tp == cur and '%#TabLineSel#' or '%#TabLine#') .. ' ' .. esc(name) .. ' ')
  end
  return table.concat(out) .. '%#TabLineFill#%T'
end

-- Left part of the toolbar; *level* 0 (full) to 4 (narrowest window): no
-- project name, no captions, short label names, short view / theme names.
local function left_segments(app, level)
  local st = app.state
  local short = level >= 4
  local segs = { { ' rtl_integ ', 'RtlIntegToolbarTitle' } }
  if level < 1 then table.insert(segs, { ' ' .. project_name(app) .. ' ', 'RtlIntegToolbar' }) end
  local function caption(text)
    table.insert(segs, { '│', 'RtlIntegToolbarSep' })
    table.insert(segs, { level < 2 and (' ' .. text .. ' ') or ' ', 'RtlIntegToolbarLabel' })
  end
  caption('View')
  for i, v in ipairs(M.VIEWS) do button(segs, M.ID.view + i - 1, short and v[3] or v[2], st.view == v[1]) end
  caption('Labels')
  for i, l in ipairs(M.LABELS) do button(segs, M.ID.label + i - 1, level >= 3 and l[3] or l[2], st.label == l[1]) end
  caption('Theme')
  local theme = require('rtl_integ.themes').display_name()
  button(segs, M.ID.theme, (short and 'Theme' or theme) .. ' ▾', false)
  return segs
end

function M.render()
  local layout = require('rtl_integ.layout')
  if layout.tab == nil or layout.tab ~= vim.api.nvim_get_current_tabpage() then return plain_tabs() end
  local app = require('rtl_integ')
  local right, _, rwidth = M.format({
    { ' Build ', 'RtlIntegToolbarAction', M.ID.build }, { ' ', 'RtlIntegToolbar' },
    { ' Undo ', 'RtlIntegToolbarAction', M.ID.undo }, { ' ', 'RtlIntegToolbar' },
    { ' Help ', 'RtlIntegToolbarAction', M.ID.help }, { ' ', 'RtlIntegToolbar' },
  })
  local left, cols
  for level = 0, 4 do
    local width
    left, cols, width = M.format(left_segments(app, level))
    if width + rwidth < vim.o.columns then break end
  end
  M.cols = cols
  M.theme_col = cols[M.ID.theme] or 0
  return left .. '%#RtlIntegToolbar#%=' .. right
end

function M.redraw()
  pcall(vim.cmd, 'redrawtabline')
end

-- Show the toolbar (saving the tabline options to restore on close).
function M.install()
  if not M.saved then
    M.saved = { tabline = vim.o.tabline, showtabline = vim.o.showtabline }
  end
  vim.o.showtabline = 2
  vim.o.tabline = "%!v:lua.require'rtl_integ.toolbar'.render()"
end

function M.uninstall()
  if M.saved then
    vim.o.tabline = M.saved.tabline
    vim.o.showtabline = M.saved.showtabline
    M.saved = nil
  end
end

function M.click(id, _clicks, button_name, _mods)
  if button_name ~= 'l' then return end
  local app = require('rtl_integ')
  if id >= M.ID.view and id < M.ID.view + #M.VIEWS then
    app.set_view(M.VIEWS[id - M.ID.view + 1][1])
  elseif id >= M.ID.label and id < M.ID.label + #M.LABELS then
    app.set_label(M.LABELS[id - M.ID.label + 1][1])
  elseif id == M.ID.theme then
    M.theme_menu()
  elseif id == M.ID.build then
    app.build(false)
  elseif id == M.ID.undo then
    app.undo()
  elseif id == M.ID.help then
    app.help()
  elseif id >= M.ID.scope and id < M.ID.scope + #M.SCOPES then
    app.set_search_scope(M.SCOPES[id - M.ID.scope + 1][1])
  end
end

-- Theme menu under the Theme button: moving the cursor previews a theme,
-- <CR> or a click keeps it, <Esc> / q (or leaving) goes back.
function M.theme_menu()
  local themes = require('rtl_integ.themes')
  local items = themes.list()
  local start = vim.g.colors_name
  local lines, width, cur = {}, 0, 1
  for i, t in ipairs(items) do
    local note = t.builtin and '  (Neovim)' or (t.background == 'light' and '  (light)' or '')
    lines[i] = ' ' .. t.name .. note .. ' '
    width = math.max(width, vim.fn.strdisplaywidth(lines[i]))
    if t.id == start then cur = i end
  end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.bo[buf].bufhidden = 'wipe'
  local height = math.max(1, math.min(#lines, vim.o.lines - 6))
  local col = math.max(0, math.min(M.theme_col, vim.o.columns - width - 2))
  local win = vim.api.nvim_open_win(buf, true, {
    relative = 'editor', row = 1, col = col, width = width, height = height,
    border = 'rounded', title = ' Theme ', title_pos = 'center', style = 'minimal',
  })
  vim.wo[win].cursorline = true
  vim.wo[win].winhighlight = 'Normal:NormalFloat,CursorLine:PmenuSel'
  vim.api.nvim_win_set_cursor(win, { cur, 0 })
  local done = false
  local function close(keep)
    if done then return end
    done = true
    if vim.api.nvim_win_is_valid(win) then vim.api.nvim_win_close(win, true) end
    if keep then
      require('rtl_integ').set_theme(keep)
    elseif start and vim.g.colors_name ~= start then
      pcall(vim.cmd.colorscheme, start)
      M.redraw()
    end
  end
  local function selected()
    return items[vim.api.nvim_win_get_cursor(win)[1]]
  end
  local group = vim.api.nvim_create_augroup('rtl_integ_theme_menu', { clear = true })
  vim.api.nvim_create_autocmd('CursorMoved', {
    group = group, buffer = buf,
    callback = function()
      local t = selected()
      if t and t.id ~= vim.g.colors_name then
        pcall(vim.cmd.colorscheme, t.id)
        M.redraw()
      end
    end,
  })
  vim.api.nvim_create_autocmd({ 'WinLeave', 'BufLeave' }, {
    group = group, buffer = buf, once = true,
    callback = function() vim.schedule(function() close(nil) end) end,
  })
  local map = function(lhs, fn) vim.keymap.set('n', lhs, fn, { buffer = buf, nowait = true, silent = true }) end
  map('<CR>', function() close(selected().id) end)
  -- a click picks; the release of the click that opened the menu is ignored
  local pressed = false
  map('<LeftMouse>', function()
    local pos = vim.fn.getmousepos()
    if pos.winid == win and pos.line > 0 then
      vim.api.nvim_win_set_cursor(win, { pos.line, 0 })
      pressed = true
    else
      close(nil)
    end
  end)
  map('<LeftRelease>', function()
    if pressed then close(selected().id) end
  end)
  map('<Esc>', function() close(nil) end)
  map('q', function() close(nil) end)
end

return M
