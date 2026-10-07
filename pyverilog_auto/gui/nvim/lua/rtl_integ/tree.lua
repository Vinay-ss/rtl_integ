-- Hierarchy tree panel.
local M = {}

local ns = vim.api.nvim_create_namespace('rtl_integ_tree')

M.buf = nil
M.roots = {}
M.expanded = {}      -- path -> true
M.folded = {}        -- path -> true: collapsed by hand while the tree is filtered
M.marked = {}        -- path -> order number
M.mark_seq = 0
M.filter = nil       -- compiled vim.regex or nil (whole path, from the top)
M.filter_text = nil
M.search = nil       -- { text, scope, test }: instance / module name search
M.match_count = 0
M.line_nodes = {}    -- line (1-based) -> node
M.by_path = {}

local HELP = {
  'click open module source     double-click  expand / collapse',
  '<CR>  open module source     i  instantiation site',
  'o     expand / collapse      t  template <-> RTL view',
  'L     labels: inst | inst (module) | inst : module : file',
  'f     search instance / module names (in the box below)',
  '/     filter (regex on the whole path, from the top)',
  'm     mark / unmark          M  clear marks',
  'W     wrap marked            H  hoist out of wrapper',
  'U     unroll template loop   u  undo last operation',
  'R     rebuild                T  theme',
  'K     node info              ?  this help',
}

local function app() return require('rtl_integ') end

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

-- -- filtering ------------------------------------------------------------------

local function filtered()
  return M.filter ~= nil or M.search ~= nil
end

-- Name test: substring, or a glob with * and ?; case-insensitive unless the
-- text has a capital letter.
local function compile_search(text)
  local sensitive = text:find('%u') ~= nil
  local needle = sensitive and text or text:lower()
  if needle:find('[%*%?]') then
    local pat = needle:gsub('[%^%$%(%)%%%.%[%]%+%-]', '%%%0'):gsub('%*', '.*'):gsub('%?', '.')
    return function(s)
      return (sensitive and s or s:lower()):find(pat) ~= nil
    end
  end
  return function(s)
    return (sensitive and s or s:lower()):find(needle, 1, true) ~= nil
  end
end

-- Whether the search hits the instance name and the module name.
local function search_hits(node)
  local s = M.search
  if not s then return false, false end
  local hit_name = s.scope ~= 'module' and s.test(node.name)
  local hit_mod = s.scope ~= 'inst' and s.test(node.module or '')
  return hit_name, hit_mod
end

local function matches(node)
  if M.filter and M.filter:match_str(node.path) == nil then return false end
  if M.search then
    local hit_name, hit_mod = search_hits(node)
    if not (hit_name or hit_mod) then return false end
  end
  return true
end

-- Paths shown while filtering: matches and their ancestors.
local function visible_set()
  local vis = {}
  local function walk(nodes)
    local any = false
    for _, n in ipairs(nodes) do
      local below = walk(n.children or {})
      if below or matches(n) then
        vis[n.path] = true
        any = true
      end
    end
    return any
  end
  walk(M.roots)
  return vis
end

local function is_open(node)
  if filtered() then return not M.folded[node.path] end
  return M.expanded[node.path]
end

-- -- rendering ------------------------------------------------------------------

local VIEW_FALLBACK = { 'integ', 'gen', 'template' }

local function node_file(node, view)
  local files = node.files
  if type(files) ~= 'table' then return nil end
  if files[view] then return files[view] end
  for _, v in ipairs(VIEW_FALLBACK) do
    if files[v] then return files[v] end
  end
  return nil
end

-- Text of a tree line and its highlights { start_col, end_col, group }.
local function node_line(node, depth, hit)
  local st = app().state
  local text, hls = '', {}
  local function add(s, group)
    if group and #s > 0 then table.insert(hls, { #text, #text + #s, group }) end
    text = text .. s
  end
  local hit_name, hit_mod = false, false
  if hit then
    hit_name, hit_mod = search_hits(node)
    if not M.search then hit_name = true end
    if st.label == 'name' and hit_mod then hit_name = true end
  end
  local has_kids = node.children and #node.children > 0
  add(string.rep('  ', depth))
  add(M.marked[node.path] and '*' or ' ', 'RtlIntegMarkSign')
  add(has_kids and (is_open(node) and '▾ ' or '▸ ') or '  ', 'RtlIntegIcon')
  local name_hl = (hit_name and 'RtlIntegMatch') or (M.marked[node.path] and 'RtlIntegMarked') or 'RtlIntegName'
  add(node.name, name_hl)
  local mod_hl = hit_mod and 'RtlIntegMatch' or 'RtlIntegModule'
  if st.label == 'module' then
    add(' (', 'RtlIntegPunct')
    add(node.module, mod_hl)
    add(')', 'RtlIntegPunct')
  elseif st.label == 'file' then
    add(' : ', 'RtlIntegPunct')
    add(node.module, mod_hl)
    local file = node_file(node, st.view)
    if file then
      add(' : ', 'RtlIntegPunct')
      add(file, 'RtlIntegFile')
    end
  end
  local tags = {}
  if node.wrapper then table.insert(tags, 'W') end
  if node.tag and node.tag ~= '' then table.insert(tags, node.tag) end
  if node.blackbox then table.insert(tags, 'BB') end
  if node.iface then table.insert(tags, 'IF') end
  if #tags > 0 then
    add('  [' .. table.concat(tags, ',') .. ']', node.class == 'CODE' and 'RtlIntegTagCode' or 'RtlIntegTag')
  end
  return text, hls
end

