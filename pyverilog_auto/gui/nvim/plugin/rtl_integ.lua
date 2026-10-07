-- User commands for the rtl_integ GUI.
if vim.g.loaded_rtl_integ then return end
vim.g.loaded_rtl_integ = true

local function app()
  local m = require('rtl_integ')
  if not m._setup_done then
    m.setup(vim.g.rtl_integ_config or {})
    m._setup_done = true
  end
  return m
end

vim.api.nvim_create_user_command('RtlIntegOpen', function(o) app().open(o.fargs) end,
  { nargs = '*', complete = 'file', desc = 'Open an rtl_integ project (dir, manifest, or -f FILELIST [TOP])' })
vim.api.nvim_create_user_command('RtlIntegClose', function() app().close() end, { desc = 'Close the rtl_integ GUI' })
vim.api.nvim_create_user_command('RtlBuild', function(o) app().build(o.bang) end,
  { bang = true, desc = 'Rebuild the project (! forces all templates)' })
vim.api.nvim_create_user_command('RtlView', function(o) app().toggle_view(o.fargs[1]) end, {
  nargs = '?',
  complete = function() return { 'template', 'integ', 'gen' } end,
  desc = 'Right-panel view: template | integ | gen (no argument: toggle)',
})
vim.api.nvim_create_user_command('RtlConsole', function(o)
  app()
  require('rtl_integ.console').run(o.args)
end, { nargs = '+', desc = 'Run a console command on the backend' })
vim.api.nvim_create_user_command('RtlWrap', function() app().wrap() end, { desc = 'Wrap the marked instances' })
vim.api.nvim_create_user_command('RtlHoist', function()
  app().hoist(require('rtl_integ.tree').node_at_cursor())
end, { desc = 'Hoist the instance under the cursor out of its wrapper' })
vim.api.nvim_create_user_command('RtlUnroll', function()
  app().unroll(require('rtl_integ.tree').node_at_cursor())
end, { desc = 'Unroll the template loop that prints the instance under the cursor' })
vim.api.nvim_create_user_command('RtlUndo', function() app().undo() end, { desc = 'Undo the last operation' })

vim.api.nvim_create_autocmd('BufWritePost', {
  group = vim.api.nvim_create_augroup('rtl_integ', { clear = true }),
  callback = function(ev)
    local m = package.loaded['rtl_integ']
    if m then m.on_write(ev.match) end
  end,
})
