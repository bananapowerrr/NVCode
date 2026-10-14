# -*- coding: utf-8 -*-
"""TasksService — queue / plan / decisions for bottom panel & Task Detail."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class TasksService:
    """Façade over living plan, decisions, dynamic queue summaries."""

    def __init__(self, project_root: str | Path | None = None):
        self.root = Path(project_root).resolve() if project_root else None

    def set_root(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()

    def _root(self) -> Path:
        if not self.root:
            raise ValueError("project_root not set")
        return self.root

    def list_queue_summary(self) -> dict[str, Any]:
        """Human-oriented queue buckets: now / waiting / next / deferred."""
        buckets: dict[str, list[dict[str, Any]]] = {
            "now": [],
            "waiting": [],
            "next": [],
            "deferred": [],
            "done": [],
        }
        reasons: dict[str, str] = {}
        try:
            from intelligence.decision_queue import DecisionQueue
            from intelligence.architecture_blockers import has_architecture_blockers
            dq = DecisionQueue(path=self._root() / ".agentbus" / "decisions.json")
            for item in dq.open_items():
                buckets["waiting"].append({
                    "id": item.id,
                    "title": item.title or item.question[:80],
                    "kind": "decision",
                    "status": item.status,
                })
                reasons[item.id] = "WAITING_DECISION"
            if has_architecture_blockers(dq):
                reasons["_arch"] = "architecture_blocker"
        except Exception:
            pass

        try:
            from intelligence.living_plan import LivingPlan, is_active, normalize_status
            import json
            plan_path = self._root() / ".agentbus" / "living_plan.json"
            if plan_path.is_file():
                plan = LivingPlan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
                for s in plan.steps or []:
                    st = normalize_status(getattr(s, "status", ""))
                    meta = getattr(s, "meta", {})
                    meta = meta if isinstance(meta, dict) else {}
                    row = {
                        "id": getattr(s, "id", ""),
                        "task_id": str(meta.get("task_id") or ""),
                        "title": getattr(s, "action", "")[:100],
                        "kind": "plan_step",
                        "status": st,
                    }
                    if st in ("DONE", "COMPLETED"):
                        buckets["done"].append(row)
                    elif st in ("IN_PROGRESS", "PROCESSING", "CLAIMED", "RUNNING"):
                        buckets["now"].append(row)
                    elif st in ("BLOCKED", "WAITING"):
                        buckets["waiting"].append(row)
                    elif st in ("DEFERRED", "NIGHT"):
                        buckets["deferred"].append(row)
                    elif is_active(st):
                        buckets["next"].append(row)
        except Exception:
            pass

        return {"buckets": buckets, "reasons": reasons}

    def open_decisions(self) -> list[dict[str, Any]]:
        try:
            from intelligence.decision_queue import DecisionQueue
            dq = DecisionQueue(path=self._root() / ".agentbus" / "decisions.json")
            out = []
            for i in dq.open_items():
                out.append({
                    "id": i.id,
                    "title": i.title,
                    "question": i.question,
                    "options": [o.to_dict() for o in (i.options or [])],
                    "risk": i.risk,
                })
            return out
        except Exception:
            return []

    def resolve_decision(self, decision_id: str, option_id: str) -> dict[str, Any]:
        from intelligence.architecture_interview import apply_architecture_answer
        from intelligence.decision_queue import DecisionQueue
        from intelligence.project_state import load_project_state, save_project_state
        root = self._root()
        dq = DecisionQueue(path=root / ".agentbus" / "decisions.json")
        state = load_project_state(root)
        result = apply_architecture_answer(dq, decision_id, option_id, state=state)
        if result.get("ok"):
            save_project_state(root, state)
        return result

    def supervisor_status(self) -> dict[str, Any]:
        try:
            from intelligence.smart_waiting import evaluate_wait
            from intelligence.decision_queue import DecisionQueue
            dq = DecisionQueue(path=self._root() / ".agentbus" / "decisions.json")
            wait = evaluate_wait(decisions=dq, project=str(self._root()), check_night=True)
            reason = getattr(wait, "reason", "ready")
            labels = {
                "ready": "Готов",
                "waiting_decision": "Ждёт решения",
                "architecture_blocker": "Архитектурный стопор",
                "policy_ask": "Нужно подтверждение",
                "policy_block": "Блок политики",
                "defer_to_night": "Ночной режим",
                "manual_pause": "Пауза",
            }
            return {
                "reason": reason,
                "label": labels.get(reason, reason),
                "can_emit": getattr(wait, "can_emit", True),
                "detail": getattr(wait, "detail", "") or "",
            }
        except Exception as exp:
            return {"reason": "unknown", "label": str(exp)[:80], "can_emit": True, "detail": ""}


    def find_task_row(self, task_id: str) -> dict[str, Any] | None:
        """Locate task JSON on bus / desktop_queue / done / errors (best-effort)."""
        tid = (task_id or "").strip()
        if not tid:
            return None
        candidates: list[Path] = []
        try:
            from safety.security import validate_task_id
            tid = validate_task_id(tid)
        except Exception:
            return None
        bases: list[Path] = [self.root] if self.root else []
        try:
            from core.config import BUS_ROOT
            bases.append(Path(BUS_ROOT))
        except Exception:
            pass
        try:
            from ui.paths import agentbus_root
            bases.append(Path(agentbus_root()))
        except Exception:
            if not bases:
                bases.append(Path.cwd())
        seen_bases: set[str] = set()
        for base in bases:
            try:
                key = str(base.resolve())
            except OSError:
                key = str(base)
            if key in seen_bases:
                continue
            seen_bases.add(key)
            dq = base / ".agentbus" / "desktop_queue"
            if dq.is_dir():
                candidates.extend(dq.glob(f"*{tid}*.json"))
            channels = base / "channels"
            if channels.is_dir():
                for state in ("incoming", "processing", "deferred", "done", "errors"):
                    for f in channels.glob(f"*/{state}/*{tid}*.json"):
                        if f.name.endswith(".lease.json"):
                            continue
                        candidates.append(f)
        # exact id match preferred
        for f in candidates:
            try:
                import json
                data = json.loads(f.read_text(encoding="utf-8"))
                if str(data.get("id") or "") == tid or tid in f.stem:
                    data["_path"] = str(f)
                    data["_state"] = f.parent.name
                    return data
            except Exception:
                continue
        return None

    def get_task_detail(self, task_id: str) -> dict[str, Any]:
        """Unified Task Detail card data (status, phases, files, trace, verify)."""
        row = self.find_task_row(task_id) or {"id": task_id}
        detail: dict[str, Any] = {
            "id": str(row.get("id") or task_id),
            "status": str(row.get("status") or row.get("_state") or "UNKNOWN").upper(),
            "message": str(row.get("message") or "")[:500],
            "project": str(row.get("project") or ""),
            "channel": str(row.get("channel") or row.get("_channel") or ""),
            "files": list(row.get("files") or [])[:30],
            "phases": [],
            "trace": [],
            "verification": {},
            "result_summary": "",
            "error": "",
            "worker": "",
            "skill": "",
            "changes": {},
            "human": "",
        }
        try:
            from core.task_result import build_task_result, history_detail_text, history_card_lines
            tr = build_task_result(row)
            detail["status"] = (tr.status or detail["status"]).upper()
            detail["worker"] = tr.worker or ""
            detail["skill"] = tr.skill or ""
            detail["result_summary"] = (tr.summary or "")[:400]
            detail["error"] = (tr.error or "")[:400]
            detail["trace"] = list(tr.timeline or [])[:20]
            if isinstance(tr.verification, dict):
                detail["verification"] = tr.verification
            if tr.changes:
                try:
                    detail["changes"] = tr.changes.to_dict() if hasattr(tr.changes, "to_dict") else dict(tr.changes)
                except Exception:
                    pass
            if not detail["files"] and getattr(tr, "files", None):
                detail["files"] = list(tr.files or [])[:30]
            detail["human"] = history_detail_text(row)
            card = history_card_lines(row)
            detail["card"] = card
        except Exception as exp:
            detail["error"] = detail["error"] or str(exp)[:200]

        # Phases from metadata / result
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        phases = meta.get("phases") or meta.get("pipeline_phases") or []
        if isinstance(phases, list) and phases:
            detail["phases"] = [str(p) for p in phases][:12]
        elif detail["trace"]:
            # derive simple phase marks from trace labels
            detail["phases"] = [str(x) for x in detail["trace"][:8]]

        # Memory trace file
        if not detail["trace"]:
            try:
                from utils.task_trace import GLOBAL_TRACES, event_label
                trc = GLOBAL_TRACES.get(str(detail["id"])) if hasattr(GLOBAL_TRACES, "get") else None
                if trc is None and isinstance(GLOBAL_TRACES, dict):
                    trc = GLOBAL_TRACES.get(str(detail["id"]))
                if trc is not None:
                    events = getattr(trc, "events", []) or []
                    detail["trace"] = [
                        event_label(getattr(e, "name", str(e))) for e in events
                    ][:20]
                    if getattr(trc, "final_status", None):
                        detail["status"] = str(trc.final_status).upper()
            except Exception:
                pass
        return detail

    def format_detail_text(self, task_id: str) -> str:
        d = self.get_task_detail(task_id)
        if d.get("human"):
            return d["human"]
        lines = [
            f"TASK {d.get('id')}",
            f"STATUS  {d.get('status')}",
        ]
        if d.get("message"):
            lines.append(f"PROMPT  {d['message'][:200]}")
        if d.get("worker") or d.get("skill"):
            lines.append(f"WORKER  {d.get('worker') or ''} {('skill:'+d['skill']) if d.get('skill') else ''}".strip())
        if d.get("phases"):
            lines.append("PHASES")
            for ph in d["phases"]:
                lines.append(f"  · {ph}")
        if d.get("files"):
            lines.append("FILES")
            for f in d["files"]:
                lines.append(f"  · {f}")
        if d.get("trace"):
            lines.append("TRACE")
            lines.append("  " + " → ".join(str(x) for x in d["trace"][:10]))
        if d.get("verification"):
            lines.append(f"VERIFY  {d['verification']}")
        if d.get("result_summary"):
            lines.append(f"RESULT  {d['result_summary']}")
        if d.get("error"):
            lines.append(f"ERROR   {d['error']}")
        return "\n".join(lines)

    def runtime_feedback(self, task_id: str | None = None) -> dict[str, Any]:
        """FC-45E: compact run status for UI (phase, worker, verify, outcome).

        Read-only aggregation — does not drive Runtime.
        """
        out: dict[str, Any] = {
            "task_id": task_id or "",
            "status": "idle",
            "phase": "",
            "worker": "",
            "verify": "",
            "label": "Готов",
            "detail": "",
            "supervisor": {},
        }
        try:
            out["supervisor"] = self.supervisor_status()
        except Exception:
            pass
        # queue headline
        try:
            q = self.list_queue_summary()
            buckets = q.get("buckets") if isinstance(q.get("buckets"), dict) else {}
            running = buckets.get("now") or []
            if isinstance(running, int):
                out["queue_running"] = running
            elif isinstance(running, list) and running:
                out["queue_running"] = len(running)
                if not task_id and isinstance(running[0], dict):
                    task_id = str(running[0].get("task_id") or "")
                    out["task_id"] = task_id
                if not task_id:
                    out["status"] = "processing"
            else:
                out["queue_running"] = 0
        except Exception:
            out["queue_running"] = 0
        if task_id:
            try:
                d = self.get_task_detail(task_id)
                out["status"] = str(d.get("status") or d.get("state") or "unknown")
                out["phase"] = str(d.get("phase") or "")
                if not out["phase"] and d.get("phases"):
                    ph = d["phases"]
                    out["phase"] = str(ph[-1] if isinstance(ph, list) and ph else ph)
                out["worker"] = str(d.get("worker") or d.get("worker_id") or "")
                v = d.get("verification") or d.get("verify") or {}
                if isinstance(v, dict):
                    out["verify"] = str(v.get("status") or v.get("result") or v.get("level") or "")
                else:
                    out["verify"] = str(v or "")
                out["detail"] = str(d.get("summary") or d.get("result_summary") or "")[:300]
            except Exception as exp:
                out["detail"] = str(exp)[:200]
        # human label
        st = (out["status"] or "").lower()
        if st in ("done", "success"):
            out["label"] = "Готово"
        elif st in ("error", "failed", "errors"):
            out["label"] = "Ошибка"
        elif st in ("processing", "running", "claimed", "verifying"):
            out["label"] = f"В работе: {out['phase'] or st}"
        elif st in ("deferred",):
            out["label"] = "Отложено"
        elif out.get("supervisor", {}).get("reason") not in (None, "ready", "unknown"):
            out["label"] = out["supervisor"].get("label") or out["status"]
        else:
            out["label"] = "Готов" if not out.get("queue_running") else "Очередь"
        return out

    def format_runtime_feedback(self, task_id: str | None = None) -> str:
        f = self.runtime_feedback(task_id)
        parts = [f"● {f.get('label')}"]
        if f.get("task_id"):
            parts.append(f"task={str(f['task_id'])[:12]}")
        if f.get("worker"):
            parts.append(f"worker={f['worker']}")
        if f.get("verify"):
            parts.append(f"verify={f['verify']}")
        if f.get("detail"):
            parts.append(str(f["detail"])[:120])
        return " | ".join(parts)
