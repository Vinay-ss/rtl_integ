-- Colour palettes of the built-in themes (rtl-<id> colorschemes).
--
-- Colours are taken from each theme's published palette; the mapping to
-- highlight groups is ours (themes/init.lua), so the panels of the GUI look
-- the same in every theme.  Upstream projects and licences:
--   tokyonight       folke/tokyonight.nvim            Apache-2.0
--   catppuccin-*     catppuccin/nvim                  MIT
--   kanagawa         rebelot/kanagawa.nvim            MIT
--   rose-pine        rose-pine/neovim                 MIT
--   gruvbox          ellisonleao/gruvbox.nvim         MIT
--   onedark          navarasu/onedark.nvim            MIT
--   dracula          dracula/vim                      MIT
--   nord             nordtheme/vim                    MIT
--   nightfox         EdenEast/nightfox.nvim           MIT
--   github-light     projekt0n/github-nvim-theme      MIT
--
-- UI keys: bg, bg_dark (panels, floats), bg_hl (cursor line), bg_vis
-- (selection), fg, fg_dim, gutter (line numbers), comment, border, accent.
-- Base colours: red orange yellow green cyan blue purple magenta.
-- syn: colours of the syntax roles (keyword, statement, cond, func, type,
-- storage, string, number, const, ident, operator, preproc, special, label,
-- delim, tplvar).
local P = {}

P.order = {
  'tokyonight', 'catppuccin-mocha', 'kanagawa', 'rose-pine', 'gruvbox', 'onedark',
  'dracula', 'nord', 'nightfox', 'catppuccin-latte', 'github-light',
}

P.tokyonight = {
  name = 'Tokyo Night', background = 'dark',
  bg = '#1a1b26', bg_dark = '#16161e', bg_hl = '#292e42', bg_vis = '#283457',
  fg = '#c0caf5', fg_dim = '#a9b1d6', gutter = '#3b4261', comment = '#565f89', border = '#15161e',
  accent = '#7aa2f7',
  red = '#f7768e', orange = '#ff9e64', yellow = '#e0af68', green = '#9ece6a',
  cyan = '#7dcfff', blue = '#7aa2f7', purple = '#9d7cd8', magenta = '#bb9af7',
  syn = {
    keyword = '#7dcfff', statement = '#bb9af7', cond = '#bb9af7', func = '#7aa2f7', type = '#2ac3de',
    storage = '#9d7cd8', string = '#9ece6a', number = '#ff9e64', const = '#ff9e64', ident = '#73daca',
    operator = '#89ddff', preproc = '#7dcfff', special = '#2ac3de', label = '#e0af68', delim = '#89ddff',
    tplvar = '#ff9e64',
  },
}

P['catppuccin-mocha'] = {
  name = 'Catppuccin Mocha', background = 'dark',
  bg = '#1e1e2e', bg_dark = '#181825', bg_hl = '#313244', bg_vis = '#45475a',
  fg = '#cdd6f4', fg_dim = '#bac2de', gutter = '#585b70', comment = '#9399b2', border = '#11111b',
  accent = '#89b4fa',
  red = '#f38ba8', orange = '#fab387', yellow = '#f9e2af', green = '#a6e3a1',
  cyan = '#89dceb', blue = '#89b4fa', purple = '#cba6f7', magenta = '#f5c2e7',
  syn = {
    keyword = '#cba6f7', statement = '#cba6f7', cond = '#cba6f7', func = '#89b4fa', type = '#f9e2af',
    storage = '#eba0ac', string = '#a6e3a1', number = '#fab387', const = '#fab387', ident = '#f2cdcd',
    operator = '#89dceb', preproc = '#f5c2e7', special = '#f5c2e7', label = '#74c7ec', delim = '#9399b2',
    tplvar = '#fab387',
  },
}

P['catppuccin-latte'] = {
  name = 'Catppuccin Latte', background = 'light',
  bg = '#eff1f5', bg_dark = '#e6e9ef', bg_hl = '#dce0e8', bg_vis = '#bcc0cc',
  fg = '#4c4f69', fg_dim = '#5c5f77', gutter = '#9ca0b0', comment = '#7c7f93', border = '#ccd0da',
  accent = '#1e66f5',
  red = '#d20f39', orange = '#fe640b', yellow = '#df8e1d', green = '#40a02b',
  cyan = '#04a5e5', blue = '#1e66f5', purple = '#8839ef', magenta = '#ea76cb',
  syn = {
    keyword = '#8839ef', statement = '#8839ef', cond = '#8839ef', func = '#1e66f5', type = '#df8e1d',
    storage = '#e64553', string = '#40a02b', number = '#fe640b', const = '#fe640b', ident = '#dd7878',
    operator = '#04a5e5', preproc = '#ea76cb', special = '#ea76cb', label = '#209fb5', delim = '#7c7f93',
    tplvar = '#fe640b',
  },
}

