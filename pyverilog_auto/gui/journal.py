"""Undo journal for structural operations.

Each applied operation stores, under ``<project>/.rtl_integ_gui/journal/``,
the bytes every touched file had before (or that it did not exist) and a
hash of what was written.  Undo restores the last entry, refusing when a
file was changed after the operation.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

from .tedits import file_hash


class JournalError(RuntimeError):
    pass


@dataclass
class JournalEntry:
    id: str
    title: str
    op: str
    time: float
    files: list[dict] = field(default_factory=list)   # {path, before: "files/N" | None, after_hash}
    details: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "title": self.title, "op": self.op, "time": self.time, "files": self.files,
                "details": self.details}


class Journal:
    def __init__(self, state_dir: str):
        self.dir = os.path.join(state_dir, "journal")
        self.root = os.path.dirname(os.path.abspath(state_dir))      # the project directory

    def _stored(self, path: str) -> str:
        """Paths are kept relative to the project, so a copied project's
        journal acts on the copy."""
        rel = os.path.relpath(os.path.abspath(path), self.root)
        return path if rel.startswith("..") or os.path.isabs(rel) else rel.replace(os.sep, "/")

    def _resolve(self, stored: str, title: str) -> str:
        path = os.path.normpath(os.path.join(self.root, stored))
        inside = os.path.normcase(path).startswith(os.path.normcase(self.root) + os.sep)
        if not inside:
            raise JournalError(f"'{title}' changed {stored}, which is outside this project "
                               f"({self.root}); not undoing (was the project copied?)")
        return path

    def entries(self) -> list[JournalEntry]:
        if not os.path.isdir(self.dir):
            return []
        out = []
        for name in sorted(os.listdir(self.dir)):
            p = os.path.join(self.dir, name, "entry.json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                out.append(JournalEntry(d["id"], d["title"], d["op"], d["time"], d.get("files", []),
                                        d.get("details", {})))
        return out

    def record(self, title: str, op: str, before: dict[str, Optional[bytes]], after: dict[str, bytes],
               details: Optional[dict] = None) -> JournalEntry:
        existing = self.entries()
        n = int(existing[-1].id.split("-")[0]) + 1 if existing else 1
        eid = f"{n:04d}-{op}"
        edir = os.path.join(self.dir, eid)
        os.makedirs(os.path.join(edir, "files"), exist_ok=True)
        entry = JournalEntry(eid, title, op, time.time(), details=details or {})
        for i, (path, data) in enumerate(before.items()):
            rec = {"path": self._stored(path), "before": None,
                   "after_hash": file_hash(after[path]) if path in after else None}
            if data is not None:
                rel = f"files/{i}"
                with open(os.path.join(edir, rel), "wb") as fh:
                    fh.write(data)
                rec["before"] = rel
            entry.files.append(rec)
        with open(os.path.join(edir, "entry.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(entry.to_json(), fh, indent=1)
        return entry

    def undo_last(self) -> JournalEntry:
        entries = self.entries()
        if not entries:
            raise JournalError("nothing to undo")
        entry = entries[-1]
        edir = os.path.join(self.dir, entry.id)
        paths = [self._resolve(rec["path"], entry.title) for rec in entry.files]
        for rec, path in zip(entry.files, paths):
            if rec.get("after_hash") is None:
                continue
            if not os.path.exists(path):
                raise JournalError(f"{path} was removed after '{entry.title}'; not undoing")
            with open(path, "rb") as fh:
                if file_hash(fh.read()) != rec["after_hash"]:
                    raise JournalError(f"{path} changed after '{entry.title}'; not undoing")
        for rec, path in zip(entry.files, paths):
            if rec["before"] is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(os.path.join(edir, rec["before"]), "rb") as fh:
                    data = fh.read()
                with open(path, "wb") as fh:
                    fh.write(data)
        shutil.rmtree(edir)
        return entry
