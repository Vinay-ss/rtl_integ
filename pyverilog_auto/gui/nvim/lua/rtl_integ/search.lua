-- Search box under the hierarchy: filters the tree by instance and/or module
-- name while typing (case-insensitive unless the text has a capital; * and ?
-- are wildcards).  The scope buttons are in the window bar.
local M = {}

local ns = vim.api.nvim_create_namespace('rtl_integ_search')
local HINT = 'type to filter instances / modules'
M.buf = nil

local function tree() return require('rtl_integ.tree') end
local function app() return require('rtl_integ') end

local function valid(win)
  return win ~= nil and vim.api.nvim_win_is_valid(win)
end

function M.text()
  return vim.api.nvim_buf_get_lines(M.ensure_buf(), 0, 1, false)[1] or ''
end

local function show_hint()
  vim.api.nvim_buf_clear_namespace(M.buf, ns, 0, -1)
  if M.text() == '' then
    vim.api.nvim_buf_set_extmark(M.buf, ns, 0, 0, {
      virt_text = { { HINT, 'RtlIntegSearchHint' } }, virt_text_pos = 'overlay',
    })
  end
end

-- Re-filter the tree with the current text and scope.
function M.apply()
  tree().set_search(M.text(), app().state.search_scope)
  pcall(vim.cmd, 'redrawstatus!')
end

function M.changed()
  local buf = M.buf
  if vim.api.nvim_buf_line_count(buf) > 1 then        -- a pasted or split line: keep one
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { table.concat(lines, ' ') })
  end
  show_hint()
  M.apply()
end

function M.set_text(text)
  vim.api.nvim_buf_set_lines(M.ensure_buf(), 0, -1, false, { text or '' })
  M.changed()
end

function M.clear()
  M.set_text('')
end

function M.focus()
  local win = require('rtl_integ.layout').wins.search
  if not valid(win) then return end
  vim.api.nvim_set_current_win(win)
  vim.cmd('startinsert!')
end

-- Back to the tree (keeping the filter); with *jump*, on the first match.
function M.leave(jump)
  vim.cmd('stopinsert')
  vim.schedule(function()
    local win = require('rtl_integ.layout').wins.tree
    if valid(win) then vim.api.nvim_set_current_win(win) end
    if jump then tree().goto_first_match() end
  end)
end

function M.cycle_scope()
  local order = { both = 'inst', inst = 'module', module = 'both' }
  app().set_search_scope(order[app().state.search_scope] or 'both')
end

function M.ensure_buf()
  if M.buf and vim.api.nvim_buf_is_valid(M.buf) then return M.buf end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(buf, 'rtlinteg://search')
  vim.bo[buf].buftype = 'nofile'
  vim.bo[buf].bufhidden = 'hide'
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = 'rtlsearch'
  M.buf = buf
  local group = vim.api.nvim_create_augroup('rtl_integ_search', { clear = true })
  vim.api.nvim_create_autocmd({ 'TextChanged', 'TextChangedI', 'TextChangedP' }, {
    group = group, buffer = buf, callback = M.changed,
  })
  vim.api.nvim_create_autocmd('WinEnter', {
    group = group, buffer = buf, callback = function() vim.cmd('startinsert!') end,
  })
  local function map(modes, lhs, fn, desc)
    vim.keymap.set(modes, lhs, fn, { buffer = buf, nowait = true, silent = true, desc = 'rtl_integ: ' .. desc })
  end
  map({ 'i', 'n' }, '<CR>', function() M.leave(true) end, 'go to the first match')
  map({ 'i', 'n' }, '<Esc>', function() M.leave(false) end, 'back to the hierarchy')
  map('n', 'q', function() M.leave(false) end, 'back to the hierarchy')
  map({ 'i', 'n' }, '<C-c>', M.clear, 'clear the search')
  map({ 'i', 'n' }, '<Tab>', M.cycle_scope, 'search scope: both / instances / modules')
  show_hint()
  return buf
end

return M
