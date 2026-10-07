"""Structural operations: plan, preview, apply (with journal), verify, undo.

Registered on the RPC server by :func:`install`:

* ``plan_wrap(paths, module, instance, choices?, port_naming?)``
* ``plan_hoist(path, choices?)``
* ``plan_unroll(path, choices?)``
* ``apply(plan_id)`` / ``discard(plan_id)``
* ``journal()`` / ``undo()``

A plan is computed against the current build and holds the planned template
edits; ``apply`` writes them (journaled), rebuilds, and checks that leaf
connectivity is unchanged.
"""

from __future__ import annotations

from typing import Optional

from ..journal import Journal, JournalError
from ..session import Session, SessionError
from ..tedits import TemplateEditError
from ..verify import elab_messages, new_messages, verify_designs
from .common import OpError, Plan


class OpsManager:
    def __init__(self, session: Session):
        self.s = session
        self.plans: dict[int, Plan] = {}
        session.ops = self

    @property
    def journal_store(self) -> Journal:
        proj = self.s._need_project()
        return Journal(proj.state_dir)

    def _keep(self, plan: Plan, rebuilt: bool) -> dict:
        self.plans[plan.id] = plan
        proj = self.s.project
        out = plan.to_json(proj.root if proj else None)
        out["rebuilt"] = rebuilt
        return out

    def _fresh_build(self) -> bool:
        """Plans map design locations to template lines through the last
        build, so a project file edited since then must be rebuilt first
        (otherwise the edits land on the wrong lines).  True if it rebuilt."""
        changed = self.s.changed_inputs()
        if not changed:
            self.s._need_build()
            return False
        proj = self.s._need_project()
        names = ", ".join(proj.rel(f) for f in changed)
        self.s.log(f"{names} changed since the last build: rebuilding first", "warning")
        if not self.s.build(reload=True)["ok"]:
            raise SessionError(f"rebuild after editing {names} failed; fix the errors (see the console) "
                               "and try again", "E_NOT_BUILT")
        return True

    # -- planning -------------------------------------------------------------

    def plan_wrap(self, paths: list[str], module: str, instance: str, choices: Optional[dict] = None,
                  port_naming: Optional[str] = None) -> dict:
        from .wrap import plan_wrap

        rebuilt = self._fresh_build()
        return self._keep(plan_wrap(self.s, paths, module, instance, choices, port_naming), rebuilt)

    def plan_hoist(self, path: str, choices: Optional[dict] = None) -> dict:
        from .hoist import plan_hoist

        rebuilt = self._fresh_build()
        return self._keep(plan_hoist(self.s, path, choices), rebuilt)

    def plan_unroll(self, path: str, choices: Optional[dict] = None) -> dict:
        from .unroll import plan_unroll

        rebuilt = self._fresh_build()
        return self._keep(plan_unroll(self.s, path, choices), rebuilt)

    def discard(self, plan_id: int) -> dict:
        self.plans.pop(int(plan_id), None)
        return {}

    # -- applying ---------------------------------------------------------------

    def apply(self, plan_id: int) -> dict:
        plan = self.plans.pop(int(plan_id), None)
        if plan is None:
            raise SessionError("unknown or already applied plan", "E_PLAN")
        if not plan.ok:
            raise SessionError("the plan has errors and cannot be applied", "E_PLAN")
        changed = self.s.changed_inputs()
        if changed:
            names = ", ".join(self.s._need_project().rel(f) for f in changed)
            raise SessionError(f"not applied: {names} changed after this change was planned; plan it again",
                               "E_STALE")
        before_design = self.s.result.design if self.s.result else None
        before_msgs = elab_messages(before_design) if before_design is not None else []
        try:
            before = plan.edits.before_bytes()
            written = plan.edits.apply()
        except (TemplateEditError, OSError) as exc:
            raise SessionError(f"not applied: {exc}", "E_APPLY") from None
        entry = self.journal_store.record(plan.title, plan.op, before, written,
                                          {"renames": plan.renames, "focus": plan.focus})
        self.s.log(f"applied: {plan.title} (journal {entry.id})")
        summary = self.s.build(reload=True)
        result = {"message": f"applied: {plan.title}", "journal": entry.id, "focus": plan.focus,
                  "diagnostics": [], "verified": None, "build_ok": summary["ok"]}
        if not summary["ok"]:
            result["message"] += " -- the rebuild failed, see the console (u undoes it)"
            result["verified"] = False
            return result
        if before_design is not None and self.s.result and self.s.result.design is not None:
            after = self.s.result.design
            vr = verify_designs(before_design, after, plan.renames)
            fresh = new_messages(before_msgs, elab_messages(after))
            result["verified"] = vr.ok and not fresh
            if vr.ok:
                result["message"] += " -- leaf connectivity unchanged"
            else:
                result["message"] += " -- WARNING: leaf connectivity changed (u undoes it)"
                for line in vr.describe().splitlines():
                    result["diagnostics"].append({"severity": "warning", "code": "W_VERIFY", "message": line})
            if fresh:
                result["message"] += f" -- {len(fresh)} new elaboration message(s)"
                for line in fresh:
                    result["diagnostics"].append({"severity": "warning", "code": "W_ELAB", "message": line})
            for msg in self._printed_markers(written):
                result["verified"] = False
                result["diagnostics"].append({"severity": "warning", "code": "W_MARKER", "message": msg})
        return result

    def _printed_markers(self, written: dict[str, bytes]) -> list[str]:
        """Template code lines that ended up printed as text (a marker line
        that lost its place, e.g. ``   // py`` indented)."""
        from .units import marker_of

        res = self.s.result
        out_msgs: list[str] = []
        touched = {k.lower() for k in written}
        for o in res.outputs if res else []:
            if o.kind != "template" or o.src.lower() not in touched:
                continue
            marker = marker_of(o)
            try:
                with open(o.gen_path, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                s = line.strip()
                if s == marker or s.startswith(marker + " "):
                    out_msgs.append(f"{o.out}:{n}: template code printed as text: {s}")
        return out_msgs

    def journal(self) -> list[dict]:
        return [{"id": e.id, "title": e.title, "op": e.op, "time": e.time} for e in self.journal_store.entries()]

    def undo(self) -> dict:
        try:
            entry = self.journal_store.undo_last()
        except JournalError as exc:
            raise SessionError(str(exc), "E_UNDO") from None
        self.s.log(f"undone: {entry.title}")
        summary = self.s.build(reload=True)
        return {"message": f"undone: {entry.title}", "build_ok": summary["ok"]}


def _safe(fn):
    def wrapper(**kw):
        try:
            return fn(**kw)
        except OpError as exc:
            raise SessionError(exc.message, exc.code) from None
    wrapper.__name__ = getattr(fn, "__name__", "op")
    return wrapper


def install(server) -> OpsManager:
    mgr = OpsManager(server.session)
    for name in ("plan_wrap", "plan_hoist", "plan_unroll", "apply", "discard", "journal", "undo"):
        server.register(name, _safe(getattr(mgr, name)))
    return mgr
