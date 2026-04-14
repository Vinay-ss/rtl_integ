"""VerilogConfig — all user-configurable settings for AUTO processing.

Ported from the ``defcustom`` variables in ``verilog-mode.el`` (lines
~1200–3200) whose names start with ``verilog-auto-``,
``verilog-library-``, or ``verilog-typedef-``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VerilogConfig:
    """Per-file configuration, mirroring the Emacs defcustom knobs."""

    # Library search (verilog-library-*)
    library_flags: list[str] = field(default_factory=lambda: [""])
    library_directories: list[str] = field(default_factory=lambda: ["."])
    library_files: list[str] = field(default_factory=list)
    library_extensions: list[str] = field(
        default_factory=lambda: [".v", ".va", ".sv"]
    )
    library_shell_command_string: Optional[str] = None

    # AUTO behavior
    auto_wire_type: Optional[str] = None
    auto_declare_nettype: Optional[str] = None
    auto_inst_sort: bool = False
    auto_inst_vector: object = True  # True, None/False, or "unsigned"
    auto_inst_column: int = 40
    auto_inst_param_value: bool = False
    auto_inst_param_value_type: bool = True
    auto_inst_dot_name: bool = False
    auto_inst_template_numbers: Optional[str] = None  # None, "lsb", "msb"
    auto_inst_interfaced_ports: bool = False
    auto_inst_template_required: bool = False
    auto_arg_sort: bool = False
    auto_arg_format: str = "packed"  # "packed" or "single"
    auto_sense_include_inputs: bool = False
    auto_sense_defines_constant: bool = False
    auto_reset_blocking_in_non: bool = True
    auto_reset_widths: object = True   # True, False/None, or "unbased"
    auto_reset_widths_add: bool = False
    auto_wire_comment: bool = True
    auto_star_expand: bool = True
    auto_star_save: bool = False
    auto_read_includes: bool = False
    auto_ignore_concat: bool = False
    auto_simplify_expressions: bool = True
    auto_tieoff_declaration: str = "wire"
    auto_tieoff_ignore_regexp: Optional[str] = None
    auto_unused_ignore_regexp: Optional[str] = None
    auto_input_ignore_regexp: Optional[str] = None
    auto_output_ignore_regexp: Optional[str] = None
    auto_inout_ignore_regexp: Optional[str] = None
    auto_reg_input_assigned_ignore_regexp: Optional[str] = None

    # General
    case_fold: bool = True
    typedef_regexp: Optional[str] = None
    typedef_members_regexp: Optional[str] = None
    typedef_parameters_regexp: Optional[str] = None
    active_low_regexp: Optional[str] = None
    assignment_delay: str = ""
    warn_fatal: bool = False
    highlight_grouping_keywords: bool = False  # used in indent

    # General formatting
    fill_column: int = 100  # Emacs fill-column; 0test.el uses 100

    # Indentation (needed for batch-indent)
    indent_level: int = 3
    indent_level_module: int = 3
    indent_level_declaration: int = 3
    indent_level_behavioral: int = 3
    indent_level_directive: int = 1
    case_indent: int = 2
    cexp_indent: int = 1
    indent_begin_depth: int = 1

    # User-defined functions from eval:(defun ...) in Local Variables
    # Maps function name -> (param_names: list[str], body_sexp: str)
    user_functions: dict[str, tuple[list[str], str]] = field(default_factory=dict)

    # Defines set by AUTO_LISP or verilog-read-defines
    # Keys are the define name; values are the substitution string
    defines: dict[str, str] = field(default_factory=dict)

    # Enum associations from verilog-read-defines (venum-* in elisp).
    # Maps enum tag name -> list of constant names belonging to that enum.
    # Populated by DefinesParser when reading includes.
    enum_assocs: dict[str, list[str]] = field(default_factory=dict)
