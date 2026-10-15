# -*- coding: utf-8 -*-
"""Task cancellation for Protocol v0 (FileBus only; no FSM/reclaim changes)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def cancel_task(task_id: str, *, reason: str = "") -> tuple[int, dict[str, Any]]:
    """Cancel a non-terminal task via FileBus move.

    Allowed: incoming, processing, deferred.
    Terminal done/errors → 409. Missing → 404.
    """
    from protocol import rest_handlers as rh

    tid = (task_id or "").strip()
    if not tid:
        return 400, {"ok": False, "error": {"code": "BAD_REQUEST", "message": "empty task_id"}}
    filename = f"{tid}.json"
    cancellable = ("incoming", "processing", "deferred")
    terminal = ("done", "errors")
    found: tuple[str, str, Path] | None = None
    for ch, st, path in rh._iter_task_files():
        if path.stem == tid or path.name == filename:
            found = (ch, st, path)
            break
    if not found:
        return 404, {"ok": False, "error": {"code": "NOT_FOUND", "message": f"task {tid} not found"}}
    channel, state, path = found
    if state in terminal:
        return 409, {
            "ok": False,
            "error": {"code": "ALREADY_TERMINAL", "message": f"task is already in {state}"},
            "task_id": tid,
            "folder": state,
        }
    if state not in cancellable:
        return 409, {
            "ok": False,
            "error": {"code": "NOT_CANCELLABLE", "message": f"folder {state} cannot be cancelled"},
            "task_id": tid,
            "folder": state,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data["status"] = "CANCELLED"
    data["cancelled"] = True
    data["cancel_reason"] = (reason or "user cancel via protocol")[:500]
    try:
        from datetime import datetime, timezone
        data["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return 500, {"ok": False, "error": {"code": "WRITE_FAILED", "message": str(e)[:200]}}

    lease = path.with_name(path.stem + ".lease.json")
    if lease.is_file():
        try:
            lease.unlink()
        except Exception:
            pass

    target = "deferred"
    try:
        from core.bus import FileBus
        try:
            from core.config import CHANNELS
            chs = tuple(CHANNELS) if CHANNELS else ("desktop",)
        except Exception:
            chs = ("desktop",)
        if "desktop" not in chs:
            chs = chs + ("desktop",)
        bus = FileBus(rh._project_root(), chs)
        moved = bus.move(channel, state, target, filename)
        if not moved and state != target:
            dst = rh._channels_root(rh._project_root()) / channel / target / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                path.replace(dst)
                moved = True
        if not moved:
            return 409, {
                "ok": False,
                "error": {"code": "RACE", "message": "task moved by another process"},
                "task_id": tid,
            }
    except Exception as e:
        return 500, {"ok": False, "error": {"code": "MOVE_FAILED", "message": str(e)[:300]}}

    try:
        from protocol.event_bridge import BRIDGE
        from protocol.envelope import make_envelope
        BRIDGE.broadcast(
            make_envelope(
                "task.terminal",
                {
                    "task_id": tid,
                    "status": "CANCELLED",
                    "folder": target,
                    "channel": channel,
                    "reason": data.get("cancel_reason"),
                },
            )
        )
    except Exception:
        pass

    return 200, {
        "ok": True,
        "task_id": tid,
        "status": "CANCELLED",
        "folder": target,
        "channel": channel,
        "from_folder": state,
    }