P.kanagawa = {
  name = 'Kanagawa Wave', background = 'dark',
  bg = '#1f1f28', bg_dark = '#16161d', bg_hl = '#2a2a37', bg_vis = '#223249',
  fg = '#dcd7ba', fg_dim = '#c8c093', gutter = '#54546d', comment = '#727169', border = '#16161d',
  accent = '#7e9cd8',
  red = '#e46876', orange = '#ffa066', yellow = '#e6c384', green = '#98bb6c',
  cyan = '#7fb4ca', blue = '#7e9cd8', purple = '#957fb8', magenta = '#d27e99',
  syn = {
    keyword = '#957fb8', statement = '#957fb8', cond = '#957fb8', func = '#7e9cd8', type = '#7aa89f',
    storage = '#938aa9', string = '#98bb6c', number = '#d27e99', const = '#ffa066', ident = '#e6c384',
    operator = '#c0a36e', preproc = '#e46876', special = '#7fb4ca', label = '#9cabca', delim = '#9cabca',
    tplvar = '#ff9e3b',
  },
}

P['rose-pine'] = {
  name = 'Rosé Pine', background = 'dark',
  bg = '#191724', bg_dark = '#16141f', bg_hl = '#21202e', bg_vis = '#403d52',
  fg = '#e0def4', fg_dim = '#908caa', gutter = '#6e6a86', comment = '#6e6a86', border = '#26233a',
  accent = '#c4a7e7',
  red = '#eb6f92', orange = '#ebbcba', yellow = '#f6c177', green = '#9ccfd8',
  cyan = '#9ccfd8', blue = '#31748f', purple = '#c4a7e7', magenta = '#c4a7e7',
  syn = {
    keyword = '#31748f', statement = '#31748f', cond = '#31748f', func = '#ebbcba', type = '#9ccfd8',
    storage = '#9ccfd8', string = '#f6c177', number = '#f6c177', const = '#f6c177', ident = '#c4a7e7',
    operator = '#908caa', preproc = '#eb6f92', special = '#ebbcba', label = '#9ccfd8', delim = '#908caa',
    tplvar = '#eb6f92',
  },
}

P.gruvbox = {
  name = 'Gruvbox Dark', background = 'dark',
  bg = '#282828', bg_dark = '#1d2021', bg_hl = '#3c3836', bg_vis = '#504945',
  fg = '#ebdbb2', fg_dim = '#d5c4a1', gutter = '#7c6f64', comment = '#928374', border = '#504945',
  accent = '#fabd2f',
  red = '#fb4934', orange = '#fe8019', yellow = '#fabd2f', green = '#b8bb26',
  cyan = '#8ec07c', blue = '#83a598', purple = '#d3869b', magenta = '#d3869b',
  syn = {
    keyword = '#fb4934', statement = '#fb4934', cond = '#fb4934', func = '#b8bb26', type = '#fabd2f',
    storage = '#fe8019', string = '#b8bb26', number = '#d3869b', const = '#d3869b', ident = '#83a598',
    operator = '#d5c4a1', preproc = '#8ec07c', special = '#fe8019', label = '#8ec07c', delim = '#a89984',
    tplvar = '#fe8019',
  },
}

P.onedark = {
  name = 'One Dark', background = 'dark',
  bg = '#282c34', bg_dark = '#21252b', bg_hl = '#2c313c', bg_vis = '#3e4452',
  fg = '#abb2bf', fg_dim = '#848b98', gutter = '#495162', comment = '#5c6370', border = '#181a1f',
  accent = '#61afef',
  red = '#e06c75', orange = '#d19a66', yellow = '#e5c07b', green = '#98c379',
  cyan = '#56b6c2', blue = '#61afef', purple = '#c678dd', magenta = '#c678dd',
  syn = {
    keyword = '#c678dd', statement = '#c678dd', cond = '#c678dd', func = '#61afef', type = '#e5c07b',
    storage = '#c678dd', string = '#98c379', number = '#d19a66', const = '#d19a66', ident = '#e06c75',
    operator = '#56b6c2', preproc = '#c678dd', special = '#56b6c2', label = '#56b6c2', delim = '#848b98',
    tplvar = '#d19a66',
  },
}

