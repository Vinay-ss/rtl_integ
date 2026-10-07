-- Self-test of the GUI against the real backend, on a copy of examples/demo:
--   rtl-integ-gui --selftest
-- which runs (headless, results written to RTL_SMOKE_OUT):
--   RTL_SMOKE_PROJECT=<copy of demo> RTL_SMOKE_OUT=<file> RTL_INTEG_PYTHON=<python>
--   nvim --headless --clean -u <pkg>/gui/nvim/init.lua -c "luafile <pkg>/gui/nvim/selftest.lua"
local out_path = vim.env.RTL_SMOKE_OUT
local results = {}

local function finish(ok, msg)
  table.insert(results, (ok and 'OK ' or 'FAIL ') .. (msg or ''))
  local f = io.open(out_path, 'w')
  if f then
    f:write(table.concat(results, '\n') .. '\n')
    f:close()
  end
  vim.cmd(ok and 'qa!' or 'cquit 1')
end

local function check(cond, msg)
  if not cond then error(msg, 2) end
  table.insert(results, 'pass ' .. msg)
end

local app = require('rtl_integ')
local tree = require('rtl_integ.tree')
local source = require('rtl_integ.source')
local console = require('rtl_integ.console')
local layout = require('rtl_integ.layout')

local function wait(cond, ms, what)
  local ok = vim.wait(ms or 120000, cond, 50)
  if not ok then error('timeout waiting for ' .. what, 2) end
end

local function tree_lines()
  return vim.api.nvim_buf_get_lines(tree.buf, 0, -1, false)
end

local function has_line(lines, pat)
  for _, l in ipairs(lines) do
    if l:find(pat, 1, true) then return true end
  end
  return false
end

