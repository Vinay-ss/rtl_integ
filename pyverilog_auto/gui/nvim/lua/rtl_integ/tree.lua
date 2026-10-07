-- Hierarchy tree panel.
local M = {}

local ns = vim.api.nvim_create_namespace('rtl_integ_tree')

M.buf = nil
M.roots = {}
M.expanded = {}      -- path -> true
M.marked = {}        -- path -> order number
M.mark_seq = 0
M.filter = nil       -- compiled vim.regex or nil
M.filter_text = nil
M.line_nodes = {}    -- line (1-based) -> node
M.by_path = {}

local HELP = {
  '<CR>  open module source     i  instantiation site',
  'o     expand / collapse      t  template <-> RTL view',
  'm     mark / unmark          M  clear marks',
  'W     wrap marked            H  hoist out of wrapper',
  'U     unroll template loop   u  undo last operation',
  'R     rebuild                /  filter (regex from top)',
  'K     node info              ?  this help',
}

local function index(nodes, parent)
  for _, n in ipairs(nodes) do
    n.parent = parent
    M.by_path[n.path] = n
    index(n.children or {}, n)
  end
end

function M.ensure_buf()
  if M.buf and vim.api.nvim_buf_is_valid(M.buf) then return M.buf end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(buf, 'rtlinteg://hierarchy')
  vim.bo[buf].buftype = 'nofile'
  vim.bo[buf].bufhidden = 'hide'
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = 'rtlhier'
  vim.bo[buf].modifiable = false
  M.buf = buf
  M.keymaps(buf)
  return buf
end

function M.set_data(data)
  M.roots = (data and data.roots) or {}
  M.by_path = {}
  index(M.roots, nil)
  for _, r in ipairs(M.roots) do
    if M.expanded[r.path] == nil then M.expanded[r.path] = true end
  end
  for p in pairs(M.marked) do
    if not M.by_path[p] then M.marked[p] = nil end
  end
  M.render()
end

local function matches(node)
  if not M.filter then return true end
  return M.filter:match_str(node.path) ~= nil
end

-- A node is shown when it matches or has a matching descendant (filter mode).
local function visible(node)
  if not M.filter then return true end
  if matches(node) then return true end
  for _, c in ipairs(node.children or {}) do
    if visible(c) then return true end
  end
  return false
end

