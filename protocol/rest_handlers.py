# -*- coding: utf-8 -*-
"""Pure REST handlers for Protocol v0 (read-mostly + intake POST)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    try:
        env = os.getenv("AGENTBUS_ROOT") or os.getenv("NVCODE_ROOT")
        if env:
            return Path(env).resolve()
    except Exception:
        pass
    try:
        from core.config import BASE_DIR, PROJECT_ROOT
        if PROJECT_ROOT:
            return Path(PROJECT_ROOT).resolve()
        if BASE_DIR:
            return Path(BASE_DIR).resolve()
    except Exception:
        pass
    return Path(".").resolve()


def _channels_root(root: Path) -> Path:
    return root / "channels"


def health() -> dict[str, Any]:
    try:
        from protocol import __version__, PROTOCOL_VERSION
    except Exception:
        __version__, PROTOCOL_VERSION = "0.1.0", 1
    return {
        "ok": True,
        "service": "nvcode-protocol",
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
        "bind": "localhost",
    }


def status() -> dict[str, Any]:
    """Backend snapshot. Existing fields kept; additive `diagnostics` block."""
    root = _project_root()
    data: dict[str, Any] = {"ok": True, "root": str(root), "queues": {}, "policy": {}, "runtimes": []}
    try:
        from core.local_queue import get_local_queue
        data["desktop_queue"] = int(get_local_queue(root).size())
        data["queues"]["desktop"] = data["desktop_queue"]
    except Exception as e:
        data["desktop_queue"] = 0
        data["queues_error"] = str(e)[:200]
    ch_root = _channels_root(root)
    totals = {"incoming": 0, "processing": 0, "done": 0, "errors": 0, "deferred": 0, "quarantine": 0}
    if ch_root.is_dir():
        for ch in sorted(p.name for p in ch_root.iterdir() if p.is_dir()):
            counts: dict[str, int] = {}
            for name in ("incoming", "processing", "done", "errors", "deferred", "quarantine"):
                d = ch_root / ch / name
                if d.is_dir():
                    n = sum(1 for f in d.glob("*.json") if not f.name.endswith(".lease.json"))
                else:
                    n = 0
                counts[name] = n
                totals[name] = totals.get(name, 0) + n
            data["queues"][ch] = counts
    try:
        from core.policy import load_policy
        p = load_policy()
        data["policy"] = {
            "name": getattr(p, "name", None),
            "allow_cloud": getattr(p, "allow_cloud", None),
            "prefer_local": getattr(p, "prefer_local", None),
        }
    except Exception:
        pass

    leases = list_leases()
    workers = list_workers()
    lease_count = int(leases.get("count") or 0)
    worker_count = int(workers.get("count") or 0)
    active = int(totals.get("incoming", 0)) + int(totals.get("processing", 0))
    data["diagnostics"] = {
        "active_tasks": active,
        "incoming": int(totals.get("incoming", 0)),
        "processing": int(totals.get("processing", 0)),
        "done": int(totals.get("done", 0)),
        "errors": int(totals.get("errors", 0)),
        "deferred": int(totals.get("deferred", 0)),
        "quarantine": int(totals.get("quarantine", 0)),
        "leases": lease_count,
        "workers_busy": worker_count,
        "lease_store": "processing/*.lease.json",
        "protocol": "v0",
    }
    return data


def _iter_task_files(state: str | None = None, channel: str | None = None):
    root = _channels_root(_project_root())
    if not root.is_dir():
        return []
    states = (state,) if state else ("incoming", "processing", "done", "errors", "deferred")
    channels = (channel,) if channel else tuple(p.name for p in root.iterdir() if p.is_dir())
    out = []
    for ch in channels:
        for st in states:
            d = root / ch / st
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                if p.name.endswith(".lease.json"):
                    continue
                out.append((ch, st, p))
    return out


def _task_summary(ch: str, st: str, path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    tid = str(raw.get("id") or path.stem)
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    return {
        "task_id": tid,
        "channel": ch,
        "status": str(raw.get("status") or st.upper()),
        "folder": st,
        "worker_id": raw.get("worker") or raw.get("executor"),
        "attempts": raw.get("attempts"),
        "message": str(raw.get("message") or "")[:200],
        "verified": result.get("verified") if result else raw.get("verified"),
        "changed_files": result.get("changed_files") if result else raw.get("changed_files"),
    }


def list_tasks(state: str | None = None, channel: str | None = None, limit: int = 100) -> dict[str, Any]:
    items = []
    for ch, st, path in _iter_task_files(state=state, channel=channel):
        items.append(_task_summary(ch, st, path))
        if len(items) >= max(1, min(limit, 500)):
            break
    return {"ok": True, "tasks": items, "count": len(items)}


def get_task(task_id: str) -> dict[str, Any]:
    for ch, st, path in _iter_task_files():
        if path.stem == task_id or path.name.startswith(task_id):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                return {"ok": False, "error": {"code": "INTERNAL", "message": str(e)}}
            return {"ok": True, "task": _task_summary(ch, st, path), "raw": raw, "lease": _read_lease(path)}
    return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"task {task_id} not found"}}


def create_task(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST tasks — only via core.task_service intake (fail-closed)."""
    message = str(body.get("message") or "").strip()
    if not message:
        return 400, {"ok": False, "error": {"code": "INTAKE_REJECTED", "message": "empty message"}}
    project = str(body.get("project") or "")
    channel = str(body.get("channel") or "desktop")
    extra: dict[str, Any] = {}
    if body.get("metadata"):
        extra["metadata"] = dict(body["metadata"])
    if body.get("recipe"):
        extra.setdefault("metadata", {})["recipe"] = body["recipe"]
    extra.setdefault("metadata", {})["intake_source"] = "protocol_v0"
    extra.setdefault("metadata", {})["source"] = "protocol_v0"
    if channel and channel != "desktop":
        extra["channel"] = channel
    try:
        from core.task_service import submit
        tid, err = submit(
            message, project=project, source="protocol_v0", root=_project_root(), extra=extra or None
        )
    except Exception as e:
        return 500, {"ok": False, "error": {"code": "INTERNAL", "message": str(e)[:300]}}
    if err or not tid:
        return 400, {"ok": False, "error": {"code": "INTAKE_REJECTED", "message": err or "queue failed"}}
    return 200, {"ok": True, "task_id": tid, "status": "PENDING"}


