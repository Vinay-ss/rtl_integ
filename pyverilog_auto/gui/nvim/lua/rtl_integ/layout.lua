-- Three-pane layout in its own tab page:
--   +-----------+---------------------+
--   | hierarchy |  source             |
--   |  tree     +---------------------+
--   |           |  console            |
--   +-----------+---------------------+
local M = {}

M.tab = nil
M.wins = { tree = nil, source = nil, console = nil }

local function valid(win)
  return win ~= nil and vim.api.nvim_win_is_valid(win)
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
end

function M.open(tree_buf, console_buf, opts)
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
  set_panel_opts(M.wins.console)
  vim.wo[M.wins.console].cursorline = false

  vim.api.nvim_set_current_win(M.wins.source)
  vim.cmd('topleft vsplit')
  M.wins.tree = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(M.wins.tree, tree_buf)
  vim.api.nvim_win_set_width(M.wins.tree, opts.tree_width or 42)
  vim.wo[M.wins.tree].winfixwidth = true
  set_panel_opts(M.wins.tree)
  return M.wins
end

-- The window that shows sources; recreated next to the tree if it was closed.
function M.source_win()
  if valid(M.wins.source) then return M.wins.source end
  if valid(M.wins.tree) then
    vim.api.nvim_set_current_win(M.wins.tree)
    vim.cmd('belowright vsplit')
    M.wins.source = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_width(M.wins.tree, 42)
    return M.wins.source
  end
  return vim.api.nvim_get_current_win()
end

function M.close()
  if M.tab and vim.api.nvim_tabpage_is_valid(M.tab) and #vim.api.nvim_list_tabpages() > 1 then
    local nr = vim.api.nvim_tabpage_get_number(M.tab)
    vim.cmd('tabclose ' .. nr)
  end
  M.tab = nil
  M.wins = { tree = nil, source = nil, console = nil }
end

return M
