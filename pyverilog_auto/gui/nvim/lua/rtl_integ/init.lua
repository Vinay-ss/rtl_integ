-- rtl_integ: hierarchy browser and structural editor for template-generated RTL.
--
--   :RtlIntegOpen [PROJECT_DIR | rtl_integ_project.toml | -f FILELIST [TOP]]
--
-- Left: instance tree.  Right: the module source (template by default, `t`
-- toggles the generated RTL).  Bottom right: console.
local M = {}

local rpc = require('rtl_integ.rpc')
local console = require('rtl_integ.console')
local tree = require('rtl_integ.tree')
local layout = require('rtl_integ.layout')
local source = require('rtl_integ.source')

M.config = {
  python = nil,          -- backend interpreter (default: $RTL_INTEG_PYTHON, python3, python)
  tree_width = 42,
  console_height = 12,
  build_on_save = false, -- rebuild when a template of the project is written
  view = 'template',     -- initial right-panel view: template | integ | gen
}

M.state = {
  view = 'template',
  summary = nil,
  last = nil,            -- { path = ..., what = ... } last opened node
  busy = false,
}

local function log_handler(p)
  console.append(p.text, p.level)
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend('force', M.config, opts or {})
  M.state.view = M.config.view
  tree.setup_highlights()
  source.setup_highlights()
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
  layout.open(tbuf, cbuf, { tree_width = M.config.tree_width, console_height = M.config.console_height })
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
    console.append(string.format('build %s: %d error(s), %d warning(s)%s', summary.ok and 'ok' or 'FAILED',
      summary.errors, summary.warnings, summary.view_only and ' (view-only)' or ''), summary.ok and 'info' or 'error')
    M.refresh(function()
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

function M.toggle_view(view)
  if view then
    M.state.view = view
  else
    M.state.view = (M.state.view == 'template') and 'integ' or 'template'
  end
  console.append('view: ' .. M.state.view)
  local last = M.state.last
  if last and tree.by_path[last.path] then
    M.open_node(tree.by_path[last.path], last.what, M.state.view)
  end
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
