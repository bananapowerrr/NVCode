# -*- coding: utf-8 -*-
"""Event envelope helpers (Protocol v0)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def make_envelope(event_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "v": 1,
        "type": event_type,
        "ts": utc_now_iso(),
        "payload": dict(payload or {}),
    }
    env.update(extra)
    return env


def map_agent_event(ev: Any) -> dict[str, Any] | None:
    """Map eventbus.AgentEvent → protocol envelope. Returns None to drop."""
    try:
        et = str(getattr(ev, "type", "") or "")
        tid = str(getattr(ev, "task_id", "") or "")
        worker = str(getattr(ev, "worker", "") or getattr(ev, "executor", "") or "")
        msg = str(getattr(ev, "message", "") or "")
        payload = getattr(ev, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        return None

    upper = et.upper()
    if upper in ("DONE", "TASK_DONE"):
        return make_envelope(
            "task.terminal",
            {
                "task_id": tid,
                "status": "DONE",
                "worker_id": worker or None,
                "verified": payload.get("verified"),
                "changed_files": payload.get("changed_files") or [],
                "message": msg[:500],
            },
        )
    if upper in ("ERROR", "TASK_ERROR", "TIMEOUT"):
        return make_envelope(
            "task.terminal",
            {
                "task_id": tid,
                "status": "ERROR",
                "worker_id": worker or None,
                "reason": upper,
                "message": msg[:500],
            },
        )
    if upper in ("DEFERRED",):
        return make_envelope(
            "task.terminal",
            {"task_id": tid, "status": "DEFERRED", "worker_id": worker or None, "message": msg[:500]},
        )
    if upper in ("CLAIM", "TASK_CLAIMED", "START", "TASK_STARTED", "READY", "RETRY", "RECLAIM"):
        status_map = {
            "CLAIM": "CLAIMED",
            "TASK_CLAIMED": "CLAIMED",
            "START": "PROCESSING",
            "TASK_STARTED": "PROCESSING",
            "READY": "PROCESSING",
            "RETRY": "PENDING",
            "RECLAIM": "PENDING",
        }
        return make_envelope(
            "task.status",
            {
                "task_id": tid,
                "to": status_map.get(upper, upper),
                "worker_id": worker or None,
                "raw_type": upper,
            },
        )
    if upper in ("TASK_CREATED",):
        return make_envelope("task.created", {"task_id": tid, "message": msg[:200]})
    if upper in ("WORKER_BUSY",):
        return make_envelope("worker.busy", {"worker_id": worker or payload.get("worker")})
    if upper in ("WORKER_READY",):
        return make_envelope("worker.ready", {"worker_id": worker or payload.get("worker")})
    if upper in ("HEARTBEAT",):
        return make_envelope("runtime.health", {"ok": True, "message": msg[:200]})
    if upper in ("MESSAGE", "THINKING", "PULSE", "COMMAND"):
        return make_envelope(
            "log.line",
            {"level": "info", "text": msg[:2000], "task_id": tid, "worker_id": worker or None},
        )
    return make_envelope(
        "log.line",
        {"level": "debug", "text": f"{upper}: {msg}"[:2000], "task_id": tid, "raw_type": upper},
    )
