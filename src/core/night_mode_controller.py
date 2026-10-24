# -*- coding: utf-8 -*-
"""DEV-007 / R6 Night Mode autonomous loop — serial plan loop with recovery, no parallel projects.

Flow (one project at a time):
  plan → eligible step → task payload → (Runtime executes) → terminal
       → recover/replan if ERROR → next step → morning summary

Does NOT:
  - call intake / LocalQueue directly (returns payloads for existing path)
  - bypass FSM / DONE gate
  - run MAX_PARALLEL_PROJECTS > 1

MAX_PARALLEL_PROJECTS is hard-coded to 1 for night sessions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MAX_PARALLEL_PROJECTS = 1  # R6 invariant


@dataclass
class NightSessionConfig:
    max_steps: int = 20
    max_duration_sec: float = 0.0  # 0 = unlimited
    apply_plan_recovery: bool = True
    save_plan: bool = True
    project_root: str | Path | None = None
    project_id: str = ""
    channel: str = "gpt"


@dataclass
class NightSessionResult:
    ok: bool = True
    stopped_reason: str = ""
    steps_planned: int = 0
    steps_processed: int = 0
    done: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    replans: list[str] = field(default_factory=list)
    task_payloads: list[dict[str, Any]] = field(default_factory=list)
    recovery_outcomes: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    duration_sec: float = 0.0
    max_parallel: int = MAX_PARALLEL_PROJECTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stopped_reason": self.stopped_reason,
            "steps_planned": self.steps_planned,
            "steps_processed": self.steps_processed,
            "done": list(self.done),
            "errors": list(self.errors),
            "deferred": list(self.deferred),
            "replans": list(self.replans),
            "recovery_outcomes": list(self.recovery_outcomes),
            "summary": self.summary,
            "duration_sec": self.duration_sec,
            "max_parallel": self.max_parallel,
            "task_payloads_n": len(self.task_payloads),
        }


def step_to_task_payload(
    step: Any,
    *,
    project: str = "",
    channel: str = "gpt",
    night: bool = True,
) -> dict[str, Any]:
    """Map LivingStep → task dict for intake (caller enqueues via existing path)."""
    sid = str(getattr(step, "id", "") or "")
    action = str(getattr(step, "action", "") or getattr(step, "title", "") or "").strip()
    files = list(getattr(step, "files", None) or [])
    complexity = int(getattr(step, "complexity", 3) or 3)
    meta = dict(getattr(step, "meta", None) or {})
    meta.update({
        "plan_step_id": sid,
        "night_mode": bool(night),
        "complexity": complexity,
        "source": "night_mode_r6",
    })
    return {
        "message": action or f"night step {sid}",
        "project": project or str(getattr(step, "target", "") or ""),
        "channel": channel,
        "files": files,
        "metadata": meta,
        "complexity": complexity,
    }


def run_night_session(
    plan: Any,
    *,
    config: NightSessionConfig | None = None,
    execute_step: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> NightSessionResult:
    """Run serial night loop over plan steps.

    execute_step(payload) -> terminal row dict with status DONE|ERROR|DEFERRED
    If execute_step is None, only selects steps and builds payloads (dry-run).
    """
    cfg = config or NightSessionConfig()
    result = NightSessionResult(max_parallel=MAX_PARALLEL_PROJECTS)
    t0 = time.monotonic()

    if MAX_PARALLEL_PROJECTS != 1:
        result.ok = False
        result.stopped_reason = "max_parallel_must_be_1"
        return result

    try:
        from intelligence.night_scheduler import NightScheduler, NightConfig

        sched = NightScheduler(
            NightConfig(
                max_tasks_per_night=max(1, cfg.max_steps),
                max_duration_sec=float(cfg.max_duration_sec or 0),
            )
        )
        steps = sched.select_plan_steps_for_night(plan)
    except Exception as exc:
        result.ok = False
        result.stopped_reason = f"select_failed:{type(exc).__name__}"
        result.summary = str(exc)[:300]
        return result

    result.steps_planned = len(steps)
    root = Path(cfg.project_root) if cfg.project_root else None
    project = cfg.project_id or (root.name if root else "")

    for step in steps:
        if cfg.max_duration_sec > 0 and (time.monotonic() - t0) > cfg.max_duration_sec:
            result.stopped_reason = "max_duration"
            break
        if result.steps_processed >= cfg.max_steps:
            result.stopped_reason = "max_steps"
            break

        payload = step_to_task_payload(step, project=project, channel=cfg.channel)
        result.task_payloads.append(payload)
        sid = str(getattr(step, "id", "") or "")

        if execute_step is None:
            # dry-run: only collect payloads
            result.steps_processed += 1
            continue

        try:
            terminal = execute_step(payload) or {}
        except Exception as exc:
            terminal = {
                "status": "ERROR",
                "result": {"error": f"{type(exc).__name__}: {exc}"},
                "metadata": dict(payload.get("metadata") or {}),
                "attempts": 1,
            }

        status = str(terminal.get("status") or terminal.get("terminal_state") or "").upper()
        result.steps_processed += 1

        if status == "DONE":
            result.done.append(sid)
            continue

        if status == "DEFERRED":
            result.deferred.append(sid)
            continue

        # ERROR or unknown → recovery
        result.errors.append(sid)
        row = dict(terminal)
        meta = dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {})
        meta.setdefault("plan_step_id", sid)
        meta.setdefault("failure_layer", meta.get("failure_layer") or "verification")
        row["metadata"] = meta
        if "result" not in row:
            row["result"] = {"error": str(terminal.get("error") or "night_step_error")}

        try:
            from core.recovery_controller import run_recovery

            outcome = run_recovery(
                row,
                plan=plan,
                project_root=root,
                apply_plan=cfg.apply_plan_recovery,
                save_plan=cfg.save_plan,
            )
            result.recovery_outcomes.append(outcome)
            action = str((outcome.get("decision") or {}).get("action") or "")
            if action == "ask_user":
                result.stopped_reason = "ask_user"
                result.ok = True  # controlled stop, not crash
                break
            if action == "stop":
                result.stopped_reason = "recovery_stop"
                break
            if action == "replan" and outcome.get("applied"):
                nid = (outcome.get("plan_replan") or {}).get("new_step_id")
                if nid:
                    result.replans.append(str(nid))
        except Exception as exc:
            result.recovery_outcomes.append({
                "ok": False,
                "enqueued": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    result.duration_sec = time.monotonic() - t0
    if not result.stopped_reason:
        result.stopped_reason = "complete" if result.steps_processed else "no_steps"

    # morning-style summary
    lines = [
        f"Night session ({result.stopped_reason})",
        f"planned={result.steps_planned} processed={result.steps_processed}",
        f"DONE={len(result.done)} ERROR={len(result.errors)} DEFERRED={len(result.deferred)} replan={len(result.replans)}",
        f"max_parallel={MAX_PARALLEL_PROJECTS}",
        f"duration_sec={result.duration_sec:.1f}",
    ]
    if result.done:
        lines.append("done: " + ", ".join(result.done[:10]))
    if result.errors:
        lines.append("errors: " + ", ".join(result.errors[:10]))
    if result.replans:
        lines.append("replans: " + ", ".join(result.replans[:10]))
    result.summary = "\n".join(lines)
    return result



def _resolve_start_state(
    plan: Any,
    cfg: NightSessionConfig,
    *,
    resume: bool,
    run_id: str | None,
    sdir: Path,
    result: NightSessionResult,
) -> tuple[dict[str, Any], str, dict[str, Any], set[str]] | None:
    """Resolve the start state for a night run (new or resumed).

    Returns (state, run_id, plan_info, skip_steps); None means the run is
    already finished and `result` has been filled for an early return.
    """
    from core.night_run_state import (
        STATUS_ASK_USER,
        STATUS_COMPLETED,
        STATUS_STOPPED,
        find_resumable_run,
        load_state,
        new_run_state,
        resume_plan,
        state_path_for,
    )

    existing = None
    if resume:
        if run_id:
            existing = load_state(state_path_for(run_id, sdir))
        if existing is None:
            existing = find_resumable_run(sdir)
        if existing and run_id and existing.get("run_id") != run_id:
            existing = None
    if existing and existing.get("status") in (STATUS_COMPLETED, STATUS_STOPPED, STATUS_ASK_USER):
        # already finished — do not re-execute
        result.ok = True
        result.stopped_reason = "run_already_finished"
        result.done = list(existing.get("completed_steps") or [])
        result.errors = list(existing.get("failed_steps") or [])
        result.summary = f"Night run {existing.get('run_id')} already finished"
        return None
    if existing:
        state = dict(existing)
        rid = str(state["run_id"])
        plan_info = resume_plan(state)
        skip = set(plan_info.get("skip_steps") or [])
    else:
        steps0 = list(getattr(plan, "steps", None) or [])
        ids = [
            str(getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "") or i)
            for i, s in enumerate(steps0)
        ]
        state = new_run_state(project_id=cfg.project_id, plan_step_ids=ids, run_id=run_id)
        rid = str(state["run_id"])
        plan_info = {"action": "continue", "skip_steps": [], "do_not_auto_done": False}
        skip = set()
    return state, rid, plan_info, skip


def _select_pending_steps(
    steps: list[Any],
    skip: set[str],
    state: dict[str, Any],
) -> list[Any]:
    """Filter plan steps down to those eligible for execution now."""
    from core.night_run_state import is_step_done

    pending = []
    for s in steps:
        st = str(getattr(s, "status", None) or (s.get("status") if isinstance(s, dict) else "") or "PENDING").upper()
        sid = str(getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "") or "")
        if sid and (sid in skip or is_step_done(state, sid)):
            continue
        if st in ("PENDING", "READY", ""):
            pending.append(s)
    return pending


def _recover_interrupted_step(
    plan: Any,
    cfg: NightSessionConfig,
    plan_info: dict[str, Any],
    state: dict[str, Any],
    path: Path,
    result: NightSessionResult,
    skip: set[str],
) -> tuple[dict[str, Any], set[str], bool]:
    """Handle a step interrupted mid-run (once). stop=True -> caller returns early."""
    from core.night_run_state import (
        PHASE_RECOVERY_FINISHED,
        PHASE_TERMINAL,
        STATUS_ASK_USER,
        checkpoint,
        mark_run_finished,
        recovery_already_applied,
    )

    if plan_info.get("action") != "recover_interrupted":
        return state, skip, False
    isid = str(plan_info.get("step_id") or "")
    if not isid or isid in state.get("failed_steps", []):
        return state, skip, False
    row = {
        "id": isid,
        "attempts": int(state.get("current_attempt") or 0),
        "metadata": {"failure_kind": "worker_crash", "night_interrupted": True},
        "result": {"error": plan_info.get("reason") or "interrupted"},
        "error": plan_info.get("reason") or "interrupted",
    }
    try:
        from core.recovery_controller import run_recovery
        outcome = run_recovery(row, plan=plan, project_root=cfg.project_root,
                               apply_plan=cfg.apply_plan_recovery, save_plan=cfg.save_plan)
        result.recovery_outcomes.append(outcome)
        action = str((outcome.get("decision") or {}).get("action") or "")
        if not recovery_already_applied(state, isid, action, int(state.get("current_attempt") or 0)):
            state = checkpoint(path, state, phase=PHASE_RECOVERY_FINISHED,
                               step_id=isid, recovery_action=action,
                               attempt=int(state.get("current_attempt") or 0))
        if action == "ask_user":
            mark_run_finished(path, state, status=STATUS_ASK_USER, stop_reason="ask_user")
            result.stopped_reason = "ask_user"
            result.summary = "Interrupted step → ask_user"
            return state, skip, True
    except Exception as exc:
        result.recovery_outcomes.append({"ok": False, "enqueued": False, "error": str(exc)})
    state = checkpoint(path, state, phase=PHASE_TERMINAL, step_id=isid, terminal="ERROR")
    result.errors.append(isid)
    skip.add(isid)
    return state, skip, False


def _classify_terminal(exec_out: dict[str, Any]) -> str:
    """DONE gate: normalize terminal state; downgrade unverified DONE to ERROR."""
    terminal = str(exec_out.get("terminal_state") or exec_out.get("status") or "").upper()
    if terminal == "DONE" and exec_out.get("verified") is False:
        terminal = "ERROR"
        exec_out["error"] = exec_out.get("error") or "unverified_done_rejected"
    if terminal == "DONE" and (
        exec_out.get("verified") is False
        or (exec_out.get("verified") is not True and exec_out.get("gate_ok") is not True
            and "verified" in exec_out)
    ):
        terminal = "ERROR"
        exec_out["error"] = exec_out.get("error") or "done_without_verify"
    return terminal


def _build_error_row(
    payload: dict[str, Any],
    exec_out: dict[str, Any],
    sid: str,
) -> dict[str, Any]:
    """Build the recovery row dict for a failed step."""
    row = {
        "id": payload.get("id") or sid,
        "attempts": int(exec_out.get("attempts") or 0),
        "metadata": dict(payload.get("metadata") or {}),
        "result": dict(exec_out.get("result") or {}),
        "error": str(exec_out.get("error") or ""),
    }
    row["result"].setdefault("error", row["error"])
    if exec_out.get("worker_result"):
        row["metadata"]["worker_result"] = exec_out["worker_result"]
    if exec_out.get("verification"):
        row["result"]["verification"] = exec_out["verification"]
    return row


def _run_step_recovery(
    *,
    row: dict[str, Any],
    plan: Any,
    cfg: NightSessionConfig,
    exec_out: dict[str, Any],
    state: dict[str, Any],
    path: Path,
    result: NightSessionResult,
    sid: str,
) -> tuple[dict[str, Any], bool]:
    """Run recovery for a failed step. stop=True -> caller stops the loop."""
    from core.night_run_state import (
        PHASE_RECOVERY_FINISHED,
        STATUS_ASK_USER,
        STATUS_STOPPED,
        checkpoint,
        mark_run_finished,
        recovery_already_applied,
    )

    try:
        from core.recovery_controller import run_recovery
        outcome = run_recovery(
            row, plan=plan, project_root=cfg.project_root,
            apply_plan=cfg.apply_plan_recovery, save_plan=cfg.save_plan,
        )
        result.recovery_outcomes.append(outcome)
        action = str((outcome.get("decision") or {}).get("action") or "")
        att = int(exec_out.get("attempts") or 0)
        if recovery_already_applied(state, sid, action, att):
            pass  # idempotent
        else:
            state = checkpoint(path, state, phase=PHASE_RECOVERY_FINISHED,
                               step_id=sid, recovery_action=action, attempt=att)
        if action == "ask_user":
            mark_run_finished(path, state, status=STATUS_ASK_USER, stop_reason="ask_user")
            result.stopped_reason = "ask_user"
            return state, True
        if action == "stop":
            mark_run_finished(path, state, status=STATUS_STOPPED, stop_reason="recovery_stop")
            result.stopped_reason = "recovery_stop"
            return state, True
        if action == "replan" and outcome.get("applied"):
            nid = (outcome.get("plan_replan") or {}).get("new_step_id")
            if nid:
                result.replans.append(str(nid))
    except Exception as exc:
        result.recovery_outcomes.append({"ok": False, "enqueued": False, "error": str(exc)})
    return state, False


def run_autonomous_loop(
    plan: Any,
    *,
    execute_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    config: NightSessionConfig | None = None,
    state_dir: str | Path | None = None,
    resume: bool = True,
    run_id: str | None = None,
) -> NightSessionResult:
    """DEV-007/008: serial autonomous cycle with checkpoint + resume.

    execute_fn(task_payload) → {terminal_state, verified?, error?, ...}
    state_dir: where to store night run JSON (default user_data/night_runs)
    """
    from core.night_run_state import (
        MAX_ACTIVE_NIGHT_RUNS,
        PHASE_EXECUTION_FINISHED,
        PHASE_STEP_STARTED,
        PHASE_TERMINAL,
        PHASE_VERIFICATION_FINISHED,
        STATUS_ASK_USER,
        STATUS_COMPLETED,
        STATUS_STOPPED,
        acquire_lock,
        atomic_write_state,
        checkpoint,
        default_state_dir,
        is_step_done,
        mark_run_finished,
        release_lock,
        state_path_for,
    )

    cfg = config or NightSessionConfig()
    if execute_fn is None:
        return run_night_session(plan, config=cfg)

    assert MAX_PARALLEL_PROJECTS == 1
    assert MAX_ACTIVE_NIGHT_RUNS == 1
    sdir = Path(state_dir) if state_dir else default_state_dir()
    result = NightSessionResult()
    t0 = time.monotonic()

    resolved = _resolve_start_state(plan, cfg, resume=resume, run_id=run_id, sdir=sdir, result=result)
    if resolved is None:
        return result
    state, rid, plan_info, skip = resolved

    lock = acquire_lock(sdir, run_id=rid)
    if not lock.get("ok"):
        result.ok = False
        result.stopped_reason = str(lock.get("error") or "lock_failed")
        result.summary = f"Night lock: {result.stopped_reason}"
        return result

    path = state_path_for(rid, sdir)
    try:
        atomic_write_state(path, state)

        steps = list(getattr(plan, "steps", None) or [])
        result.steps_planned = len(steps)
        pending = _select_pending_steps(steps, skip, state)

        state, skip, stop = _recover_interrupted_step(plan, cfg, plan_info, state, path, result, skip)
        if stop:
            return result

        for step in pending:
            if result.steps_processed >= int(cfg.max_steps or 20):
                result.stopped_reason = "max_steps"
                break
            if cfg.max_duration_sec and (time.monotonic() - t0) >= float(cfg.max_duration_sec):
                result.stopped_reason = "max_duration"
                break

            sid = str(getattr(step, "id", None) or (step.get("id") if isinstance(step, dict) else "") or result.steps_processed)
            if is_step_done(state, sid):
                continue

            payload = step_to_task_payload(step, project=cfg.project_id, channel=cfg.channel, night=True)
            payload.setdefault("metadata", {})["night_run_id"] = rid
            result.task_payloads.append(payload)
            result.steps_processed += 1

            state = checkpoint(path, state, phase=PHASE_STEP_STARTED, step_id=sid, attempt=0)

            try:
                exec_out = dict(execute_fn(payload) or {})
            except Exception as exc:
                exec_out = {"terminal_state": "ERROR", "error": f"{type(exc).__name__}: {exc}", "result": {}}

            state = checkpoint(path, state, phase=PHASE_EXECUTION_FINISHED, step_id=sid,
                               terminal=str(exec_out.get("terminal_state") or ""))

            terminal = _classify_terminal(exec_out)

            if exec_out.get("verification") is not None:
                state = checkpoint(path, state, phase=PHASE_VERIFICATION_FINISHED, step_id=sid, terminal=terminal)

            if terminal == "DONE":
                state = checkpoint(path, state, phase=PHASE_TERMINAL, step_id=sid, terminal="DONE")
                result.done.append(sid)
                if hasattr(step, "status"):
                    try:
                        step.status = "DONE"
                    except Exception:
                        pass
                continue

            if terminal in ("DEFERRED", "SKIPPED"):
                state = checkpoint(path, state, phase=PHASE_TERMINAL, step_id=sid, terminal="DEFERRED")
                result.deferred.append(sid)
                continue

            result.errors.append(sid)
            row = _build_error_row(payload, exec_out, sid)
            state, stop = _run_step_recovery(row=row, plan=plan, cfg=cfg, exec_out=exec_out,
                                             state=state, path=path, result=result, sid=sid)
            if stop:
                break
            state = checkpoint(path, state, phase=PHASE_TERMINAL, step_id=sid, terminal="ERROR")

        result.duration_sec = time.monotonic() - t0
        if not result.stopped_reason:
            result.stopped_reason = "complete" if result.steps_processed else "no_steps"
        fin_status = STATUS_ASK_USER if result.stopped_reason == "ask_user" else (
            STATUS_STOPPED if result.stopped_reason in ("recovery_stop",) else STATUS_COMPLETED
        )
        if result.stopped_reason not in ("ask_user", "recovery_stop"):
            mark_run_finished(path, state, status=fin_status, stop_reason=result.stopped_reason)
        result.summary = (
            f"Night autonomous ({result.stopped_reason}) run_id={rid}\n"
            f"processed={result.steps_processed} DONE={len(result.done)} ERROR={len(result.errors)}\n"
            f"max_parallel={MAX_PARALLEL_PROJECTS}"
        )
        result.ok = result.stopped_reason not in ("recovery_stop",)
        # expose run_id for tests
        result.task_payloads.append({"_night_run_id": rid})  # type: ignore
        return result
    finally:
        release_lock(sdir, run_id=rid)


def night_mode_status() -> dict[str, Any]:
    """Lightweight status for UI / doctor."""
    out: dict[str, Any] = {
        "max_parallel_projects": MAX_PARALLEL_PROJECTS,
        "is_night": False,
        "config": {},
    }
    try:
        from intelligence.night_scheduler import NightScheduler

        sched = NightScheduler()
        out["is_night"] = sched.is_night()
        out["config"] = {
            "max_tasks_per_night": sched.config.max_tasks_per_night,
            "max_duration_sec": sched.config.max_duration_sec,
            "min_complexity": getattr(sched.config, "min_complexity", None),
        }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out
