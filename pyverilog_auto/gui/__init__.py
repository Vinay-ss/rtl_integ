"""RTL integration GUI: Neovim front end over a Python backend.

The backend (:mod:`.server`) builds a project of prepro templates and plain
sources (:mod:`.project`, :mod:`.build`), maps instances back to the
template lines that produce them (:mod:`.srcmap`) and serves the hierarchy
to the Lua plugin in ``nvim/``.

    python -m pyverilog_auto.gui.launch [PROJECT_DIR | rtl_integ_project.toml | -f design.f]
"""

from __future__ import annotations

from .build import Builder, BuildResult
from .project import MANIFEST_NAME, Project, ProjectError, load_project, view_only_project
from .session import Session, SessionError
from .srcmap import CODE, GUARDED, LITERAL, LOOP, SUBST, SourceMap, Statement

__all__ = [
    "Builder",
    "BuildResult",
    "CODE",
    "GUARDED",
    "LITERAL",
    "LOOP",
    "MANIFEST_NAME",
    "Project",
    "ProjectError",
    "SUBST",
    "Session",
    "SessionError",
    "SourceMap",
    "Statement",
    "load_project",
    "view_only_project",
]