P.dracula = {
  name = 'Dracula', background = 'dark',
  bg = '#282a36', bg_dark = '#21222c', bg_hl = '#343746', bg_vis = '#44475a',
  fg = '#f8f8f2', fg_dim = '#c0c0d0', gutter = '#6272a4', comment = '#6272a4', border = '#191a21',
  accent = '#bd93f9',
  red = '#ff5555', orange = '#ffb86c', yellow = '#f1fa8c', green = '#50fa7b',
  cyan = '#8be9fd', blue = '#8be9fd', purple = '#bd93f9', magenta = '#ff79c6',
  syn = {
    keyword = '#ff79c6', statement = '#ff79c6', cond = '#ff79c6', func = '#50fa7b', type = '#8be9fd',
    storage = '#ff79c6', string = '#f1fa8c', number = '#bd93f9', const = '#bd93f9', ident = '#ffb86c',
    operator = '#ff79c6', preproc = '#ff79c6', special = '#ffb86c', label = '#8be9fd', delim = '#f8f8f2',
    tplvar = '#ffb86c',
  },
}

P.nord = {
  name = 'Nord', background = 'dark',
  bg = '#2e3440', bg_dark = '#272c36', bg_hl = '#3b4252', bg_vis = '#434c5e',
  fg = '#d8dee9', fg_dim = '#b8c0cc', gutter = '#4c566a', comment = '#616e88', border = '#3b4252',
  accent = '#88c0d0',
  red = '#bf616a', orange = '#d08770', yellow = '#ebcb8b', green = '#a3be8c',
  cyan = '#88c0d0', blue = '#81a1c1', purple = '#b48ead', magenta = '#b48ead',
  syn = {
    keyword = '#81a1c1', statement = '#81a1c1', cond = '#81a1c1', func = '#88c0d0', type = '#8fbcbb',
    storage = '#81a1c1', string = '#a3be8c', number = '#b48ead', const = '#b48ead', ident = '#d08770',
    operator = '#81a1c1', preproc = '#5e81ac', special = '#ebcb8b', label = '#ebcb8b', delim = '#eceff4',
    tplvar = '#d08770',
  },
}

P.nightfox = {
  name = 'Nightfox', background = 'dark',
  bg = '#192330', bg_dark = '#131a24', bg_hl = '#29394f', bg_vis = '#2b3b51',
  fg = '#cdcecf', fg_dim = '#aeafb0', gutter = '#5a6d87', comment = '#738091', border = '#131a24',
  accent = '#719cd6',
  red = '#c94f6d', orange = '#f4a261', yellow = '#dbc074', green = '#81b29a',
  cyan = '#63cdcf', blue = '#719cd6', purple = '#9d79d6', magenta = '#d67ad2',
  syn = {
    keyword = '#9d79d6', statement = '#9d79d6', cond = '#baa1e2', func = '#86abdc', type = '#dbc074',
    storage = '#d67ad2', string = '#81b29a', number = '#f4a261', const = '#f4a261', ident = '#63cdcf',
    operator = '#aeafb0', preproc = '#d67ad2', special = '#719cd6', label = '#e0c989', delim = '#aeafb0',
    tplvar = '#f4a261',
  },
}

P['github-light'] = {
  name = 'GitHub Light', background = 'light',
  bg = '#ffffff', bg_dark = '#f6f8fa', bg_hl = '#eaeef2', bg_vis = '#cce6ff',
  fg = '#1f2328', fg_dim = '#59636e', gutter = '#8c959f', comment = '#6e7781', border = '#d0d7de',
  accent = '#0969da',
  red = '#cf222e', orange = '#bc4c00', yellow = '#9a6700', green = '#1a7f37',
  cyan = '#1b7c83', blue = '#0969da', purple = '#8250df', magenta = '#bf3989',
  syn = {
    keyword = '#cf222e', statement = '#cf222e', cond = '#cf222e', func = '#8250df', type = '#953800',
    storage = '#cf222e', string = '#0a3069', number = '#0550ae', const = '#0550ae', ident = '#116329',
    operator = '#0550ae', preproc = '#cf222e', special = '#0550ae', label = '#1b7c83', delim = '#1f2328',
    tplvar = '#bc4c00',
  },
}

return P
