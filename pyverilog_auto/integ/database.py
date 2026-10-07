"""Design-aware ModuleDatabase implementations.

``DesignModuleDatabase`` answers module lookups from the design index
(and the in-memory overlay of already-expanded files) before falling back
to the classic ``-y``/``-v`` search.  Declarations come from the pyslang
reader when available, else from ``DeclParser`` on the overlay text.

``SlangReaderDatabase`` keeps the classic file search but reads
declarations with the pyslang reader; it powers the reader-in-the-loop
golden test variant.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from ..library.module_db import ModuleDatabase
from ..signal import ModDecls, Modi

if TYPE_CHECKING:
    from ..config import VerilogConfig
    from .design import Design
    from .model import ModuleDef


class DesignModuleDatabase(ModuleDatabase):
    """ModuleDatabase backed by a :class:`Design` index."""

    def __init__(self, config: "VerilogConfig", current_file: str, design: "Design", reader=None) -> None:
        super().__init__(config, current_file)
        self._design = design
        self._reader = reader
        self._current_key = os.path.normcase(os.path.normpath(os.path.abspath(current_file)))

    # ------------------------------------------------------------------

    def lookup(self, module_name: str, ignore_error: bool = False) -> Optional[Modi]:
        if module_name in self._modi_cache:
            return self._modi_cache[module_name]
        real = self._detick(module_name)
        design = self._design
        modi: Optional[Modi] = None

        # 1. A definition in the file being processed wins (same as the
        #    classic search, which tries the current file first).
        mod = design.modules.get(real)
        if mod is not None:
            sf = design.files.get(mod.file)
            if sf is not None:
                modi = mod.modi(sf.path)
        # 2. Fall back to the classic -y/-v search (files outside the design).
        if modi is None:
            modi = super().lookup(module_name, ignore_error=True)
        if modi is None and not ignore_error:
            raise RuntimeError(
                f"Can't locate '{module_name}' module definition in the design "
                f"or library directories {self._config.library_directories}"
            )
        self._modi_cache[module_name] = modi
        return modi

    def get_decls(self, modi: Modi) -> ModDecls:
        design = self._design
        key = os.path.normcase(os.path.normpath(os.path.abspath(modi.filepath)))
        sf = design.files.get(key)
        mod = self._module_for(modi, key)
        if sf is None or mod is None:
            return super().get_decls(modi)
        # the parse depends on the *calling* file's verilog-typedef-regexp (a typedef'd
        # port is otherwise read as an interface), so it is part of the key
        cache_key = (key, sf.version, mod.name, self._config.typedef_regexp)
        cached = design._decls_cache.get(cache_key)
        if cached is not None:
            return cached
        decls: Optional[ModDecls] = None
        if self._reader is not None and not mod.needs_text_parser:
            try:
                decls = self._reader.read(mod, self._config)
            except Exception as exc:  # pragma: no cover - defensive fallback
                from .model import Diag

                design.diagnostics.append(Diag(
                    "warning", f"pyslang reader failed for {mod.name} ({exc}); using text parser",
                    sf.path, mod.keyword_range.line, "design"))
                decls = None
        if decls is None:
            from ..buffer import VerilogBuffer
            from ..parser.decl_parser import DeclParser

            buf = VerilogBuffer.from_string(sf.text, sf.path)
            buf.goto_char(modi.point)
            decls = DeclParser(buf, self._config).parse()
        design._decls_cache[cache_key] = decls
        return decls

    # ------------------------------------------------------------------

    def _module_for(self, modi: Modi, key: str) -> Optional["ModuleDef"]:
        mod = self._design.modules.get(modi.name)
        if mod is not None and mod.file == key:
            return mod
        for m in self._design.modules.values():
            if m.file == key and m.name == modi.name:
                return m
        return None


class SlangReaderDatabase(ModuleDatabase):
    """Classic file search + pyslang reader for declarations."""

    def __init__(self, config: "VerilogConfig", current_file: str, *, directive_policy: str = "text") -> None:
        super().__init__(config, current_file)
        from .reader import SlangModuleReader

        self._reader = SlangModuleReader(None, directive_policy=directive_policy)

    def get_decls(self, modi: Modi) -> ModDecls:
        cache_key = self._cache_key_with_point(modi.filepath, modi.point)
        if cache_key in self._decls_cache:
            return self._decls_cache[cache_key]
        decls = self._reader.read_module_from_file(modi, self._config)
        if decls is None:
            decls = super().get_decls(modi)
        self._decls_cache[cache_key] = decls
        return decls
