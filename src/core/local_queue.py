# -*- coding: utf-8 -*
"""Desktop primary task queue (in-process + optional spill file).

Chat UI is the main channel. File-bus (channels/) is optional for phone/remote.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any


def _reject_unsafe_files(data: dict[str, Any]) -> None:
    """Fail-closed intake: the desktop queue must never carry unsafe paths."""
    files = data.get("files")
    if not files:
        return
    from safety.security import SecurityError, validate_path

    for item in files:
        try:
            validate_path(item)
        except SecurityError as exc:
            raise ValueError(f"reject unsafe path in files: {item!r} ({exc})") from exc


class LocalQueue:
    """Thread-safe FIFO of task dicts for the desktop agent."""

    def __init__(self, spill_dir: Path | None = None) -> None:
        self._q: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._spill = spill_dir
        if self._spill is not None:
            self._spill.mkdir(parents=True, exist_ok=True)

    def put(self, task: dict[str, Any]) -> str:
        data = dict(task)
        _reject_unsafe_files(data)
        tid = str(data.get("id") or f"ui-{uuid.uuid4().hex[:10]}")
        data["id"] = tid
        data.setdefault("status", "PENDING")
        data.setdefault("channel", "desktop")
        supplied_meta = data.get("metadata")
        if supplied_meta is None:
            meta = {}
        elif isinstance(supplied_meta, dict):
            meta = dict(supplied_meta)
        else:
            raise ValueError("metadata must be an object")
        meta.setdefault("source", "desktop_chat")
        meta.setdefault("primary_channel", "desktop")
        data["metadata"] = meta
        from core.task_contract import normalize_task

        # LocalQueue is a public ingress too; never rely on callers to have
        # passed through TaskService before a task becomes persistent.
        data.update(normalize_task(data))
        with self._lock:
            if self._spill is not None:
                path = self._spill / f"{tid}.json"
                tmp = path.with_suffix(path.suffix + ".tmp")
                try:
                    with open(tmp, "w", encoding="utf-8", newline="") as fh:
                        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, path)
                except OSError:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
            self._q.append(data)
        return tid

    def claim(self) -> dict[str, Any] | None:
        with self._lock:
            if self._q:
                data = self._q.popleft()
                # drop spill twin so second claim is not a duplicate
                if self._spill is not None:
                    try:
                        (self._spill / f"{data.get('id')}.json").unlink(missing_ok=True)
                    except OSError:
                        pass
                return data
            # recover spill (UI process → dispatcher process)
            if self._spill is not None and self._spill.is_dir():
                files = sorted(
                    (
                        p for p in self._spill.glob("*.json")
                        if not p.name.endswith(".claiming.json")
                        and not p.name.endswith(".tmp")
                    ),
                    key=lambda p: p.stat().st_mtime,
                )
                for path in files:
                    # TD-026: claim by atomic rename first, then read.
                    # Two processes cannot both rename the same file.
                    claimed = path.with_name(path.stem + ".claiming.json")
                    try:
                        path.replace(claimed)
                    except FileNotFoundError:
                        continue
                    except OSError as exp:
                        try:
                            import logging
                            logging.getLogger("agentbus.queue").warning(
                                "spill claim rename %s: %s", path.name, exp
                            )
                        except Exception:
                            pass
                        continue
                    try:
                        data = json.loads(claimed.read_text(encoding="utf-8"))
                        claimed.unlink(missing_ok=True)
                        if isinstance(data, dict):
                            return data
                    except Exception as exp:
                        try:
                            import logging
                            logging.getLogger("agentbus.queue").warning(
                                "spill claim skip %s: %s", claimed.name, exp
                            )
                        except Exception:
                            pass
                        try:
                            claimed.unlink(missing_ok=True)
                        except OSError:
                            pass
            return None

    def size(self) -> int:
        with self._lock:
            n = len(self._q)
            if self._spill is not None and self._spill.is_dir():
                n += sum(
                    1 for p in self._spill.glob("*.json")
                    if not p.name.endswith(".claiming.json")
                    and not p.name.endswith(".tmp")
                )
            return n


_GLOBAL: LocalQueue | None = None
_BY_ROOT: dict[str, LocalQueue] = {}
_GLOBAL_LOCK = threading.Lock()


def _root_key(root: Path | str | None) -> str:
    if root is None:
        return ""
    try:
        return str(Path(root).resolve())
    except OSError:
        return str(root)


def _new_queue(root: Path | None) -> LocalQueue:
    spill = None
    try:
        if root is None:
            from core.config import BASE_DIR
            root = Path(BASE_DIR)
        # desktop queue on disk so UI process → dispatcher process works
        spill = Path(root) / ".agentbus" / "desktop_queue"
    except Exception:
        spill = Path(".agentbus") / "desktop_queue"
    return LocalQueue(spill_dir=spill)


def reset_local_queue(root: Path | str | None = None) -> None:
    """Test/helper: drop queue instance(s). Without root, drop all."""
    global _GLOBAL
    key = _root_key(root)
    with _GLOBAL_LOCK:
        if key == "":
            _GLOBAL = None
            _BY_ROOT.clear()
            return
        _BY_ROOT.pop(key, None)


def get_local_queue(root: Path | None = None) -> LocalQueue:
    """Per-root queue instance: projects must not share a desktop queue."""
    global _GLOBAL
    key = _root_key(root)
    with _GLOBAL_LOCK:
        if key == "":
            if _GLOBAL is None:
                _GLOBAL = _new_queue(None)
            return _GLOBAL
        q = _BY_ROOT.get(key)
        if q is None:
            q = _new_queue(Path(root))
            _BY_ROOT[key] = q
        return q


def enqueue_desktop_task(
    message: str,
    *,
    project: str = "",
    files: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    root: Path | None = None,
) -> str:
    """API for ChatPanel / dashboard desktop path."""
    q = get_local_queue(root)
    task = {
        "id": f"ui-{uuid.uuid4().hex[:10]}",
        "project": project,
        "message": message,
        "files": list(files or []),
        "channel": "desktop",
        "status": "PENDING",
        "metadata": dict(metadata or {}),
        "created_at": time.time(),
    }
    return q.put(task)
