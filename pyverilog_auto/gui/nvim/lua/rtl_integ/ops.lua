-- Structural operations (wrap / hoist / unroll / undo): plan on the backend,
-- preview the template diff in the console, apply on confirmation.
local M = {}

local rpc = require('rtl_integ.rpc')
local console = require('rtl_integ.console')
local tree = require('rtl_integ.tree')
local source = require('rtl_integ.source')

local function report(prefix, err)
  console.append(prefix .. ': ' .. (err.message or vim.inspect(err)), 'error')
end

local function project_root()
  local summary = require('rtl_integ').state.summary
  return summary and summary.root
end

-- Operations read and write the files on disk, so unsaved edits to project
-- files are saved first (or the operation is not started).
local function with_saved(fn)
  local unsaved = {}
  for _, b in ipairs(source.project_buffers(project_root())) do
    if vim.bo[b].modified then table.insert(unsaved, b) end
  end
  if #unsaved == 0 then return fn() end
  local names = {}
  for _, b in ipairs(unsaved) do table.insert(names, vim.fn.fnamemodify(vim.api.nvim_buf_get_name(b), ':t')) end
  vim.ui.select({ 'Save and continue', 'Cancel' }, { prompt = 'Unsaved changes in ' .. table.concat(names, ', ') .. ':' },
    function(choice)
      if choice ~= 'Save and continue' then
        console.append('cancelled: save or discard the changes to ' .. table.concat(names, ', ') .. ' first', 'warning')
        return
      end
      for _, b in ipairs(unsaved) do
        vim.api.nvim_buf_call(b, function() vim.cmd('silent write') end)
      end
      fn()
    end)
end

-- Show a plan and ask for confirmation; on yes apply it and refresh.
function M.preview_and_apply(plan)
  console.append('')
  console.append(plan.summary or ('plan ' .. tostring(plan.id)), 'cmd')
  for _, d in ipairs(plan.diagnostics or {}) do
    console.append('  ' .. d.severity .. ': ' .. d.message, d.severity)
  end
  if plan.diff and plan.diff ~= '' then console.append(plan.diff, 'diff') end
  if not plan.ok then
    console.append('operation refused', 'error')
    rpc.request('discard', { plan_id = plan.id }, function() end)
    return
  end
  vim.ui.select({ 'Apply', 'Cancel' }, { prompt = 'Apply this change?' }, function(choice)
    if choice ~= 'Apply' then
      rpc.request('discard', { plan_id = plan.id }, function() end)
      console.append('cancelled')
      return
    end
    console.append('applying ...')
    rpc.request('apply', { plan_id = plan.id }, function(err, res)
      if err then return report('apply', err) end
      source.reload_all(project_root())
      console.append(res.message or 'applied', res.verified == false and 'warning' or 'info')
      for _, d in ipairs(res.diagnostics or {}) do
        console.append('  ' .. d.severity .. ': ' .. d.message, d.severity)
      end
      tree.clear_marks()
      require('rtl_integ').refresh(function()
        if res.focus then tree.goto_path(res.focus) end
        -- an unroll/freeze planned on the way to another operation: plan that one now
        if plan['then'] and res.build_ok ~= false then
          M.plan(plan['then'].method, plan['then'].params)
        end
      end)
    end)
  end)
end

local function ask_choices(plan, method, params)
  -- The backend may need a decision (e.g. how to handle a template loop).
  local q = plan.question
  vim.ui.select(q.options, { prompt = q.prompt }, function(choice)
    if not choice then
      console.append('cancelled')
      return
    end
    params.choices = params.choices or {}
    params.choices[q.key] = choice
    M.plan(method, params)
  end)
end

function M.plan(method, params)
  rpc.request(method, params, function(err, plan)
    if err then return report(method, err) end
    local function go()
      if plan.question then return ask_choices(plan, method, params) end
      M.preview_and_apply(plan)
    end
    if plan.rebuilt then
      -- the backend rebuilt with edited project files before planning
      source.reload_all(project_root())
      require('rtl_integ').refresh(go)
    else
      go()
    end
  end)
end

function M.wrap()
  local paths = tree.marked_paths()
  if #paths == 0 then
    local n = tree.node_at_cursor()
    if n then paths = { n.path } end
  end
  if #paths == 0 then
    console.append('wrap: mark instances with m first', 'warning')
    return
  end
  local first = tree.by_path[paths[1]]
  local parent = first and first.parent
  local default = (parent and parent.module or 'top') .. '_wrap'
  with_saved(function()
    vim.ui.input({ prompt = 'wrapper module name: ', default = default }, function(module)
      if not module or module == '' then return end
      vim.ui.input({ prompt = 'wrapper instance name: ', default = 'u_' .. module }, function(inst)
        if not inst or inst == '' then return end
        M.plan('plan_wrap', { paths = paths, module = module, instance = inst })
      end)
    end)
  end)
end

function M.hoist(node)
  if not node then return end
  with_saved(function() M.plan('plan_hoist', { path = node.path }) end)
end

function M.unroll(node)
  if not node then return end
  with_saved(function() M.plan('plan_unroll', { path = node.path }) end)
end

function M.undo()
  with_saved(M.undo_saved)
end

function M.undo_saved()
  rpc.request('journal', nil, function(err, entries)
    if err then return report('undo', err) end
    if not entries or #entries == 0 then
      console.append('nothing to undo')
      return
    end
    local last = entries[#entries]
    vim.ui.select({ 'Undo', 'Cancel' }, { prompt = 'Undo "' .. last.title .. '"?' }, function(choice)
      if choice ~= 'Undo' then return end
      rpc.request('undo', nil, function(e, res)
        if e then return report('undo', e) end
        source.reload_all(project_root())
        console.append(res.message or 'undone')
        require('rtl_integ').refresh()
      end)
    end)
  end)
end

return M
