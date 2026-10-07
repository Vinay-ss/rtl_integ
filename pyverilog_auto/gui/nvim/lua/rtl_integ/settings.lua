-- GUI settings kept across sessions (view, hierarchy labels, theme, search
-- scope): JSON in $RTL_INTEG_SETTINGS, default <stdpath data>/rtl_integ_settings.json
-- (the launcher's NVIM_APPNAME gives the GUI its own data directory).
local M = {}

M.KEYS = { 'view', 'rtl_view', 'label', 'theme', 'search_scope' }

function M.path()
  local env = vim.env.RTL_INTEG_SETTINGS
  if env and env ~= '' then return env end
  return vim.fs.joinpath(vim.fn.stdpath('data'), 'rtl_integ_settings.json')
end

function M.load()
  local ok, data = pcall(function()
    local f = io.open(M.path(), 'r')
    if not f then return {} end
    local text = f:read('*a')
    f:close()
    return vim.json.decode(text)
  end)
  if not ok or type(data) ~= 'table' then return {} end
  local out = {}
  for _, k in ipairs(M.KEYS) do
    if type(data[k]) == 'string' then out[k] = data[k] end
  end
  return out
end

function M.save(values)
  pcall(function()
    local path = M.path()
    vim.fn.mkdir(vim.fs.dirname(path), 'p')
    local out = {}
    for _, k in ipairs(M.KEYS) do out[k] = values[k] end
    local f = assert(io.open(path, 'w'))
    f:write(vim.json.encode(out))
    f:close()
  end)
end

return M