local ok, err = pcall(function()
  app.open({ vim.env.RTL_SMOKE_PROJECT })
  wait(function() return #tree.roots > 0 end, 240000, 'tree')
  check(layout.is_open(), 'layout has tree, source and console windows')
  local lines = tree_lines()
  check(has_line(lines, 'top : top'), 'root node shown')
  check(has_line(lines, 'u_c : stage  [S]'), 'subst tag on u_c')
  check(has_line(lines, 'u_d : stage  [G]'), 'guarded tag on u_d')
  check(has_line(lines, 'u_lane1 : stage  [Lx2]'), 'loop tag on u_lane1')
  check(has_line(console.lines(), 'build ok'), 'console reports the build')

  check(tree.goto_path('top.u_c'), 'cursor to top.u_c')
  source.current = nil
  app.open_node(tree.node_at_cursor(), 'module')
  wait(function() return source.current ~= nil end, 30000, 'module source')
  check(source.current.path:match('stage%.sv$') ~= nil, 'module source is the stage source')
  check(vim.api.nvim_win_get_cursor(layout.wins.source)[1] == 2, 'cursor on the module header')

  source.current = nil
  app.open_node(tree.by_path['top.u_c'], 'inst')
  wait(function() return source.current ~= nil end, 30000, 'instantiation')
  check(source.current.path:match('top%.svp$') ~= nil, 'instantiation shown in the template')
  -- u_c spans two template lines: "stage #(.W(...)) u_c" and its connections
  local uc
  for n, l in ipairs(vim.fn.readfile(source.current.path)) do
    if l:find('u_c', 1, true) and l:find('stage', 1, true) then uc = n end
  end
  check(uc ~= nil and source.current.line == uc and source.current.end_line == uc + 1,
    'instantiation span ' .. tostring(uc) .. '-' .. tostring(uc and uc + 1))
  check(not vim.bo[source.current.bufnr].readonly, 'template is editable')

  source.current = nil
  app.toggle_view()
  wait(function() return source.current ~= nil end, 30000, 'integ view')
  check(source.current.view == 'integ', 'toggled to the integrated RTL')
  check(vim.bo[source.current.bufnr].readonly, 'integrated RTL is read-only')
  app.toggle_view()

  tree.set_filter('top\\.u_lane.*')
  lines = tree_lines()
  check(#lines == 3, 'filter keeps the root and two lanes (' .. #lines .. ')')
  tree.set_filter(nil)

  tree.goto_path('top.u_a')
  tree.toggle_mark()
  tree.goto_path('top.u_b')
  tree.toggle_mark()
  local marked = tree.marked_paths()
  check(#marked == 2 and marked[1] == 'top.u_a' and marked[2] == 'top.u_b', 'marks keep their order')

  local done = false
  console.run('find top.u_.', function() done = true end)
  wait(function() return done end, 30000, 'console command')
  check(has_line(console.lines(), 'top.u_a'), 'console command output')

  -- wrap the two marked instances through the UI (prompts answered by stubs)
  local answers = { 'ab_wrap', 'u_ab' }
  vim.ui.input = function(_, cb) cb(table.remove(answers, 1)) end
  vim.ui.select = function(items, _, cb) cb(items[1]) end
  require('rtl_integ.ops').wrap()
  wait(function() return tree.by_path['top.u_ab'] ~= nil end, 120000, 'wrapped tree')
  lines = tree_lines()
  check(has_line(lines, 'u_ab : ab_wrap  [W]'), 'wrapper shown with [W]')
  check(has_line(console.lines(), 'leaf connectivity unchanged'), 'apply verified')
  check(has_line(console.lines(), '+   ab_wrap u_ab'), 'diff previewed in the console')
  check(#tree.marked_paths() == 0, 'marks cleared after apply')

  require('rtl_integ.ops').undo()
  wait(function() return tree.by_path['top.u_a'] ~= nil and tree.by_path['top.u_ab'] == nil end, 120000, 'undo')
  check(has_line(console.lines(), 'undone: wrap u_a, u_b into ab_wrap u_ab'), 'undo reported')

  -- wrap again, then hoist both instances back out; the last hoist dissolves the wrapper
  answers = { 'ab_wrap', 'u_ab' }
  tree.goto_path('top.u_a')
  tree.toggle_mark()
  tree.goto_path('top.u_b')
  tree.toggle_mark()
  require('rtl_integ.ops').wrap()
  wait(function() return tree.by_path['top.u_ab.u_b'] ~= nil end, 120000, 'second wrap')
  require('rtl_integ.ops').hoist(tree.by_path['top.u_ab.u_b'])
  wait(function() return tree.by_path['top.u_b'] ~= nil end, 120000, 'hoist u_b')
  check(has_line(console.lines(), 'applied: hoist u_b out of ab_wrap -- leaf connectivity unchanged'), 'hoist verified')
  require('rtl_integ.ops').hoist(tree.by_path['top.u_ab.u_a'])
  wait(function() return tree.by_path['top.u_a'] ~= nil and tree.by_path['top.u_ab'] == nil end, 120000,
    'hoist u_a with dissolve')
  check(has_line(console.lines(), 'removed:  ab_wrap'), 'empty wrapper dissolved')

  -- edit the template in its buffer (lines shift) and wrap without saving or
  -- rebuilding: the wrap saves (stubbed select), rebuilds first and verifies
  local tbuf
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_get_name(b):match('top%.svp$') then tbuf = b end
  end
  check(tbuf ~= nil, 'template buffer is loaded')
  local text = table.concat(vim.api.nvim_buf_get_lines(tbuf, 0, -1, false), '\n')
  check(not text:find('ab_wrap', 1, true), 'template buffer was reloaded after the operations')
  text = text:gsub('output logic       busy%);', 'output logic       busy,\n   output logic       test_busy);')
  text = text:gsub('%.q%(a2b%), %.busy%(%)%);', '.q(a2b), .busy(test_busy));')
  vim.api.nvim_buf_set_lines(tbuf, 0, -1, false, vim.split(text, '\n', { plain = true }))
  check(vim.bo[tbuf].modified, 'template edited, not saved')
  answers = { 'ab2_wrap', 'u_ab2' }
  tree.clear_marks()
  tree.goto_path('top.u_a')
  tree.toggle_mark()
  tree.goto_path('top.u_b')
  tree.toggle_mark()
  require('rtl_integ.ops').wrap()
  wait(function() return tree.by_path['top.u_ab2.u_a'] ~= nil end, 120000, 'wrap after an unsaved edit')
  check(not vim.bo[tbuf].modified, 'unsaved template saved before the wrap')
  check(has_line(console.lines(), 'tpl/top.svp changed since the last build: rebuilding first'), 'rebuilt first')
  check(has_line(console.lines(), 'applied: wrap u_a, u_b into ab2_wrap u_ab2 -- leaf connectivity unchanged'),
    'wrap after an edit verified')
  wait(function() return has_line(vim.api.nvim_buf_get_lines(tbuf, 0, -1, false), 'ab2_wrap u_ab2') end, 10000,
    'template buffer reload')
  require('rtl_integ.ops').undo()
  wait(function() return tree.by_path['top.u_a'] ~= nil and tree.by_path['top.u_ab2'] == nil end, 120000,
    'undo the wrap after an edit')
  check(has_line(vim.api.nvim_buf_get_lines(tbuf, 0, -1, false), '.busy(test_busy));'), 'undo keeps the edit')

  -- a loop member: the backend asks, the (stubbed) select answers "group"
  answers = { 'lanes', 'u_lanes' }
  tree.clear_marks()
  tree.goto_path('top.u_lane0')
  tree.toggle_mark()
  require('rtl_integ.ops').wrap()
  wait(function() return tree.by_path['top.u_lanes.u_lane1'] ~= nil end, 120000, 'loop group wrap')
  check(has_line(tree_lines(), 'u_lanes : lanes  [W]'), 'loop group wrapped through the question')
end)

if ok then finish(true, 'all checks passed') else finish(false, tostring(err)) end