local function node_line(node, depth)
  local has_kids = node.children and #node.children > 0
  local open = M.expanded[node.path] or (M.filter ~= nil)
  local icon = has_kids and (open and '▾ ' or '▸ ') or '  '
  local mark = M.marked[node.path] and '*' or ' '
  local text = string.rep('  ', depth) .. mark .. icon .. node.name .. ' : ' .. node.module
  local tags = {}
  if node.wrapper then table.insert(tags, 'W') end
  if node.tag and node.tag ~= '' then table.insert(tags, node.tag) end
  if node.blackbox then table.insert(tags, 'BB') end
  if node.iface then table.insert(tags, 'IF') end
  local tagtxt = (#tags > 0) and ('  [' .. table.concat(tags, ',') .. ']') or ''
  return text .. tagtxt, #string.rep('  ', depth) + 1 + #icon, #text, tagtxt
end

function M.render()
  local buf = M.ensure_buf()
  local lines, hls = {}, {}
  M.line_nodes = {}
  local function walk(nodes, depth)
    for _, n in ipairs(nodes) do
      if visible(n) then
        local line, name_col, text_end, tagtxt = node_line(n, depth)
        table.insert(lines, line)
        M.line_nodes[#lines] = n
        table.insert(hls, { #lines, name_col, name_col + #n.name, M.marked[n.path] and 'RtlIntegMarked' or 'RtlIntegName' })
        table.insert(hls, { #lines, name_col + #n.name + 3, text_end, 'RtlIntegModule' })
        if tagtxt ~= '' then
          local group = (n.class == 'CODE') and 'RtlIntegTagCode' or 'RtlIntegTag'
          table.insert(hls, { #lines, text_end, text_end + #tagtxt, group })
        end
        if M.filter and matches(n) then
          table.insert(hls, { #lines, name_col, name_col + #n.name, 'RtlIntegMatch' })
        end
        if (M.expanded[n.path] or M.filter) and n.children then walk(n.children, depth + 1) end
      end
    end
  end
  walk(M.roots, 0)
  if #lines == 0 then lines = { M.filter and '(no instance matches the filter)' or '(no design loaded)' } end
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
  for _, h in ipairs(hls) do
    pcall(vim.api.nvim_buf_set_extmark, buf, ns, h[1] - 1, h[2], { end_col = h[3], hl_group = h[4] })
  end
end

function M.node_at_cursor()
  local win = vim.fn.bufwinid(M.ensure_buf())
  if win == -1 then return nil end
  local row = vim.api.nvim_win_get_cursor(win)[1]
  return M.line_nodes[row]
end

function M.goto_path(path)
  local n = M.by_path[path]
  if not n then return false end
  local p = n.parent
  while p do
    M.expanded[p.path] = true
    p = p.parent
  end
  M.render()
  for line, node in pairs(M.line_nodes) do
    if node.path == path then
      local win = vim.fn.bufwinid(M.buf)
      if win ~= -1 then vim.api.nvim_win_set_cursor(win, { line, 0 }) end
      return true
    end
  end
  return false
end

function M.toggle(node)
  node = node or M.node_at_cursor()
  if not node or not node.children or #node.children == 0 then return end
  M.expanded[node.path] = not M.expanded[node.path]
  M.render()
  M.goto_path(node.path)
end

function M.toggle_mark(node)
  node = node or M.node_at_cursor()
  if not node then return end
  if M.marked[node.path] then
    M.marked[node.path] = nil
  else
    M.mark_seq = M.mark_seq + 1
    M.marked[node.path] = M.mark_seq
  end
  M.render()
  M.goto_path(node.path)
end

function M.clear_marks()
  M.marked = {}
  M.render()
end

-- Marked paths in marking order.
function M.marked_paths()
  local items = {}
  for p, seq in pairs(M.marked) do table.insert(items, { p, seq }) end
  table.sort(items, function(a, b) return a[2] < b[2] end)
  local out = {}
  for _, it in ipairs(items) do table.insert(out, it[1]) end
  return out
end

function M.set_filter(text)
  if text == nil or text == '' then
    M.filter, M.filter_text = nil, nil
  else
    -- full match from the top: anchor both ends (no implicit tail anchoring)
    local ok, rx = pcall(vim.regex, '\\v^(' .. text .. ')$')
    if not ok then
      vim.notify('rtl_integ: bad filter regex: ' .. tostring(rx), vim.log.levels.ERROR)
      return
    end
    M.filter, M.filter_text = rx, text
  end
  M.render()
end

function M.help_lines()
  return HELP
end

function M.keymaps(buf)
  local app = function() return require('rtl_integ') end
  local map = function(lhs, fn, desc)
    vim.keymap.set('n', lhs, fn, { buffer = buf, nowait = true, silent = true, desc = 'rtl_integ: ' .. desc })
  end
  map('<CR>', function() app().open_node(M.node_at_cursor(), 'module') end, 'open module source')
  map('<2-LeftMouse>', function() app().open_node(M.node_at_cursor(), 'module') end, 'open module source')
  map('i', function() app().open_node(M.node_at_cursor(), 'inst') end, 'open instantiation site')
  map('o', function() M.toggle() end, 'expand / collapse')
  map('za', function() M.toggle() end, 'expand / collapse')
  map('t', function() app().toggle_view() end, 'template <-> RTL view')
  map('m', function() M.toggle_mark() end, 'mark')
  map('M', function() M.clear_marks() end, 'clear marks')
  map('W', function() app().wrap() end, 'wrap marked instances')
  map('H', function() app().hoist(M.node_at_cursor()) end, 'hoist out of wrapper')
  map('U', function() app().unroll(M.node_at_cursor()) end, 'unroll template loop')
  map('u', function() app().undo() end, 'undo last operation')
  map('R', function() app().build(false) end, 'rebuild')
  map('/', function()
    vim.ui.input({ prompt = 'filter (regex, full match from top): ', default = M.filter_text or '' }, function(text)
      if text ~= nil then M.set_filter(text) end
    end)
  end, 'filter')
  map('K', function() app().info(M.node_at_cursor()) end, 'node info')
  map('?', function() require('rtl_integ.console').append(table.concat(HELP, '\n')) end, 'help')
end

function M.setup_highlights()
  local set = function(name, link) vim.api.nvim_set_hl(0, name, { default = true, link = link }) end
  set('RtlIntegName', 'Identifier')
  set('RtlIntegModule', 'Type')
  set('RtlIntegTag', 'Special')
  set('RtlIntegTagCode', 'WarningMsg')
  set('RtlIntegMarked', 'Search')
  set('RtlIntegMatch', 'IncSearch')
end

return M
