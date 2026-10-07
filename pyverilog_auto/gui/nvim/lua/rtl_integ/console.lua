-- Console panel: log, build output, diagnostics, operation previews, and a
-- command line (":RtlConsole CMD" or <CR> in the console) sent to the backend.
local M = {}

local ns = vim.api.nvim_create_namespace('rtl_integ_console')
M.buf = nil

local level_hl = {
  error = 'DiagnosticError',
  warning = 'DiagnosticWarn',
  note = 'DiagnosticInfo',
  info = nil,
  cmd = 'Title',
  diff_add = 'DiffAdd',
  diff_del = 'DiffDelete',
  diff_hdr = 'DiffText',
}

function M.ensure_buf()
  if M.buf and vim.api.nvim_buf_is_valid(M.buf) then return M.buf end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(buf, 'rtlinteg://console')
  vim.bo[buf].buftype = 'nofile'
  vim.bo[buf].bufhidden = 'hide'
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = 'rtlconsole'
  vim.bo[buf].modifiable = false
  vim.keymap.set('n', '<CR>', function() M.prompt() end, { buffer = buf, desc = 'rtl_integ: console command' })
  vim.keymap.set('n', 'i', function() M.prompt() end, { buffer = buf, desc = 'rtl_integ: console command' })
  vim.keymap.set('n', 'C', function() M.clear() end, { buffer = buf, desc = 'rtl_integ: clear console' })
  M.buf = buf
  return buf
end

local function scroll_to_end()
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == M.buf then
      local n = vim.api.nvim_buf_line_count(M.buf)
      pcall(vim.api.nvim_win_set_cursor, win, { n, 0 })
    end
  end
end

-- Append text (may contain newlines) with an optional level.
function M.append(text, level)
  local buf = M.ensure_buf()
  local lines = vim.split(tostring(text or ''), '\n', { plain = true })
  vim.bo[buf].modifiable = true
  local n = vim.api.nvim_buf_line_count(buf)
  local first = vim.api.nvim_buf_get_lines(buf, 0, 1, false)[1]
  local start = n
  if n == 1 and first == '' then
    vim.api.nvim_buf_set_lines(buf, 0, 1, false, lines)
    start = 0
  else
    vim.api.nvim_buf_set_lines(buf, n, n, false, lines)
  end
  vim.bo[buf].modifiable = false
  for i, line in ipairs(lines) do
    local hl = level_hl[level or 'info']
    if level == 'diff' then
      if line:sub(1, 3) == '+++' or line:sub(1, 3) == '---' or line:sub(1, 2) == '@@' then
        hl = level_hl.diff_hdr
      elseif line:sub(1, 1) == '+' then
        hl = level_hl.diff_add
      elseif line:sub(1, 1) == '-' then
        hl = level_hl.diff_del
      end
    end
    if hl and #line > 0 then
      vim.api.nvim_buf_set_extmark(buf, ns, start + i - 1, 0, { end_col = #line, hl_group = hl })
    end
  end
  scroll_to_end()
end

function M.clear()
  local buf = M.ensure_buf()
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {})
  vim.bo[buf].modifiable = false
  vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
end

function M.lines()
  local buf = M.ensure_buf()
  return vim.api.nvim_buf_get_lines(buf, 0, -1, false)
end

-- Send a console command to the backend and print the answer.
function M.run(text, on_done)
  local rpc = require('rtl_integ.rpc')
  M.append('> ' .. text, 'cmd')
  rpc.request('console_cmd', { text = text }, function(err, res)
    if err then
      M.append(err.message or vim.inspect(err), 'error')
    else
      if res.output and res.output ~= '' then M.append(res.output) end
      if res.refresh then require('rtl_integ').refresh() end
    end
    if on_done then on_done(err, res) end
  end)
end

function M.prompt()
  vim.ui.input({ prompt = 'rtl> ' }, function(text)
    if text and text ~= '' then M.run(text) end
  end)
end

return M
