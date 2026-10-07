Self-contained downloads of **rtl-integ-gui**, the Neovim-based GUI for browsing and restructuring
template-generated RTL (wrap instances into a wrapper, hoist them out, edits go into the prepro templates).
Nothing else needs to be installed; Perl is needed only for Perl (`// pl`) templates.

### New in 0.3.0

- **Toolbar**: switch the source view (Template / Generated / Integrated), the hierarchy labels and the
  theme with a click; Build, Undo and Help buttons.
- **Hierarchy**: labels `inst`, `inst (module)` or `inst : module : file`; a search box that filters by
  instance or module name while you type; a click opens the module source, a double-click expands or
  collapses.
- **Themes**: Tokyo Night, Catppuccin Mocha / Latte, Kanagawa, Rosé Pine, Gruvbox, One Dark, Dracula,
  Nord, Nightfox and GitHub Light (menu with live preview); the console stays dark in every theme.
- **Highlighting**: Verilog / SystemVerilog with template code shown as Python or Perl; `.svpy`, `.svpl`,
  `.vpy` and `.vpl` templates are recognised.
- **Projects**: `gen_dir` writes the generated RTL into a directory of the project.
- **Wrap / hoist** work in modules that import packages. A wrapper now gets the parent's parameter types,
  type parameters, typedefs and functions it needs (before, such names became `input logic` ports), and
  keeps `signed` on its ports and nets. Undo works in a copied project.

| Download | For |
|---|---|
| `rtl-integ-gui-*-windows-x86_64.zip` | Windows 10/11 |
| `rtl-integ-gui-*-linux-x86_64.tar.gz` | any x86_64 Linux with glibc 2.17+ (CentOS/RHEL 7+, Rocky/Alma 8+, Ubuntu 18.04+, Debian 10+, SUSE 15), no root needed |
| `rtl-integ-gui-*.x86_64.rpm` | RHEL/CentOS/Rocky/Alma/Fedora/SUSE: installs to `/opt/rtl-integ-gui`, `rtl-integ-gui` on PATH |
| `rtl-integ-gui_*_amd64.deb` | Debian/Ubuntu: same |
| `*-third-party-sources.tar.gz` | sources of the LGPL parts inside the bundles |
| `SHA256SUMS` | checksums of all files |

**Windows:** right-click the zip, Properties, tick *Unblock*, then extract it and run `rtl-integ-gui.cmd --demo`.
The files are not code-signed, so SmartScreen may ask once.

**Linux:** `tar xzf rtl-integ-gui-*-linux-x86_64.tar.gz` and run `./rtl-integ-gui-*/rtl-integ-gui --demo`,
or install the rpm/deb (`sudo dnf install ./rtl-integ-gui-*.rpm`, `sudo apt install ./rtl-integ-gui_*.deb`).
The Neovide window needs glibc 2.35+, a display and OpenGL; elsewhere (RHEL/CentOS 7-9, SSH sessions) the
GUI runs in the terminal with the same layout and mouse support.

Check an installation with `rtl-integ-gui --selftest`; include `rtl-integ-gui --version` in bug reports.
Licenses of all bundled components are in the `LICENSES/` folder of each download.
