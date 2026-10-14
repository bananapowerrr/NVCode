# -*- coding: utf-8 -*-
"""Unified TaskService — single intake for UI / recipes / CLI / resend (FC-09).

Wraps intake_pipeline + local_queue. Phone file-bus mirror is optional.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _resolve_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    try:
        env = os.getenv("AGENTBUS_ROOT")
        if env:
            return Path(env)
    except Exception:
        pass
    try:
        from core.config import PROJECT_ROOT, BASE_DIR

        if PROJECT_ROOT:
            return Path(PROJECT_ROOT)
        if BASE_DIR:
            return Path(BASE_DIR)
    except Exception:
        pass
    return Path(".")


def submit(
    message: str,
    *,
    project: str = "",
    files: list[str] | None = None,
    source: str = "task_service",
    root: Path | None = None,
    extra: dict[str, Any] | None = None,
    mirror_phone: bool = False,
    phone_channel: str = "gpt",
) -> tuple[str | None, str | None]:
    """Enqueue a task for the desktop product path.

    Returns:
        (task_id, None) on success or (None, error_message).
    """
    raw: dict[str, Any] = {
        "message": (message or "").strip(),
        "project": project or "",
        "files": list(files or []),
        "channel": "desktop",
        "status": "PENDING",
    }
    if extra:
        meta = dict(extra.get("metadata") or {})
        meta.setdefault("intake_source", source)
        raw["metadata"] = meta
        for k, v in extra.items():
            if k != "metadata" and k not in raw:
                raw[k] = v
    else:
        raw["metadata"] = {"intake_source": source}

    if not raw["message"]:
        return None, "empty message"

    return submit_payload(raw, source=source, root=root, mirror_phone=mirror_phone, phone_channel=phone_channel)


def submit_payload(
    payload: dict[str, Any],
    *,
    source: str = "task_service",
    root: Path | None = None,
    mirror_phone: bool = False,
    phone_channel: str = "gpt",
    soft_intake: bool = True,
) -> tuple[str | None, str | None]:
    """Canonical enqueue from a full task dict (chat / resend / recipe / dashboard).

    - Runs intake validation when available.
    - Always writes to desktop LocalQueue (primary product path).
    - Optionally mirrors to channels/<phone_channel>/incoming when mirror_phone.
    """
    if not isinstance(payload, dict):
        return None, "payload must be a dict"

    raw = dict(payload)
    raw.setdefault("channel", "desktop")
    raw.setdefault("status", "PENDING")
    supplied_meta = raw.get("metadata")
    if supplied_meta is None:
        meta: Any = {}
    elif isinstance(supplied_meta, dict):
        meta = dict(supplied_meta)
    else:
        # Preserve malformed metadata for the canonical validator to reject;
        # do not coerce or silently discard it at the queue boundary.
        meta = supplied_meta
    if isinstance(meta, dict):
        meta.setdefault("intake_source", source)
        meta.setdefault("primary_channel", "desktop")
    raw["metadata"] = meta
    raw["channel"] = "desktop"

    msg = str(raw.get("message") or "").strip()
    if not msg:
        return None, "empty message"
    raw["message"] = msg

    # Intake is always fail-closed. ``soft_intake`` is retained for API
    # compatibility; it must never authorize an invalid task into the queue.
    try:
        from core.intake_pipeline import accept_task_raw

        task = accept_task_raw(raw, source=source, strict=True)
        cleaned = task.to_dict() if hasattr(task, "to_dict") else dict(raw)
        cleaned.setdefault("id", getattr(task, "id", None) or raw.get("id"))
        raw = cleaned
    except ImportError as exc:
        return None, f"intake unavailable: {exc}"
    except Exception as exc:
        return None, f"intake: {type(exc).__name__}: {exc}"

    base = _resolve_root(root)
    try:
        from core.local_queue import get_local_queue

        tid = get_local_queue(base).put(raw if isinstance(raw, dict) else payload)
        tid = str(tid)
    except Exception as exc:
        return None, f"queue: {type(exc).__name__}: {exc}"

    # Optional phone file-bus mirror (not primary)
    if mirror_phone:
        try:
            from core.feature_flags import is_enabled

            do = is_enabled("phone_filebus", default=False) or is_enabled(
                "remote_filebus", default=False
            )
        except Exception:
            do = False
        if do:
            try:
                import json
                from core.bus import FileBus

                ch = (phone_channel or "gpt").strip() or "gpt"
                mirror = dict(raw)
                mirror["id"] = tid
                mirror["channel"] = ch
                mirror.setdefault("metadata", {})["mirrored_from"] = "desktop"
                FileBus(base, (ch,)).write(
                    ch,
                    "incoming",
                    f"{tid}.json",
                    json.dumps(mirror, ensure_ascii=False, indent=2),
                )
            except Exception:
                pass

    return tid, None


def resubmit_from_row(
    row: dict[str, Any],
    *,
    source: str = "ui-resend",
    root: Path | None = None,
) -> tuple[str | None, str | None]:
    """Resend history row through the same desktop path (FC-09 / FC-13)."""
    message = str(row.get("message") or "").strip()
    project = str(row.get("project") or "").strip()
    files = row.get("files") if isinstance(row.get("files"), list) else []
    if not message:
        return None, "empty message"
    if not project:
        return None, "empty project"
    meta = {
        "source": source,
        "resent_from": str(row.get("id") or ""),
        "intake_source": source,
        "primary_channel": "desktop",
    }
    return submit(
        message,
        project=project,
        files=list(files),
        source=source,
        root=root,
        extra={"metadata": meta},
    )
