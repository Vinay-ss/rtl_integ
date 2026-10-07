-- rtl_integ: hierarchy browser and structural editor for template-generated RTL.
--
--   :RtlIntegOpen [PROJECT_DIR | rtl_integ_project.toml | -f FILELIST [TOP]]
--
-- Toolbar at the top (view, hierarchy labels, theme, build / undo / help).
-- Left: instance tree with a search box below it.  Right: the module source
-- (template by default; Generated = prepro output, Integrated = after AUTO
-- expansion).  Bottom right: console.
local M = {}

local rpc = require('rtl_integ.rpc')
local console = require('rtl_integ.console')
local tree = require('rtl_integ.tree')
local layout = require('rtl_integ.layout')
local source = require('rtl_integ.source')
local settings = require('rtl_integ.settings')

M.config = {
  python = nil,          -- backend interpreter (default: $RTL_INTEG_PYTHON, python3, python)
  tree_width = 42,
  console_height = 12,
  build_on_save = false, -- rebuild when a template of the project is written
  view = 'template',     -- initial right-panel view: template | gen | integ
  label = 'module',      -- hierarchy labels: name | module (inst (module)) | file (inst : module : file)
  theme = 'rtl-tokyonight', -- colorscheme the launcher starts with (any colorscheme name)
  tree_border = 'thick', -- border of the hierarchy column: thick | thin
  highlight = true,      -- Verilog/SystemVerilog + template highlighting (syntax/rtlsv.vim)
  search_scope = 'both', -- search box: both | inst | module
}

M.state = {
  view = 'template',
  rtl_view = 'integ',    -- the RTL view `t` toggles to
  label = 'module',
  search_scope = 'both',
  theme = nil,
  summary = nil,
  last = nil,            -- { path = ..., what = ... } last opened node
  busy = false,
}

local VIEWS = { template = 'Template', gen = 'Generated', integ = 'Integrated' }
local LABELS = { name = true, module = true, file = true }
local SCOPES = { both = true, inst = true, module = true }

local function save_settings()
  settings.save(M.state)
end

local function log_handler(p)
  console.append(p.text, p.level)
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend('force', M.config, opts or {})
  local saved = settings.load()
  local st = M.state
  st.view = VIEWS[saved.view] and saved.view or M.config.view
  st.rtl_view = (saved.rtl_view == 'gen' or saved.rtl_view == 'integ') and saved.rtl_view
    or (st.view ~= 'template' and st.view or 'integ')
  st.label = LABELS[saved.label] and saved.label or M.config.label
  st.search_scope = SCOPES[saved.search_scope] and saved.search_scope or M.config.search_scope
  st.theme = saved.theme or M.config.theme
  tree.setup_highlights()
  source.setup_highlights()
  require('rtl_integ.themes').setup()
  rpc.on('log', log_handler)
  rpc.on('built', function(summary) M.state.summary = summary end)
  rpc.on('exit', function(p)
    console.append('backend exited (code ' .. tostring(p.code) .. ')', 'error')
    for _, line in ipairs(rpc.stderr_tail(15)) do console.append('  ' .. line, 'error') end
  end)
end

local function ensure_backend(cwd)
  if rpc.running() then return true end
  return rpc.start({ python = M.config.python, cwd = cwd })
end

local function report_error(prefix, err)
  console.append(prefix .. ': ' .. (err.message or vim.inspect(err)), 'error')
  if err.trace then console.append(err.trace, 'error') end
end

local function set_quickfix()
  rpc.request('diagnostics', nil, function(err, diags)
    if err or not diags then return end
    local items = {}
    for _, d in ipairs(diags) do
      if d.severity == 'error' or d.severity == 'warning' then
        table.insert(items, {
          filename = d.file ~= vim.NIL and d.file or nil,
          lnum = (d.line ~= vim.NIL and d.line) or 1,
          text = d.message,
          type = d.severity == 'error' and 'E' or 'W',
        })
      end
    end
    vim.fn.setqflist({}, 'r', { title = 'rtl_integ', items = items })
  end)
end

-- Reload the tree from the backend (after a build or an operation).
function M.refresh(on_done)
  rpc.request('tree', nil, function(err, data)
    if err then
      tree.set_data({ roots = {} })
      report_error('tree', err)
    else
      tree.set_data(data)
    end
    set_quickfix()
    if on_done then on_done(err) end
  end)
end

-- args: list of strings from :RtlIntegOpen
function M.open(args)
  args = args or {}
  local params = {}
  local cwd = vim.fn.getcwd()
  if args[1] == '-f' then
    params.filelist = vim.fn.fnamemodify(args[2] or '', ':p')
    params.top = args[3]
  elseif args[1] and args[1] ~= '' then
    params.project = vim.fn.fnamemodify(args[1], ':p')
  end
  local tbuf = tree.ensure_buf()
  local cbuf = console.ensure_buf()
  local sbuf = require('rtl_integ.search').ensure_buf()
  layout.open(tbuf, cbuf, sbuf, { tree_width = M.config.tree_width, console_height = M.config.console_height })
  tree.set_data({ roots = {} })
  if not ensure_backend(cwd) then return end
  console.append('opening ' .. (params.project or params.filelist or cwd) .. ' ...')
  M.state.busy = true
  rpc.request('open', params, function(err, summary)
    M.state.busy = false
    if err then
      report_error('open', err)
      return
    end
    M.state.summary = summary
    require('rtl_integ.toolbar').redraw()
    console.append(string.format('build %s: %d error(s), %d warning(s)%s', summary.ok and 'ok' or 'FAILED',
      summary.errors, summary.warnings, summary.view_only and ' (view-only)' or ''), summary.ok and 'info' or 'error')
    M.refresh(function()
      M.fit_tree()
      if vim.api.nvim_win_is_valid(layout.wins.tree or -1) then vim.api.nvim_set_current_win(layout.wins.tree) end
    end)
  end)
