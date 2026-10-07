-- Self-contained configuration used by the rtl-integ-gui launcher:
--   nvim --clean -u <this file> [+args]
-- It needs no user config and no third-party plugins.
local here = vim.fs.dirname(debug.getinfo(1, 'S').source:sub(2))
vim.opt.runtimepath:prepend(here)

vim.opt.mouse = 'a'
vim.opt.termguicolors = true
vim.opt.number = true
vim.opt.splitright = true
vim.opt.splitbelow = true
vim.opt.hidden = true
vim.opt.updatetime = 300
vim.opt.shada = ''          -- launcher sets NVIM_APPNAME; keep no history across projects
vim.cmd('syntax on')
vim.cmd('filetype plugin indent on')
pcall(vim.cmd, 'colorscheme habamax')

vim.filetype.add({ extension = { svp = 'systemverilog', plv = 'verilog', pyv = 'verilog' } })

vim.cmd('runtime plugin/rtl_integ.lua')

local cfg = vim.g.rtl_integ_config or {}
if vim.env.RTL_INTEG_PYTHON and vim.env.RTL_INTEG_PYTHON ~= '' then cfg.python = vim.env.RTL_INTEG_PYTHON end
require('rtl_integ').setup(cfg)
require('rtl_integ')._setup_done = true

-- RTL_INTEG_OPEN: arguments for :RtlIntegOpen, joined by newlines (set by the launcher).
local open_args = vim.env.RTL_INTEG_OPEN
if open_args ~= nil then
  vim.api.nvim_create_autocmd('VimEnter', {
    once = true,
    callback = function()
      local args = {}
      for a in open_args:gmatch('[^\n]+') do table.insert(args, a) end
      require('rtl_integ').open(args)
    end,
  })
end
