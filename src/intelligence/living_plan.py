# -*- coding: utf-8 -*-
"""FC-26 Living Plan — versioned plan that can supersede future work.

Source of truth for *upcoming* work. Completed/DONE history is immutable.
Queue (FC-27) will derive eligible tasks from active steps only.

Statuses for steps / graph nodes:
  PENDING | READY | IN_PROGRESS | DONE | ERROR | BLOCKED
  SUPERSEDED | OBSOLETE | REPLACED | CANCELLED

Rules:
  - DONE / ERROR never rewritten to SUPERSEDED
  - only future (not finished) items can be superseded/cancelled
  - each replan bumps ``version`` and keeps prior snapshot in history
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Terminal success/failure — frozen
_FINISHED = frozenset({"DONE", "ERROR"})
# Removed from eligibility (queue must ignore)
_INACTIVE = frozenset({
    "SUPERSEDED", "OBSOLETE", "REPLACED", "CANCELLED",
})
# Can still become READY
_ACTIVE_PENDING = frozenset({
    "PENDING", "READY", "BLOCKED", "IN_PROGRESS",
})

STEP_STATUSES = _FINISHED | _INACTIVE | _ACTIVE_PENDING | frozenset({"SKIPPED"})


def normalize_status(status: str | None) -> str:
    s = (status or "PENDING").strip().upper()
    if s == "CANCELED":
        s = "CANCELLED"
    if s == "SUCCESS":
        s = "DONE"
    if s == "FAIL" or s == "FAILED":
        s = "ERROR"
    return s


def is_finished(status: str | None) -> bool:
    return normalize_status(status) in _FINISHED


def is_active(status: str | None) -> bool:
    return normalize_status(status) in _ACTIVE_PENDING


def is_inactive(status: str | None) -> bool:
    return normalize_status(status) in _INACTIVE


@dataclass
class LivingStep:
    """One plan item (maps to pev PlanStep + optional graph node)."""

    id: str
    action: str
    target: str = ""
    note: str = ""
    status: str = "PENDING"
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    complexity: int = 3
    replaced_by: str = ""
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_line(self) -> str:
        st = normalize_status(self.status)
        base = self.action
        if self.target:
            base = f"{base} — {self.target}"
        if self.note:
            base = f"{base} ({self.note})"
        mark = {
            "DONE": "✓",
            "ERROR": "✗",
            "SUPERSEDED": "↦",
            "OBSOLETE": "·",
            "REPLACED": "↦",
            "CANCELLED": "×",
            "IN_PROGRESS": "…",
            "READY": "▸",
            "BLOCKED": "¦",
        }.get(st, "•")
        extra = f" → {self.replaced_by}" if self.replaced_by else ""
        return f"{mark} [{st}] {base}{extra}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "target": self.target,
            "note": self.note,
            "status": normalize_status(self.status),
            "files": list(self.files),
            "depends_on": list(self.depends_on),
            "complexity": int(self.complexity),
            "replaced_by": self.replaced_by,
            "reason": self.reason,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LivingStep":
        raw = dict(raw or {})
        return cls(
            id=str(raw.get("id") or ""),
            action=str(raw.get("action") or raw.get("message") or ""),
            target=str(raw.get("target") or ""),
            note=str(raw.get("note") or ""),
            status=normalize_status(str(raw.get("status") or "PENDING")),
            files=[str(x) for x in (raw.get("files") or [])],
            depends_on=[str(x) for x in (raw.get("depends_on") or [])],
            complexity=int(raw.get("complexity") or 3),
            replaced_by=str(raw.get("replaced_by") or ""),
            reason=str(raw.get("reason") or ""),
            meta=dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {},
        )


@dataclass
class LivingPlan:
    """Versioned living plan for a project (or a long-running goal)."""

    project_id: str = ""
    summary: str = ""
    version: int = 1
    steps: list[LivingStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[dict[str, Any]] = field(default_factory=list)  # prior versions (summaries)
    meta: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = time.time()

    def get(self, step_id: str) -> LivingStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def active_steps(self) -> list[LivingStep]:
        return [s for s in self.steps if is_active(s.status)]

    def finished_steps(self) -> list[LivingStep]:
        return [s for s in self.steps if is_finished(s.status)]

    def inactive_steps(self) -> list[LivingStep]:
        return [s for s in self.steps if is_inactive(s.status)]

    def eligible_for_queue(self) -> list[LivingStep]:
        """Steps that may be emitted to the queue (deps finished or none)."""
        done_ids = {s.id for s in self.steps if is_finished(s.status) and normalize_status(s.status) == "DONE"}
        # treat ERROR deps as blocking unless meta says otherwise
        out: list[LivingStep] = []
        for s in self.steps:
            if not is_active(s.status):
                continue
            if normalize_status(s.status) == "IN_PROGRESS":
                continue
            deps_ok = True
            for d in s.depends_on:
                dep = self.get(d)
                if dep is None:
                    # missing dep: if superseded chain, skip
                    deps_ok = False
                    break
                st = normalize_status(dep.status)
                if st == "DONE":
                    continue
                if is_inactive(st):
                    # inactive dep does not satisfy dependency
                    deps_ok = False
                    break
                deps_ok = False
                break
            if deps_ok:
                out.append(s)
        return out

    def mark(self, step_id: str, status: str, *, reason: str = "") -> bool:
        s = self.get(step_id)
        if s is None:
            return False
        new_st = normalize_status(status)
        old = normalize_status(s.status)
        # freeze finished
        if old in _FINISHED and new_st in _INACTIVE:
            return False
        if old in _FINISHED and new_st != old and new_st not in ("DONE", "ERROR"):
            # allow ERROR→ stays; DONE frozen except no-op
            if old == "DONE":
                return False
        s.status = new_st
        if reason:
            s.reason = reason[:500]
        self.touch()
        return True

    def supersede(
        self,
        step_id: str,
        *,
        reason: str = "",
        replacement: LivingStep | None = None,
        status: str = "SUPERSEDED",
    ) -> str | None:
        """Mark step inactive; optionally add replacement step. Returns new step id."""
        s = self.get(step_id)
        if s is None:
            return None
        if is_finished(s.status):
            return None
        st = normalize_status(status)
        if st not in _INACTIVE:
            st = "SUPERSEDED"
        new_id: str | None = None
        if replacement is not None:
            if not replacement.id:
                replacement.id = f"{step_id}_v{self.version + 1}"
            # avoid id clash
            existing = {x.id for x in self.steps}
            base = replacement.id
            n = 1
            while replacement.id in existing:
                replacement.id = f"{base}_{n}"
                n += 1
            replacement.status = normalize_status(replacement.status or "PENDING")
            # inherit files/deps if empty
            if not replacement.files:
                replacement.files = list(s.files)
            if not replacement.depends_on:
                replacement.depends_on = list(s.depends_on)
            self.steps.append(replacement)
            new_id = replacement.id
            s.replaced_by = new_id
        s.status = st
        s.reason = (reason or s.reason)[:500]
        self.touch()
        return new_id

    def cancel(self, step_id: str, *, reason: str = "") -> bool:
        return self.mark(step_id, "CANCELLED", reason=reason)

    def obsolete(self, step_id: str, *, reason: str = "") -> bool:
        return self.mark(step_id, "OBSOLETE", reason=reason)

    def replan(
        self,
        *,
        summary: str | None = None,
        add_steps: list[LivingStep] | None = None,
        supersede_ids: list[str] | None = None,
        reason: str = "",
    ) -> int:
        """Bump version: archive compact snapshot, supersede listed future steps, add new."""
        snap = {
            "version": self.version,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "ts": time.time(),
            "reason": reason[:300],
        }
        self.history.append(snap)
        self.history = self.history[-20:]
        self.version = int(self.version or 0) + 1
        if summary is not None:
            self.summary = summary.strip()[:2000]
        for sid in supersede_ids or []:
            self.supersede(sid, reason=reason or f"replan v{self.version}")
        for st in add_steps or []:
            if not st.id:
                st.id = f"s{self.version}_{len(self.steps)}"
            existing = {x.id for x in self.steps}
            if st.id in existing:
                st.id = f"{st.id}_{self.version}"
            st.status = normalize_status(st.status or "PENDING")
            self.steps.append(st)
        self.touch()
        return self.version

    def to_markdown(self) -> str:
        lines = [
            f"# Living Plan v{self.version}",
            "",
            f"Summary: {self.summary or '(none)'}",
            f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.updated_at))}",
            "",
            "## Steps",
        ]
        for i, s in enumerate(self.steps, 1):
            lines.append(f"{i}. {s.as_line()}")
        active = self.active_steps()
        lines.append("")
        lines.append(f"Active: {len(active)} · Finished: {len(self.finished_steps())} · Inactive: {len(self.inactive_steps())}")
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "summary": self.summary,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": list(self.history)[-10:],
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LivingPlan":
        raw = dict(raw or {})
        steps = [LivingStep.from_dict(x) for x in (raw.get("steps") or []) if isinstance(x, dict)]
        return cls(
            project_id=str(raw.get("project_id") or ""),
            summary=str(raw.get("summary") or ""),
            version=int(raw.get("version") or 1),
            steps=steps,
            created_at=float(raw.get("created_at") or time.time()),
            updated_at=float(raw.get("updated_at") or time.time()),
            history=list(raw.get("history") or [])[-20:],
            meta=dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {},
        )

    @classmethod
    def from_pev_plan(cls, plan: Any, *, project_id: str = "") -> "LivingPlan":
        """Import classic pev_loop.Plan into LivingPlan."""
        steps: list[LivingStep] = []
        raw_steps = getattr(plan, "steps", None) or []
        files = list(getattr(plan, "files", None) or [])
        for i, s in enumerate(raw_steps):
            if hasattr(s, "action"):
                action, target, note = s.action, getattr(s, "target", ""), getattr(s, "note", "")
            elif isinstance(s, dict):
                action = str(s.get("action") or "")
                target = str(s.get("target") or "")
                note = str(s.get("note") or "")
            else:
                action, target, note = str(s), "", ""
            steps.append(
                LivingStep(
                    id=f"p{i+1}",
                    action=action,
                    target=target,
                    note=note,
                    files=list(files),
                    status="PENDING",
                )
            )
        return cls(
            project_id=project_id,
            summary=str(getattr(plan, "summary", "") or ""),
            version=1,
            steps=steps,
            meta={"source": "pev_plan", "task_id": str(getattr(plan, "task_id", "") or "")},
        )


def plan_dir(project_root: str | Path) -> Path:
    return Path(project_root) / ".agentbus"


def living_plan_path(project_root: str | Path) -> Path:
    return plan_dir(project_root) / "living_plan.json"


def load_living_plan(project_root: str | Path) -> LivingPlan:
    path = living_plan_path(project_root)
    if not path.is_file():
        return LivingPlan()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return LivingPlan.from_dict(data)
    except Exception as exp:
        try:
            from utils.safe_log import warn
            warn("agentbus.plan", "load_living_plan failed %s: %s: %s", path, type(exp).__name__, exp)
        except Exception:
            pass
    return LivingPlan()


def save_living_plan(project_root: str | Path, plan: LivingPlan) -> Path:
    d = plan_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    plan.touch()
    path = living_plan_path(project_root)
    try:
        # TD-007: atomic plan save (tmp + os.replace)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exp:
        try:
            from utils.safe_log import error
            error("agentbus.plan", "save_living_plan failed %s: %s: %s", path, type(exp).__name__, exp)
        except Exception:
            pass
        raise
    # mirror markdown for humans / pev panel
    try:
        md = d / "living_plan.md"
        # TD-007: atomic md mirror (tmp + os.replace)
        md_tmp = md.with_suffix(md.suffix + ".tmp")
        md_tmp.write_text(plan.to_markdown(), encoding="utf-8")
        md_tmp.replace(md)
    except Exception as exp:
        try:
            from utils.safe_log import warn
            warn("agentbus.plan", "living_plan.md mirror failed: %s", exp)
        except Exception:
            pass
    return path


def apply_living_statuses_to_graph(graph: Any, plan: LivingPlan) -> int:
    """Sync inactive/finished statuses onto TaskGraph nodes by id. Returns updates."""
    if graph is None:
        return 0
    nodes = getattr(graph, "nodes", None) or {}
    n = 0
    for step in plan.steps:
        node = nodes.get(step.id)
        if node is None:
            continue
        st = normalize_status(step.status)
        if getattr(node, "status", None) != st:
            node.status = st
            n += 1
            if step.replaced_by:
                meta = getattr(node, "meta", None)
                if isinstance(meta, dict):
                    meta["replaced_by"] = step.replaced_by
                    meta["reason"] = step.reason
    return n


def patch_graph_finished_predicate() -> None:
    """Extend TaskGraph.all_finished / ready to treat inactive as closed (idempotent doc)."""
    # Logic is applied in graph_ready_ignore_inactive helper below — call sites use it.
    return


def graph_node_counts_as_closed(status: str | None) -> bool:
    st = normalize_status(status)
    return st in _FINISHED or st in _INACTIVE


def filter_ready_nodes(nodes: list[Any]) -> list[Any]:
    """Drop inactive nodes from a ready() result."""
    out = []
    for n in nodes:
        st = normalize_status(getattr(n, "status", None))
        if st in _INACTIVE or st in _FINISHED:
            continue
        out.append(n)
    return out

