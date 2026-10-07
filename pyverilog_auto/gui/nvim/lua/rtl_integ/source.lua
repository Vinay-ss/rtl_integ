-- Source panel: shows a template (editable) or the generated / integrated RTL
-- (read-only) at a location returned by the backend, with the span highlighted.
local M = {}

local ns = vim.api.nvim_create_namespace('rtl_integ_span')
M.current = nil   -- last shown location

-- Re-read a buffer whose file changed on disk (a rebuild, an applied
-- operation, an undo), unless it has unsaved edits.
function M.reload(bufnr)
  if not vim.api.nvim_buf_is_loaded(bufnr) or vim.bo[bufnr].modified or vim.bo[bufnr].buftype ~= '' then return end
  local locked = not vim.bo[bufnr].modifiable
  vim.bo[bufnr].autoread = true
  if locked then vim.bo[bufnr].modifiable = true end
  pcall(vim.cmd.checktime, bufnr)
  if locked and vim.api.nvim_buf_is_valid(bufnr) then vim.bo[bufnr].modifiable = false end
end

-- Buffers of files under *root*.
function M.project_buffers(root)
  if not root or root == vim.NIL then return {} end
  root = vim.fs.normalize(root):lower():gsub('/$', '') .. '/'
  local out = {}
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.api.nvim_buf_get_name(b)
    if name ~= '' and vim.bo[b].buftype == '' and vim.startswith(vim.fs.normalize(name):lower(), root) then
      table.insert(out, b)
    end
  end
  return out
end

function M.reload_all(root)
  for _, b in ipairs(M.project_buffers(root)) do M.reload(b) end
end

local VERILOG_EXT = { v = true, vh = true, plv = true, pyv = true, vpy = true, vpl = true }
local SV_EXT = { sv = true, svh = true, svp = true, svpy = true, svpl = true }

-- Filetype, and the plugin's Verilog/SystemVerilog syntax (syntax/rtlsv.vim)
-- told which template delimiters and code language a project template uses.
function M.setup_syntax(bufnr, path)
  local app = require('rtl_integ')
  local tpl = app.template_info(path)
  local ext = (path:match('%.([%w_]+)$') or ''):lower()
  local ft = vim.bo[bufnr].filetype
  if ft == '' or (tpl and ft ~= 'systemverilog' and ft ~= 'verilog') then
    if VERILOG_EXT[ext] then
      vim.bo[bufnr].filetype = 'verilog'
    elseif SV_EXT[ext] or tpl then
      vim.bo[bufnr].filetype = 'systemverilog'
    end
    ft = vim.bo[bufnr].filetype
  end
  if not app.config.highlight or (ft ~= 'systemverilog' and ft ~= 'verilog') then return end
  local want = tpl and { syntax = tpl.syntax, lang = tpl.lang } or nil
  if vim.bo[bufnr].syntax == 'rtlsv' and vim.deep_equal(want, vim.b[bufnr].rtl_integ_tpl) then return end
  if want then
    vim.b[bufnr].rtl_integ_tpl = want
  else
    pcall(vim.api.nvim_buf_del_var, bufnr, 'rtl_integ_tpl')
  end
  vim.api.nvim_buf_call(bufnr, function()
    vim.cmd('setlocal syntax=OFF')
    vim.cmd('setlocal syntax=rtlsv')
  end)
end

local function open_file(win, path, readonly)
  local bufnr = vim.fn.bufnr(path)
  if bufnr == -1 then
    bufnr = vim.fn.bufadd(path)
  else
    M.reload(bufnr)
  end
  vim.fn.bufload(bufnr)
  vim.bo[bufnr].buflisted = true
  if readonly then
    vim.bo[bufnr].readonly = true
    vim.bo[bufnr].modifiable = false
  end
  vim.api.nvim_win_set_buf(win, bufnr)
  M.setup_syntax(bufnr, path)
  return bufnr
end

-- loc = { path, line, end_line, readonly, view }
function M.show(loc, opts)
  opts = opts or {}
  local layout = require('rtl_integ.layout')
  local win = layout.source_win()
  local bufnr = open_file(win, loc.path, loc.readonly)
  layout.style_source(win)
  local nlines = vim.api.nvim_buf_line_count(bufnr)
  local line = math.max(1, math.min(loc.line or 1, nlines))
  local last = math.max(line, math.min(loc.end_line or line, nlines))
  vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
  vim.api.nvim_buf_set_extmark(bufnr, ns, line - 1, 0, {
    end_row = last, end_col = 0, hl_group = 'RtlIntegSpan', hl_eol = true,
  })
  vim.api.nvim_win_set_cursor(win, { line, 0 })
  vim.api.nvim_win_call(win, function() vim.cmd('normal! zz') end)
  vim.b[bufnr].rtl_integ_view = loc.view
  M.current = vim.tbl_extend('force', {}, loc, { bufnr = bufnr })
  if opts.focus then vim.api.nvim_set_current_win(win) end
  require('rtl_integ.layout').redraw_bars()
  return bufnr
end

-- Fallback links; the themes (themes.panel_groups) set the real colours.
function M.setup_highlights()
  local set = function(name, link) vim.api.nvim_set_hl(0, name, { default = true, link = link }) end
  set('RtlIntegSpan', 'Visual')
  set('RtlIntegSourceFile', 'WinBar')
  set('RtlIntegSourceView', 'TabLineSel')
  set('RtlIntegSourcePath', 'Comment')
  set('RtlIntegSourceBar', 'WinBar')
end

return M