end

function M.build(force)
  if not rpc.running() then
    console.append('no project open (:RtlIntegOpen)', 'error')
    return
  end
  console.append('building ...')
  M.state.busy = true
  rpc.request('build', { force = force and true or false }, function(err, summary)
    M.state.busy = false
    if err then return report_error('build', err) end
    M.state.summary = summary
    source.reload_all(summary.root)
    console.append(string.format('build %s: %d error(s), %d warning(s)', summary.ok and 'ok' or 'FAILED',
      summary.errors, summary.warnings), summary.ok and 'info' or 'error')
    local keep = tree.node_at_cursor()
    M.refresh(function()
      if keep then tree.goto_path(keep.path) end
    end)
  end)
end

-- Show a node's module source (what = 'module') or instantiation site ('inst').
function M.open_node(node, what, view)
  if not node then return end
  what = what or 'module'
  view = view or M.state.view
  rpc.request('locate', { path = node.path, what = what, view = view }, function(err, loc)
    if err then return report_error('locate', err) end
    source.show(loc)
    M.state.last = { path = node.path, what = what }
    if loc.fallback then
      console.append(string.format('%s: no %s view, showing %s', node.path, view, loc.view), 'warning')
    end
  end)
end

-- Right-panel view: template | gen (prepro output) | integ (AUTO-expanded).
function M.set_view(view)
  if not VIEWS[view] then
    console.append('unknown view ' .. tostring(view) .. ' (template, gen or integ)', 'error')
    return
  end
  M.state.view = view
  if view ~= 'template' then M.state.rtl_view = view end
  save_settings()
  console.append('view: ' .. view)
  local last = M.state.last
  if last and tree.by_path[last.path] then
    M.open_node(tree.by_path[last.path], last.what, view)
  end
  if M.state.label == 'file' then tree.render() end
  require('rtl_integ.toolbar').redraw()
end

-- Toggle between the template and the last RTL view (or go to *view*).
function M.toggle_view(view)
  if view then return M.set_view(view) end
  M.set_view(M.state.view == 'template' and M.state.rtl_view or 'template')
end

-- Hierarchy labels: name | module | file.
function M.set_label(mode)
  if not LABELS[mode] then
    console.append('unknown label mode ' .. tostring(mode) .. ' (name, module or file)', 'error')
    return
  end
  M.state.label = mode
  save_settings()
  tree.render()
  M.fit_tree()
  require('rtl_integ.toolbar').redraw()
end

-- File paths need room: with file labels the hierarchy is as wide as its
-- lines (up to half the screen), otherwise config.tree_width.
function M.fit_tree()
  local win = layout.wins.tree
  if not (win and vim.api.nvim_win_is_valid(win)) then return end
  local width = M.config.tree_width
  if M.state.label == 'file' then
    for _, line in ipairs(vim.api.nvim_buf_get_lines(tree.buf, 0, -1, false)) do
      width = math.max(width, vim.fn.strdisplaywidth(line) + 1)
    end
    width = math.min(width, math.floor(vim.o.columns / 2))
  end
  vim.api.nvim_win_set_width(win, width)
end

function M.cycle_label()
  local next_mode = { name = 'module', module = 'file', file = 'name' }
  M.set_label(next_mode[M.state.label] or 'module')
end

function M.set_search_scope(scope)
  if not SCOPES[scope] then return end
  M.state.search_scope = scope
  save_settings()
  require('rtl_integ.search').apply()
end

-- Any colorscheme name (rtl-* themes or others); kept for the next start.
function M.set_theme(name)
  local ok, err = pcall(vim.cmd.colorscheme, name)
  if not ok then
    console.append('theme ' .. tostring(name) .. ': ' .. tostring(err), 'error')
    return false
  end
  M.state.theme = name
  save_settings()
  require('rtl_integ.toolbar').redraw()
  return true
end

function M.pick_theme()
  require('rtl_integ.toolbar').theme_menu()
end

function M.help()
  console.append(table.concat(tree.help_lines(), '\n'))
end

-- Template delimiters and code language of a project template, or nil.
function M.template_info(path)
  local s = M.state.summary
  if not s or type(s.templates) ~= 'table' or not path then return nil end
  local want = vim.fs.normalize(path):lower()
  for _, t in ipairs(s.templates) do
    if vim.fs.normalize(t.src):lower() == want then
      return { syntax = t.syntax, lang = t.lang }
    end
  end
  return nil
end

function M.info(node)
  if not node then return end
  console.run('info ' .. node.path)
end

-- Structural operations live in ops.lua (loaded lazily).
function M.wrap() require('rtl_integ.ops').wrap() end
function M.hoist(node) require('rtl_integ.ops').hoist(node) end
function M.unroll(node) require('rtl_integ.ops').unroll(node) end
function M.undo() require('rtl_integ.ops').undo() end

function M.close()
  rpc.stop()
  layout.close()
end

-- A file of the project was written: rebuild (opt-in) or say the build is stale.
function M.on_write(path)
  if not rpc.running() or not M.state.summary or not M.state.summary.root then return end
  local root = vim.fs.normalize(M.state.summary.root):lower()
  local file = vim.fs.normalize(path):lower()
  if not vim.startswith(file, root) or file:find('/build/', #root, true) then return end
  if M.config.build_on_save then
    M.build(false)
  else
    console.append(vim.fn.fnamemodify(path, ':t') .. ' changed: press R in the tree to rebuild', 'warning')
  end
end

return M
