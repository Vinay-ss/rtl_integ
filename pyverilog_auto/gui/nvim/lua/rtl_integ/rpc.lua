-- JSON-lines RPC client for the Python backend (python -m pyverilog_auto.gui.server).
local M = {}

local state = {
  proc = nil,
  partial = '',
  next_id = 0,
  pending = {},
  handlers = {},
  stderr = {},
}

local function dispatch(msg)
  if msg.id ~= nil and msg.id ~= vim.NIL and (msg.result ~= nil or msg.error ~= nil) then
    local cb = state.pending[msg.id]
    state.pending[msg.id] = nil
    if cb then
      local err = msg.error
      if err == vim.NIL then err = nil end
      local res = msg.result
      if res == vim.NIL then res = nil end
      cb(err, res)
    end
  elseif msg.method then
    local h = state.handlers[msg.method]
    if h then h(msg.params or {}) end
  end
end

local function on_stdout(_, data)
  if not data then return end
  state.partial = state.partial .. data
  while true do
    local nl = state.partial:find('\n', 1, true)
    if not nl then break end
    local line = state.partial:sub(1, nl - 1):gsub('\r$', '')
    state.partial = state.partial:sub(nl + 1)
    if line ~= '' then
      vim.schedule(function()
        local ok, msg = pcall(vim.json.decode, line)
        if ok and type(msg) == 'table' then
          dispatch(msg)
        else
          local h = state.handlers['log']
          if h then h({ text = 'backend: ' .. line, level = 'warning' }) end
        end
      end)
    end
  end
end

local function on_stderr(_, data)
  if not data or data == '' then return end
  vim.schedule(function()
    for line in data:gmatch('[^\r\n]+') do
      table.insert(state.stderr, line)
      if #state.stderr > 500 then table.remove(state.stderr, 1) end
      local h = state.handlers['stderr']
      if h then h({ text = line }) end
    end
  end)
end

local function find_python()
  if vim.g.rtl_integ_python and vim.g.rtl_integ_python ~= '' then return vim.g.rtl_integ_python end
  if vim.env.RTL_INTEG_PYTHON and vim.env.RTL_INTEG_PYTHON ~= '' then return vim.env.RTL_INTEG_PYTHON end
  for _, name in ipairs({ 'python3', 'python' }) do
    if vim.fn.executable(name) == 1 then return name end
  end
  return 'python'
end

function M.on(method, fn)
  state.handlers[method] = fn
end

function M.running()
  return state.proc ~= nil
end

function M.start(opts)
  opts = opts or {}
  if state.proc then return true end
  local python = opts.python or find_python()
  state.partial = ''
  state.pending = {}
  local ok, proc = pcall(vim.system, { python, '-m', 'pyverilog_auto.gui.server' }, {
    stdin = true,
    stdout = on_stdout,
    stderr = on_stderr,
    cwd = opts.cwd,
    -- PYTHONSAFEPATH: Neovim's working directory must not shadow the installed package
    env = { PYTHONWARNINGS = 'ignore', PYTHONUNBUFFERED = '1', PYTHONIOENCODING = 'utf-8', PYTHONSAFEPATH = '1' },
  }, function(obj)
    vim.schedule(function()
      state.proc = nil
      for id, cb in pairs(state.pending) do
        state.pending[id] = nil
        cb({ code = 'E_EXIT', message = 'backend exited' }, nil)
      end
      local h = state.handlers['exit']
      if h then h({ code = obj.code, signal = obj.signal }) end
    end)
  end)
  if not ok then
    vim.notify('rtl_integ: cannot start ' .. python .. ': ' .. tostring(proc), vim.log.levels.ERROR)
    return false
  end
  state.proc = proc
  return true
end

function M.stop()
  if state.proc then
    pcall(function() state.proc:write(vim.json.encode({ id = 0, method = 'shutdown', params = vim.empty_dict() }) .. '\n') end)
    pcall(function() state.proc:write(nil) end)
    state.proc = nil
  end
end

local function encode_params(params)
  if params == nil or (type(params) == 'table' and next(params) == nil) then
    return vim.empty_dict()
  end
  return params
end

-- Asynchronous request: cb(err, result) runs on the main loop.
function M.request(method, params, cb)
  if not state.proc then
    if cb then cb({ code = 'E_NOT_RUNNING', message = 'backend is not running' }, nil) end
    return
  end
  state.next_id = state.next_id + 1
  local id = state.next_id
  state.pending[id] = cb or function() end
  state.proc:write(vim.json.encode({ id = id, method = method, params = encode_params(params) }) .. '\n')
end

-- Synchronous request (waits on the event loop); returns err, result.
function M.call(method, params, timeout_ms)
  local done, err, res = false, nil, nil
  M.request(method, params, function(e, r)
    done, err, res = true, e, r
  end)
  vim.wait(timeout_ms or 600000, function() return done end, 20)
  if not done then return { code = 'E_TIMEOUT', message = method .. ' timed out' }, nil end
  return err, res
end

function M.stderr_tail(n)
  local out = {}
  for i = math.max(1, #state.stderr - (n or 20) + 1), #state.stderr do
    table.insert(out, state.stderr[i])
  end
  return out
end

return M
