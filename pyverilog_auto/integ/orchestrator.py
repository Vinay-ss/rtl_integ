"""Integrator — leaf-first AUTO expansion over a whole design."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Union

from ..auto.engine import AutoEngine
from ..buffer import VerilogBuffer
from .graph import Order
from .model import Diag

if TYPE_CHECKING:
    from .design import Design
    from .sources import SourceFile


@dataclass
class FileResult:
    path: str
    key: str
    level: int
    pass_no: int
    changed: bool = False
    written: bool = False
    diff: Optional[str] = None
    error: Optional[str] = None
    skipped: Optional[str] = None


@dataclass
class ExpandReport:
    results: list[FileResult] = field(default_factory=list)
    order: Optional[Order] = None
    passes: int = 1
    backend: str = "text"
    dry_run: bool = False

    def changed_files(self) -> list[str]:
        seen: list[str] = []
        for r in self.results:
            if r.changed and r.path not in seen:
                seen.append(r.path)
        return seen

    def errors(self) -> list[FileResult]:
        return [r for r in self.results if r.error]

    def summary(self) -> str:
        processed = len({r.key for r in self.results if not r.skipped})
        changed = len(self.changed_files())
        written = len({r.key for r in self.results if r.written})
        errs = len(self.errors())
        mode = " (dry run)" if self.dry_run else ""
        return (f"{processed} file(s) processed, {changed} changed, {written} written, "
                f"{errs} error(s); backend={self.backend}, passes={self.passes}{mode}")


class Integrator:
    """Runs ``AutoEngine`` on every source file, leaves first."""

    def __init__(
        self,
        design: "Design",
        *,
        dry_run: bool = False,
        diff: bool = False,
        only: Optional[str] = None,
        from_level: int = 0,
        passes: Union[int, str] = "auto",
        log: Optional[Callable[[str], None]] = None,
        inject: bool = False,
    ) -> None:
        self.design = design
        self.dry_run = dry_run or diff
        self.diff = diff
        self.only = re.compile(only) if only else None
        self.from_level = from_level
        self.passes = passes
        self.log = log or (lambda s: None)
        self.inject = inject

    # ------------------------------------------------------------------

    def run(self) -> ExpandReport:
        design = self.design
        order = design.order()
        report = ExpandReport(order=order, backend=design.backend, dry_run=self.dry_run)

        self._do_pass(1, order, report, None)
        if self.passes == "auto":
            extra: set[str] = {k for comp in order.cycles for k in comp}
            for r in report.results:
                if r.changed and len(design.files[r.key].modules) >= 2:
                    extra.add(r.key)
            if extra:
                self.log(f"-- pass 2 over {len(extra)} file(s) (cycles / multi-module files)")
                self._do_pass(2, order, report, extra)
                report.passes = 2
        else:
            n = int(self.passes)
            for p in range(2, n + 1):
                self._do_pass(p, order, report, None)
            report.passes = max(1, n)
        return report

    def _do_pass(self, pass_no: int, order: Order, report: ExpandReport, restrict: Optional[set[str]]) -> None:
        design = self.design
        for level, keys in enumerate(order.levels):
            if level < self.from_level:
                continue
            changed: list[str] = []
            for key in keys:
                if restrict is not None and key not in restrict:
                    continue
                sf = design.files[key]
                if sf.role != "source":
                    continue
                if self.only is not None and not (self.only.search(sf.path) or any(self.only.search(m) for m in sf.modules)):
                    report.results.append(FileResult(sf.path, key, level, pass_no, skipped="--only"))
                    continue
                res = self._expand_file(sf, level, pass_no)
                report.results.append(res)
                if res.changed:
                    changed.append(key)
            if changed:
                design.refresh(changed)

    def _expand_file(self, sf: "SourceFile", level: int, pass_no: int) -> FileResult:
        design = self.design
        res = FileResult(sf.path, sf.key, level, pass_no)
        cfg = design.make_config(sf.path)
        buf = VerilogBuffer.from_string(sf.text, sf.path)
        engine = AutoEngine(cfg, db_factory=design.module_db_factory())
        try:
            engine.run(buf, cfg, inject=self.inject)
        except Exception as exc:  # keep going with the other files
            res.error = f"{type(exc).__name__}: {exc}"
            design.diagnostics.append(Diag("error", f"AUTO expansion failed: {res.error}", sf.path, None, "design"))
            self.log(f"[L{level} p{pass_no}] {sf.path}: ERROR {res.error}")
            return res
        new = buf.buffer_string()
        res.changed = new != sf.text
        if res.changed:
            if self.diff or self.dry_run:
                res.diff = "".join(difflib.unified_diff(
                    sf.text.splitlines(keepends=True), new.splitlines(keepends=True),
                    fromfile=f"a/{sf.path}", tofile=f"b/{sf.path}"))
            if not self.dry_run:
                buf.write_to_file()
                res.written = True
            sf.update(new)
            if res.written:
                sf.refresh_stamp()
        state = "changed" if res.changed else "unchanged"
        if res.written:
            state += ", written"
        self.log(f"[L{level} p{pass_no}] {sf.path}: {state}")
        return res
