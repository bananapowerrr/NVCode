# -*- coding: utf-8 -*-
"""FC-48: Project workflow — Audit → Advisor → Plan → Queue (user-gated).

Intelligence proposes; this module only prepares plan steps and optionally
enqueues via core.task_service when the user confirms. No second Runtime.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class ProjectWorkflow:
    """One façade for the product path: analyze → advise → plan → enqueue."""

    def __init__(self, project_root: str | Path | None = None):
        self.root = Path(project_root).resolve() if project_root else None

    def set_root(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()

    def _require(self) -> Path:
        if not self.root:
            raise ValueError("project_root not set")
        return self.root

    def analyze(self) -> dict[str, Any]:
        """Snapshot + audit + health + open decisions."""
        from app.project_service import ProjectService

        root = self._require()
        ps = ProjectService(root)
        out: dict[str, Any] = {
            "project": str(root),
            "health": {},
            "audit": {},
            "decisions_open": [],
            "blockers": [],
        }
        try:
            out["health"] = ps.get_health()
        except Exception as exp:
            out["health_error"] = str(exp)[:200]
        try:
            out["audit"] = ps.run_audit()
        except Exception as exp:
            out["audit_error"] = str(exp)[:200]
        try:
            from app.tasks_service import TasksService

            out["decisions_open"] = TasksService(root).open_decisions() or []
            if out["decisions_open"]:
                out["blockers"].append("open_decisions")
        except Exception as exp:
            out["decisions_error"] = str(exp)[:200]
        try:
            out["reconcile"] = self._reconcile_plan(root)
        except Exception as exp:
            out["reconcile_error"] = str(exp)[:200]
        banner = ""
        try:
            banner = ps.architecture_banner() or ""
            if banner:
                out["blockers"].append("architecture")
                out["architecture_banner"] = banner
        except Exception:
            pass
        return out

    def _reconcile_plan(self, root) -> dict[str, Any]:
        """Reconcile plan claims against persistent queue and terminal records."""
        from app.plan_service import PlanService
        from core.local_queue import get_local_queue
        from intelligence.dynamic_queue import on_task_terminal
        from intelligence.living_plan import normalize_status
        import json

        root = Path(root).resolve()
        ps = PlanService(str(root))
        plan = ps.load()
        live: set[str] = set()
        terminal: dict[str, tuple[str, dict[str, Any]]] = {}
        try:
            q = get_local_queue(root)
            for raw in list(getattr(q, "_q", []) or []):
                tid = str(raw.get("id") or "")
                if tid:
                    live.add(tid)
        except Exception:
            pass

        scan_roots = [root]
        try:
            from core.config import BUS_ROOT, BASE_DIR
            scan_roots.extend((Path(BUS_ROOT), Path(BASE_DIR)))
        except Exception:
            pass
        unique_roots: list[Path] = []
        seen_roots: set[str] = set()
        for base in scan_roots:
            try:
                key = str(base.resolve())
            except OSError:
                key = str(base)
            if key not in seen_roots:
                seen_roots.add(key)
                unique_roots.append(base)

        for base in unique_roots:
            queue_dir = base / ".agentbus" / "desktop_queue"
            if queue_dir.is_dir():
                for path in queue_dir.glob("*.json"):
                    try:
                        raw = json.loads(path.read_text(encoding="utf-8"))
                        tid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
                        if tid:
                            live.add(tid)
                    except Exception:
                        continue
            for channels_dir in (base / "channels", base / ".agentbus" / "channels"):
                if not channels_dir.is_dir():
                    continue
                try:
                    channel_dirs = [p for p in channels_dir.iterdir() if p.is_dir()]
                except OSError:
                    continue
                for channel_dir in channel_dirs:
                    for state in ("incoming", "processing", "deferred", "done", "errors"):
                        state_dir = channel_dir / state
                        if not state_dir.is_dir():
                            continue
                        for path in state_dir.glob("*.json"):
                            try:
                                raw = json.loads(path.read_text(encoding="utf-8"))
                                tid = str(raw.get("id") or "") if isinstance(raw, dict) else ""
                                if not tid:
                                    continue
                                if state in ("incoming", "processing", "deferred"):
                                    live.add(tid)
                                else:
                                    candidate = (state, raw)
                                    prior = terminal.get(tid)
                                    # A successful terminal record takes precedence
                                    # over an older error duplicate.
                                    if prior is None or (state == "done" and prior[0] != "done"):
                                        terminal[tid] = candidate
                            except Exception:
                                continue

        demoted: list[str] = []
        terminal_synced: list[str] = []
        for step in plan.steps:
            if normalize_status(step.status) != "IN_PROGRESS":
                continue
            meta = step.meta if isinstance(step.meta, dict) else {}
            task_id = str(meta.get("task_id") or "")
            if task_id and task_id in terminal:
                state, raw = terminal[task_id]
                status = "DONE" if state == "done" else "ERROR"
                result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
                message = ""
                if status == "DONE":
                    # Keep the canonical Runtime completion gate authoritative:
                    # a file in `done/` alone is not enough to mark a plan done.
                    from core.terminal_path import verification_allows_done
                    if not verification_allows_done(result):
                        status = ""
                    else:
                        message = str(result.get("summary") or "")
                else:
                    message = str(result.get("error") or raw.get("error") or "")
                if status and on_task_terminal(
                    plan,
                    task_id=task_id,
                    plan_step_id=step.id,
                    status=status,
                    message=message,
                ):
                    terminal_synced.append(step.id)
                    continue
            if task_id and task_id in live:
                continue
            step.status = "PENDING"
            meta["reconciled_from"] = "IN_PROGRESS"
            meta["reconciled_reason"] = "no_live_task" if not task_id else "task_not_live"
            step.meta = meta
            note = "reconcile: задачи нет, возврат в PENDING"
            step.note = (step.note + " | " if step.note else "") + note
            demoted.append(step.id)

        if demoted or terminal_synced:
            ps.save(plan)
        return {
            "demoted_to_pending": demoted,
            "terminal_synced": terminal_synced,
            "checked": len(plan.steps),
        }

    def advise(self, *, limit: int = 5) -> dict[str, Any]:
        from app.agent_service import AgentService

        root = self._require()
        return AgentService(root).suggestions(limit=limit)

    def build_plan_from_advice(self, *, limit: int = 5, persist: bool = False) -> dict[str, Any]:
        """Draft LivingPlan steps from advisor; optionally save under .agentbus."""
        root = self._require()
        out: dict[str, Any] = {"steps": [], "saved": False, "path": ""}
        try:
            from intelligence.development_advisor import advise, draft_living_steps

            rep = advise(root, limit=limit, use_index=False)
            steps = draft_living_steps(rep)
            out["steps"] = list(steps or [])
        except Exception as exp:
            out["error"] = str(exp)
            return out
        if persist and out["steps"]:
            try:
                from intelligence.living_plan import LivingPlan, LivingStep, save_living_plan

                plan = LivingPlan(project_id=str(root))
                for i, s in enumerate(out["steps"]):
                    if isinstance(s, dict):
                        action = str(s.get("action") or s.get("title") or s.get("message") or f"step-{i}")
                        sid = str(s.get("id") or f"adv-{i+1}")
                        plan.steps.append(
                            LivingStep(
                                id=sid,
                                action=action,
                                note=str(s.get("note") or s.get("why") or "")[:500],
                                status=str(s.get("status") or "PENDING"),
                                files=list(s.get("files") or []),
                                complexity=int(s.get("complexity") or 2),
                                meta=dict(s.get("meta") or {}),
                            )
                        )
                    else:
                        plan.steps.append(
                            LivingStep(id=f"adv-{i+1}", action=str(s), status="PENDING")
                        )
                path = save_living_plan(root, plan)
                out["saved"] = True
                out["path"] = str(path)
                out["plan"] = plan.to_dict() if hasattr(plan, "to_dict") else {}
            except Exception as exp:
                # try minimal persist without LivingStep shape
                out["persist_error"] = str(exp)[:300]
                try:
                    from intelligence.living_plan import load_living_plan, save_living_plan

                    plan = load_living_plan(root)
                    # if load works empty, still report
                    out["plan_loaded"] = True
                except Exception as exp2:
                    out["persist_error"] = f"{out.get('persist_error')}; {exp2}"
        return out

    def enqueue_step(
        self,
        message: str,
        *,
        files: list[str] | None = None,
        source: str = "project_workflow",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """User-confirmed enqueue via canonical task_service.submit."""
        root = self._require()
        from app.task_composer import compose_task, validate_task_dict
        from core.task_service import submit_payload

        task = compose_task(
            message=message,
            project=str(root),
            files=files or [],
            metadata={"workflow": "fc48", **dict(metadata or {}), "source": source},
        )
        errs = validate_task_dict(task)
        if errs:
            return {"ok": False, "error": "; ".join(errs)}
        tid, err = submit_payload(task, source=source, root=root)
        if err or not tid:
            return {"ok": False, "error": err or "enqueue failed", "task": task}
        return {"ok": True, "task_id": tid, "task": task}

    def enqueue_advice(self, index: int = 0) -> dict[str, Any]:
        """Compose from suggestion #index and enqueue (explicit user action)."""
        root = self._require()
        from app.agent_service import AgentService
        from app.task_composer import compose_from_suggestion

        try:
            task = compose_from_suggestion(AgentService(root), index, project=root)
        except Exception as exp:
            return {"ok": False, "error": str(exp)}
        return self.enqueue_step(
            str(task.get("message") or ""),
            files=list(task.get("files") or []),
            source="project_workflow.advice",
        )

    def run_preview(self, *, limit: int = 5) -> dict[str, Any]:
        """Full dry-run of the product loop without enqueue."""
        analysis = self.analyze()
        advice = self.advise(limit=limit)
        plan = self.build_plan_from_advice(limit=limit, persist=False)
        return {
            "analysis": analysis,
            "advice": advice,
            "plan": plan,
            "can_enqueue": bool(advice.get("items")) and "open_decisions" not in (analysis.get("blockers") or []),
            "blockers": analysis.get("blockers") or [],
        }



    def accept_finding(self, index: int = 0, *, persist: bool = True) -> dict[str, Any]:
        """User accepts one audit/advisor finding → one LivingPlan step."""
        root = self._require()
        title = ""
        why = ""
        try:
            from app.project_service import ProjectService
            audit = ProjectService(root).run_audit()
            findings = audit.get("findings") or audit.get("recommendations") or []
            if not isinstance(findings, list):
                findings = []
            if index < 0 or index >= len(findings):
                # fallback to advice items
                from app.agent_service import AgentService
                adv = AgentService(root).suggestions(limit=10)
                items = adv.get("items") or adv.get("recommendations") or []
                if index < 0 or index >= len(items):
                    return {"ok": False, "error": f"no finding at index {index}"}
                it = items[index]
                if isinstance(it, dict):
                    title = str(it.get("title") or it.get("text") or it.get("message") or "")
                    why = str(it.get("why") or it.get("reason") or "")
                else:
                    title = str(it)
            else:
                f = findings[index]
                if isinstance(f, dict):
                    title = str(f.get("title") or f.get("message") or f.get("text") or "")
                    why = str(f.get("why") or f.get("detail") or f.get("severity") or "")
                else:
                    title = str(f)
        except Exception as exp:
            return {"ok": False, "error": str(exp)}
        title = (title or "Finding").strip()[:300]
        try:
            from app.plan_service import PlanService
            step = PlanService(root).add_step(
                action=title,
                note=(why or "accepted from audit")[:500],
            )
            return {
                "ok": True,
                "step_id": step.get("id") if isinstance(step, dict) else None,
                "action": title,
                "step": step,
            }
        except Exception as exp:
            return {"ok": False, "error": str(exp)}

    def enqueue_first_pending(self) -> dict[str, Any]:
        """Enqueue first PENDING LivingPlan step (explicit user action)."""
        root = self._require()
        try:
            from app.plan_service import PlanService
            data = PlanService(root).list_plan()
            steps = data.get("steps") if isinstance(data, dict) else []
        except Exception as exp:
            return {"ok": False, "error": f"plan load: {exp}"}
        target = None
        for s in steps or []:
            if not isinstance(s, dict):
                continue
            st = str(s.get("status") or "").upper()
            if st in ("PENDING", "READY", "TODO", ""):
                target = s
                break
        if not target:
            return {"ok": False, "error": "no pending plan step"}
        msg = str(target.get("action") or target.get("title") or target.get("message") or "").strip()
        if not msg:
            return {"ok": False, "error": "empty step action"}
        files = list(target.get("files") or [])
        sid = str(target.get("id") or "").strip()
        if not sid:
            return {"ok": False, "error": "plan step id missing"}
        result = self.enqueue_step(
            msg,
            files=files,
            source="project_workflow.plan_step",
            metadata={
                "plan_step_id": sid,
                "plan_version": int(data.get("version") or 1),
                "project_root": str(root),
            },
        )
        if result.get("ok"):
            result["step_id"] = sid
            result["step_action"] = msg
            if sid:
                try:
                    from app.plan_service import PlanService
                    PlanService(root).set_step_status(
                        sid,
                        "IN_PROGRESS",
                        note="enqueued",
                        task_id=str(result.get("task_id") or ""),
                    )
                except Exception as exp:
                    result["status_warn"] = str(exp)[:200]
        return result

    def format_preview(self, *, limit: int = 5) -> str:
        d = self.run_preview(limit=limit)
        lines = ["=== Project workflow (FC-48) ==="]
        h = (d.get("analysis") or {}).get("health") or {}
        lines.append(f"Status: {h.get('status', '?')} — {h.get('headline', '')[:120]}")
        blockers = d.get("blockers") or []
        if blockers:
            lines.append("Blockers: " + ", ".join(blockers))
        items = (d.get("advice") or {}).get("items") or []
        if items:
            lines.append("Advice:")
            for i, it in enumerate(items, 1):
                title = it.get("title") if isinstance(it, dict) else str(it)
                lines.append(f"  {i}. {title}")
        steps = (d.get("plan") or {}).get("steps") or []
        if steps:
            lines.append(f"Plan draft: {len(steps)} step(s)")
        lines.append("Enqueue: " + ("ready" if d.get("can_enqueue") else "blocked/empty"))
        return "\n".join(lines)