def _read_lease(task_path: Path) -> dict[str, Any] | None:
    lp = task_path.with_name(task_path.stem + ".lease.json")
    if not lp.is_file():
        return None
    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {
            "worker_id": data.get("worker_id") or data.get("worker"),
            "phase": data.get("phase"),
            "last_heartbeat": data.get("last_heartbeat"),
            "stuck_timeout_sec": data.get("stuck_timeout_sec"),
        }
    except Exception:
        return None


def list_leases() -> dict[str, Any]:
    items = []
    for ch, st, path in _iter_task_files(state="processing"):
        items.append({"task_id": path.stem, "channel": ch, "lease": _read_lease(path)})
    return {"ok": True, "leases": items, "count": len(items)}


def get_lease(task_id: str) -> dict[str, Any]:
    for ch, st, path in _iter_task_files(state="processing"):
        if path.stem == task_id:
            return {"ok": True, "task_id": task_id, "channel": ch, "lease": _read_lease(path)}
    for ch, st, path in _iter_task_files():
        if path.stem == task_id:
            return {"ok": True, "task_id": task_id, "channel": ch, "folder": st, "lease": _read_lease(path)}
    return {"ok": False, "error": {"code": "NOT_FOUND", "message": "lease not found"}}


def list_workers() -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for item in list_leases().get("leases") or []:
        lease = item.get("lease") or {}
        wid = lease.get("worker_id")
        if not wid:
            continue
        seen[str(wid)] = {
            "worker_id": str(wid),
            "busy_task_id": item.get("task_id"),
            "phase": lease.get("phase"),
            "last_heartbeat": lease.get("last_heartbeat"),
        }
    return {"ok": True, "workers": list(seen.values()), "count": len(seen)}


def metrics() -> dict[str, Any]:
    try:
        from utils.metrics import GLOBAL_METRICS
        snap = GLOBAL_METRICS.snapshot() if hasattr(GLOBAL_METRICS, "snapshot") else None
        if isinstance(snap, dict):
            return {"ok": True, "metrics": snap}
        counters = getattr(GLOBAL_METRICS, "counters", {}) or {}
        return {"ok": True, "metrics": {"counters": dict(counters)}}
    except Exception as e:
        return {"ok": True, "metrics": {"counters": {}}, "note": str(e)[:120]}


def logs_tail(
    n: int = 200,
    *,
    task_id: str | None = None,
    level: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    from protocol.log_filters import filter_logs_tail

    return filter_logs_tail(
        _project_root(), n=n, task_id=task_id, level=level, source=source
    )
