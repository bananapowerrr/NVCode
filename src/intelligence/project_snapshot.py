# -*- coding: utf-8 -*-
"""FC-38A Project Command Center snapshot.

One read-model for UI: does not own data — aggregates existing modules.
Analysis ≠ Plan ≠ Queue remains intact.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SnapshotSection:
    """One panel-ready block."""

    id: str
    title: str
    status: str = "ok"  # ok | warn | block | empty
    headline: str = ""
    lines: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)  # {id, label}
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectSnapshot:
    """Unified project view for Command Center."""

    project_root: str = ""
    project_name: str = ""
    status: str = "unknown"  # ready | needs_decision | blocked | executing | empty
    status_label: str = ""
    next_action: str = ""
    sections: list[SnapshotSection] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    built_at: float = field(default_factory=time.time)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "project_name": self.project_name,
            "status": self.status,
            "status_label": self.status_label,
            "next_action": self.next_action,
            "sections": [s.to_dict() for s in self.sections],
            "counts": dict(self.counts),
            "built_at": self.built_at,
            "errors": list(self.errors),
        }

    def format_human(self) -> str:
        lines = [
            f"=== Project: {self.project_name or self.project_root} ===",
            f"Status: {self.status_label or self.status}",
        ]
        if self.next_action:
            lines.append(f"Next: {self.next_action}")
        for s in self.sections:
            lines.append(f"\n[{s.title}] {s.status}")
            if s.headline:
                lines.append(f"  {s.headline}")
            for ln in s.lines[:8]:
                lines.append(f"  · {ln}")
        return "\n".join(lines)

    def section(self, sid: str) -> SnapshotSection | None:
        for s in self.sections:
            if s.id == sid:
                return s
        return None


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _snap_state(snap: ProjectSnapshot, root: Path, state: Any, errors: list[str]) -> None:
    """TD-019: секция ProjectState — код перенесён из build_project_snapshot без изменений."""
    try:
        from intelligence.project_state import load_project_state
        st = state if state is not None else load_project_state(root)
        snap.counts["completed"] = len(st.completed or [])
        snap.counts["in_progress"] = len(st.in_progress or [])
        snap.counts["pending"] = len(st.pending or [])
        phase = st.current_phase or "init"
        lines = []
        if st.goal:
            lines.append(f"Goal: {st.goal[:120]}")
        lines.append(f"Phase: {phase}")
        if st.decisions:
            lines.append(f"Recorded decisions: {len(st.decisions)}")
        snap.sections.append(SnapshotSection(
            id="state",
            title="Состояние",
            status="ok",
            headline=phase,
            lines=lines,
            meta={"goal": st.goal, "plan_version": st.plan_version},
        ))
    except Exception as exp:
        errors.append(f"state: {exp}")



def _snap_decisions(snap: ProjectSnapshot, root: Path, decisions: Any, errors: list[str]) -> tuple[int, bool, Any]:
    """TD-019: решения и архитектурные стопоры.

    Возвращает (open_decisions, arch_block, decisions): очередь решений
    создаётся здесь, если не была передана, и нужна секции Supervisor.
    """
    open_decisions = 0
    arch_block = False
    try:
        from intelligence.decision_queue import DecisionQueue
        from intelligence.architecture_blockers import (
            format_blocker_banner,
            has_architecture_blockers,
            open_architecture_decisions,
        )
        if decisions is None:
            dq_path = root / ".agentbus" / "decisions.json"
            decisions = DecisionQueue(path=dq_path if dq_path.parent.exists() else None)
        open_items = decisions.open_items()
        open_decisions = len(open_items)
        arch_block = has_architecture_blockers(decisions)
        arch_items = open_architecture_decisions(decisions)
        lines = [f"{i.question[:100]}" for i in open_items[:5]]
        st_status = "block" if open_decisions else "ok"
        actions = []
        if open_decisions:
            actions.append({"id": "open_decisions", "label": "Разобрать решения"})
        snap.sections.append(SnapshotSection(
            id="decisions",
            title="Решения",
            status=st_status,
            headline=f"{open_decisions} открытых" if open_decisions else "Нет блокирующих решений",
            lines=lines,
            actions=actions,
            meta={"architecture": arch_block, "count": open_decisions},
        ))
        if arch_block:
            banner = format_blocker_banner(decisions)
            snap.sections.append(SnapshotSection(
                id="architecture",
                title="Архитектура",
                status="block",
                headline="Нужно решение",
                lines=[x for x in banner.splitlines() if x.strip()][:8],
                actions=[{"id": "architecture_interview", "label": "Открыть решение"}],
                meta={"ids": [i.id for i in arch_items]},
            ))
        snap.counts["open_decisions"] = open_decisions
        return open_decisions, arch_block, decisions
    except Exception as exp:
        errors.append(f"decisions: {exp}")
        return open_decisions, arch_block, decisions



def _snap_plan(snap: ProjectSnapshot, root: Path, plan: Any, errors: list[str]) -> None:
    """TD-019: секция LivingPlan."""
    try:
        from intelligence.living_plan import LivingPlan, is_active
        if plan is None:
            plan_path = root / ".agentbus" / "living_plan.json"
            if plan_path.is_file():
                import json
                raw = json.loads(plan_path.read_text(encoding="utf-8"))
                plan = LivingPlan.from_dict(raw) if hasattr(LivingPlan, "from_dict") else None
        if plan is not None:
            steps = list(getattr(plan, "steps", None) or [])
            active = [s for s in steps if is_active(s)] if callable(is_active) else steps
            done = [s for s in steps if str(getattr(s, "status", "")).upper() in ("DONE", "COMPLETED")]
            blocked = [s for s in steps if str(getattr(s, "status", "")).upper() in ("BLOCKED", "WAITING")]
            snap.counts["plan_total"] = len(steps)
            snap.counts["plan_active"] = len(active)
            snap.counts["plan_done"] = len(done)
            lines = [f"{getattr(s, 'id', '?')}: {getattr(s, 'action', '')[:80]}" for s in active[:6]]
            snap.sections.append(SnapshotSection(
                id="plan",
                title="План",
                status="warn" if blocked else ("ok" if steps else "empty"),
                headline=f"{len(done)}/{len(steps)} выполнено" if steps else "План пуст",
                lines=lines,
                actions=[{"id": "open_plan", "label": "Открыть план"}] if steps else [
                    {"id": "build_plan", "label": "Сформировать план"}
                ],
                meta={"version": getattr(plan, "version", 0)},
            ))
        else:
            snap.sections.append(SnapshotSection(
                id="plan",
                title="План",
                status="empty",
                headline="План ещё не создан",
                actions=[{"id": "build_plan", "label": "Сформировать план"}],
            ))
    except Exception as exp:
        errors.append(f"plan: {exp}")



def _snap_advisor(snap: ProjectSnapshot, root: Path, errors: list[str]) -> None:
    """TD-019: секция советника (быстрый скан без тяжёлых проверок)."""
    try:
        from intelligence.development_advisor import advise
        from intelligence.project_analysis import quick_scan
        analysis = _safe(lambda: quick_scan(str(root)), None)
        adv = _safe(lambda: advise(analysis) if analysis is not None else advise(str(root)), None)
        if adv is not None:
            items = list(getattr(adv, "items", None) or getattr(adv, "recommendations", None) or [])
            lines = []
            for it in items[:5]:
                if isinstance(it, dict):
                    lines.append(str(it.get("title") or it.get("text") or it)[:100])
                else:
                    lines.append(str(getattr(it, "title", None) or getattr(it, "text", None) or it)[:100])
            snap.counts["advisor"] = len(items)
            snap.sections.append(SnapshotSection(
                id="advisor",
                title="Совет",
                status="ok" if items else "empty",
                headline=f"{len(items)} направлений" if items else "Пока без рекомендаций",
                lines=lines,
                actions=[{"id": "open_audit", "label": "Открыть аудит"}] if items else [],
            ))
    except Exception as exp:
        errors.append(f"advisor: {exp}")



def _snap_supervisor(snap: ProjectSnapshot, root: Path, decisions: Any, errors: list[str]) -> None:
    """TD-019: секция Supervisor (smart_waiting) с человеческими подписями."""
    try:
        from intelligence.smart_waiting import evaluate_wait
        from intelligence.decision_queue import DecisionQueue
        dq = decisions
        if dq is None:
            dq = DecisionQueue()
        wait = evaluate_wait(decisions=dq, project=str(root), check_night=True)
        reason = getattr(wait, "reason", "ready")
        human = {
            "ready": "Готов к работе",
            "waiting_decision": "Ждёт вашего решения",
            "architecture_blocker": "Архитектурный стопор",
            "policy_ask": "Нужно подтверждение",
            "policy_block": "Политика блокирует",
            "defer_to_night": "Отложено на ночь",
            "manual_pause": "Пауза",
        }.get(reason, reason)
        snap.sections.append(SnapshotSection(
            id="supervisor",
            title="Supervisor",
            status="block" if not getattr(wait, "can_emit", True) else "ok",
            headline=human,
            lines=[getattr(wait, "detail", "") or ""],
            meta={"reason": reason, "can_emit": getattr(wait, "can_emit", True)},
        ))
    except Exception as exp:
        errors.append(f"supervisor: {exp}")



def _snap_capabilities(snap: ProjectSnapshot, errors: list[str], include_capabilities: bool) -> None:
    """TD-019: опциональная секция окружения (configuration_advisor)."""
    if include_capabilities:
        try:
            from core.configuration_advisor import advise_configuration
            adv_c = advise_configuration(probe_network=False)
            snap.sections.append(SnapshotSection(
                id="capabilities",
                title="Окружение",
                status="ok",
                headline=f"Mode: {adv_c.mode}",
                lines=[f"{a.role}: {a.model_name or a.fallback}" for a in adv_c.assignments],
            ))
        except Exception as exp:
            errors.append(f"capabilities: {exp}")



def _overall_status(snap: ProjectSnapshot, open_decisions: int, arch_block: bool) -> None:
    """TD-019: итоговый статус и next_action по собранным секциям."""
    if arch_block or open_decisions:
        snap.status = "needs_decision"
        snap.status_label = "Нужно ваше решение"
        snap.next_action = "Ответить на открытые вопросы (A/B/C)"
    elif any(s.id == "supervisor" and s.status == "block" for s in snap.sections):
        snap.status = "blocked"
        snap.status_label = "Ожидание"
        snap.next_action = next((s.headline for s in snap.sections if s.id == "supervisor"), "")
    elif snap.counts.get("in_progress"):
        snap.status = "executing"
        snap.status_label = "Выполняется"
        snap.next_action = "Следить за очередью"
    elif snap.counts.get("plan_active"):
        snap.status = "ready"
        snap.status_label = "Готов"
        snap.next_action = "Запустить следующую задачу плана"
    else:
        snap.status = "empty"
        snap.status_label = "Начните с аудита или плана"
        snap.next_action = "Открыть аудит проекта"



def build_project_snapshot(
    project_root: str | Path,
    *,
    include_capabilities: bool = False,
    plan: Any = None,
    decisions: Any = None,
    state: Any = None,
) -> ProjectSnapshot:
    """Aggregate plan / decisions / advisor / architecture into one snapshot."""
    root = Path(project_root).resolve()
    snap = ProjectSnapshot(
        project_root=str(root),
        project_name=root.name,
    )
    errors: list[str] = []

    # --- ProjectState ---
    _snap_state(snap, root, state, errors)

    # --- Decisions / architecture blockers ---
    open_decisions, arch_block, decisions = _snap_decisions(snap, root, decisions, errors)

    # --- Living plan ---
    _snap_plan(snap, root, plan, errors)

    # --- Advisor (cheap, no heavy scan by default) ---
    _snap_advisor(snap, root, errors)

    # --- Supervisor wait (human label) ---
    _snap_supervisor(snap, root, decisions, errors)

    # --- Capabilities optional ---
    _snap_capabilities(snap, errors, include_capabilities)

    # --- Overall status ---
    _overall_status(snap, open_decisions, arch_block)

    snap.errors = errors
    snap.built_at = time.time()
    return snap
