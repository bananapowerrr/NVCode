# -*- coding: utf-8 -*-
"""FC-27 Dynamic Queue — plan is source of truth; queue is a projection.

Eligible steps from LivingPlan are emitted into desktop LocalQueue (and
optionally file-bus). Already-queued / in-progress / finished steps are not
re-emitted (tracked in plan step meta + optional state file).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intelligence.living_plan import (
    LivingPlan,
    LivingStep,
    is_active,
    load_living_plan,
    normalize_status,
    save_living_plan,
)


@dataclass
class EmitResult:
    emitted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "emitted": list(self.emitted),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }


def _step_task_payload(
    step: LivingStep,
    *,
    plan: LivingPlan,
    project: str = "",
    channel: str = "desktop",
) -> dict[str, Any]:
    msg = step.action
    if step.target:
        msg = f"{msg}: {step.target}"
    if step.note:
        msg = f"{msg} ({step.note})"
    return {
        "id": f"plan-{plan.version}-{step.id}",
        "message": msg,
        "files": list(step.files),
        "project": project or plan.project_id or "",
        "channel": channel,
        "status": "PENDING",
        "complexity": int(step.complexity or 3),
        "metadata": {
            "source": "living_plan",
            "plan_version": plan.version,
            "plan_step_id": step.id,
            "depends_on": list(step.depends_on),
            "primary_channel": channel,
            **dict(step.meta or {}),
        },
    }


def _already_emitted(step: LivingStep) -> bool:
    meta = step.meta or {}
    if meta.get("emitted"):
        return True
    if meta.get("task_id"):
        return True
    st = normalize_status(step.status)
    return st in ("IN_PROGRESS", "DONE", "ERROR")


def mark_step_emitted(step: LivingStep, task_id: str) -> None:
    step.meta = dict(step.meta or {})
    step.meta["emitted"] = True
    step.meta["task_id"] = task_id
    step.meta["emitted_at"] = time.time()
    if normalize_status(step.status) == "PENDING":
        step.status = "READY"


def sync_plan_to_queue(
    plan: LivingPlan,
    *,
    project_root: str | Path | None = None,
    project: str = "",
    max_emit: int = 4,
    use_desktop_queue: bool = True,
    use_filebus: bool = False,
    filebus_channel: str = "autopilot",
    bus_root: str | Path | None = None,
    persist: bool = True,
) -> EmitResult:
    """Project eligible plan steps into queues. Idempotent per step meta."""
    result = EmitResult()
    # P0: do not emit while open plan decisions block the project
    if project_root is not None:
        try:
            from app.plan_service import get_decision_queue
            dq = get_decision_queue(project_root)
            # block if any open decision for this project root (project id optional)
            if dq.open_items() and (
                dq.has_blocking("")
                or dq.has_blocking(project or plan.project_id or "")
                or dq.has_blocking(str(project_root))
            ):
                result.errors.append("blocked: open WAITING_DECISION")
                return result
        except Exception:
            pass
    eligible = plan.eligible_for_queue()
    emitted_n = 0

    for step in eligible:
        if emitted_n >= max_emit:
            result.skipped.append(step.id)
            continue
        if _already_emitted(step):
            result.skipped.append(step.id)
            continue
        if not is_active(step.status):
            result.skipped.append(step.id)
            continue

        payload = _step_task_payload(
            step, plan=plan, project=project, channel="desktop" if use_desktop_queue else filebus_channel
        )
        tid: str | None = None

        if use_desktop_queue:
            # P0: only TaskService intake — fail-closed, no LocalQueue.put bypass
            try:
                from core.task_service import submit_payload

                root = Path(project_root) if project_root else None
                tid, err = submit_payload(
                    payload,
                    source="living_plan",
                    root=root,
                    mirror_phone=False,
                    soft_intake=False,
                )
                if err or not tid:
                    result.errors.append(f"{step.id}: intake/queue: {err or 'no task id'}")
                    continue
            except Exception as exp:
                result.errors.append(f"{step.id}: {type(exp).__name__}: {exp}")
                continue

        if use_filebus:
            try:
                root = Path(bus_root or project_root or ".")
                from core.bus import FileBus

                fb_id = payload["id"]
                FileBus(root, (filebus_channel,)).write(
                    filebus_channel,
                    "incoming",
                    f"{fb_id}.json",
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
                tid = tid or fb_id
            except Exception as exp:
                result.errors.append(f"{step.id} filebus: {exp}")

        if tid:
            mark_step_emitted(step, str(tid))
            result.emitted.append(str(tid))
            emitted_n += 1
        else:
            result.errors.append(f"{step.id}: no task id")

    if persist and project_root is not None:
        try:
            save_living_plan(project_root, plan)
        except Exception as exp:
            result.errors.append(f"persist: {exp}")

    return result


def sync_from_disk(
    project_root: str | Path,
    *,
    max_emit: int = 4,
    use_desktop_queue: bool = True,
    use_filebus: bool = False,
) -> EmitResult:
    """Load living plan from project and sync."""
    root = Path(project_root)
    plan = load_living_plan(root)
    if not plan.steps:
        return EmitResult()
    return sync_plan_to_queue(
        plan,
        project_root=root,
        project=plan.project_id,
        max_emit=max_emit,
        use_desktop_queue=use_desktop_queue,
        use_filebus=use_filebus,
        bus_root=root,
        persist=True,
    )


def on_task_terminal(
    plan: LivingPlan,
    *,
    task_id: str = "",
    plan_step_id: str = "",
    status: str = "DONE",
    message: str = "",
) -> bool:
    """Update plan step when worker finishes (runtime terminal hook)."""
    step = None
    if plan_step_id:
        step = plan.get(plan_step_id)
    if step is None and task_id:
        for s in plan.steps:
            if (s.meta or {}).get("task_id") == task_id:
                step = s
                break
            if task_id.endswith(f"-{s.id}") or task_id == s.id:
                step = s
                break
    if step is None:
        return False
    st = normalize_status(status)
    if st in ("DONE", "SUCCESS", "OK"):
        step.status = "DONE"
    elif st in ("ERROR", "FAILED", "FAIL"):
        step.status = "ERROR"
    elif st in ("CANCELLED", "CANCELED"):
        step.status = "CANCELLED"
    elif st in ("DEFERRED", "RETRY"):
        # keep step active / ready — not terminal for plan progress
        step.meta = dict(step.meta or {})
        step.meta["last_runtime_status"] = st
        if message:
            step.meta["last_message"] = str(message)[:300]
        return True
    else:
        return False
    step.meta = dict(step.meta or {})
    step.meta["last_runtime_status"] = step.status
    step.meta["terminal_at"] = time.time()
    if message:
        step.meta["last_message"] = str(message)[:300]
    return True


def notify_plan_task_terminal(
    project_root: str | Path | None,
    *,
    task_id: str = "",
    plan_step_id: str = "",
    status: str = "DONE",
    message: str = "",
    metadata: dict | None = None,
) -> bool:
    """Load plan from disk, apply terminal, persist. Safe no-op if no plan/step."""
    if not project_root:
        return False
    root = Path(project_root)
    meta = metadata or {}
    psid = plan_step_id or str(meta.get("plan_step_id") or "")
    tid = task_id or str(meta.get("task_id") or "")
    if not psid and not tid:
        return False
    # only plan-sourced tasks
    src = str(meta.get("source") or "")
    if src and src not in ("living_plan", "plan", "post_step_continue", "dynamic_queue"):
        # still try if plan_step_id present
        if not psid and not str(tid).startswith("plan-"):
            return False
    try:
        plan = load_living_plan(root)
        ok = on_task_terminal(
            plan, task_id=tid, plan_step_id=psid, status=status, message=message
        )
        if ok:
            save_living_plan(root, plan)
        return ok
    except Exception as exp:
        try:
            import logging
            logging.getLogger("agentbus.plan").warning(
                "notify_plan_task_terminal failed: %s: %s", type(exp).__name__, exp
            )
        except Exception:
            pass
        return False