function M.render()
  local buf = M.ensure_buf()
  local lines, hls = {}, {}
  M.line_nodes = {}
  M.match_count = 0
  local vis = filtered() and visible_set() or nil
  local function walk(nodes, depth)
    for _, n in ipairs(nodes) do
      if not vis or vis[n.path] then
        local hit = vis ~= nil and matches(n)
        if hit then M.match_count = M.match_count + 1 end
        local line, line_hls = node_line(n, depth, hit)
        table.insert(lines, line)
        M.line_nodes[#lines] = n
        for _, h in ipairs(line_hls) do table.insert(hls, { #lines, h[1], h[2], h[3] }) end
        if is_open(n) and n.children then walk(n.children, depth + 1) end
      end
    end
  end
  walk(M.roots, 0)
  if #lines == 0 then lines = { filtered() and '  (no instance matches)' or '  (no design loaded)' } end
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
  for _, h in ipairs(hls) do
    pcall(vim.api.nvim_buf_set_extmark, buf, ns, h[1] - 1, h[2], { end_col = h[3], hl_group = h[4] })
  end
  require('rtl_integ.layout').redraw_bars()
end

-- -- navigation -----------------------------------------------------------------

function M.node_at_cursor()
  local win = vim.fn.bufwinid(M.ensure_buf())
  if win == -1 then return nil end
  local row = vim.api.nvim_win_get_cursor(win)[1]
  return M.line_nodes[row]
end

local function cursor_to(path)
  for line, node in pairs(M.line_nodes) do
    if node.path == path then
      local win = vim.fn.bufwinid(M.buf)
      if win ~= -1 then vim.api.nvim_win_set_cursor(win, { line, 0 }) end
      return true
    end
  end
  return false
end

function M.goto_path(path)
  local n = M.by_path[path]
  if not n then return false end
  local p = n.parent
  while p do
    M.expanded[p.path] = true
    M.folded[p.path] = nil
    p = p.parent
  end
  M.render()
  return cursor_to(path)
end

function M.goto_first_match()
  for line = 1, #M.line_nodes do
    local n = M.line_nodes[line]
    if n and matches(n) then return M.goto_path(n.path) end
  end
  return false
end

function M.toggle(node)
  node = node or M.node_at_cursor()
  if not node or not node.children or #node.children == 0 then return end
  if filtered() then
    M.folded[node.path] = not M.folded[node.path] or nil
  else
    M.expanded[node.path] = not M.expanded[node.path]
  end
  M.render()
  cursor_to(node.path)
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
  M.folded = {}
  M.render()
end

-- Instance / module name search (the search box); scope: both | inst | module.
function M.set_search(text, scope)
  text = vim.trim(text or '')
  if text == '' then
    M.search = nil
  else
    M.search = { text = text, scope = scope or 'both', test = compile_search(text) }
  end
  M.folded = {}
  M.render()
end

function M.help_lines()
  return HELP
end

-- Node under the mouse; nil for a click on the window bar or past the last
-- line (no mouse position in the tree, e.g. headless: the node at the cursor).
local function node_at_mouse()
  local pos = vim.fn.getmousepos()
  if pos.winid ~= vim.fn.bufwinid(M.buf) then return M.node_at_cursor() end
  if pos.line == 0 then return nil end
  return M.line_nodes[pos.line]
end

function M.keymaps(buf)
  local map = function(lhs, fn, desc)
    vim.keymap.set('n', lhs, fn, { buffer = buf, nowait = true, silent = true, desc = 'rtl_integ: ' .. desc })
  end
  map('<CR>', function()
    local node = M.node_at_cursor()
    if node then app().open_node(node, 'module') end
  end, 'open module source')
  map('<LeftRelease>', function()
    local node = node_at_mouse()
    if node then app().open_node(node, 'module') end
  end, 'open module source')
  map('<2-LeftMouse>', function()
    local node = node_at_mouse()
    if node then M.toggle(node) end
  end, 'expand / collapse')
  -- (by default these select a word / line / block: Visual mode in the tree)
  for _, lhs in ipairs({ '<2-LeftRelease>', '<3-LeftMouse>', '<3-LeftRelease>', '<4-LeftMouse>', '<4-LeftRelease>' }) do
    map(lhs, '<Nop>', 'ignored')
  end
  map('i', function() app().open_node(M.node_at_cursor(), 'inst') end, 'open instantiation site')
  map('o', function() M.toggle() end, 'expand / collapse')
  map('za', function() M.toggle() end, 'expand / collapse')
  map('t', function() app().toggle_view() end, 'template <-> RTL view')
  map('L', function() app().cycle_label() end, 'hierarchy labels')
  map('T', function() require('rtl_integ.toolbar').theme_menu() end, 'theme')
  map('f', function() require('rtl_integ.search').focus() end, 'search instance / module names')
  map('<C-f>', function() require('rtl_integ.search').focus() end, 'search instance / module names')
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
  map('?', function() app().help() end, 'help')
end

-- Fallback links; the themes (themes.panel_groups) set the real colours.
function M.setup_highlights()
  local set = function(name, link) vim.api.nvim_set_hl(0, name, { default = true, link = link }) end
  set('RtlIntegName', 'Identifier')
  set('RtlIntegModule', 'Type')
  set('RtlIntegFile', 'Comment')
  set('RtlIntegPunct', 'Delimiter')
  set('RtlIntegIcon', 'Comment')
  set('RtlIntegTag', 'Special')
  set('RtlIntegTagCode', 'WarningMsg')
  set('RtlIntegMarked', 'Search')
  set('RtlIntegMarkSign', 'Search')
  set('RtlIntegMatch', 'IncSearch')
end

return M
