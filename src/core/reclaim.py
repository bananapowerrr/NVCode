# -*- coding: utf-8 -*-
"""Heartbeat / reclaim for stuck tasks in processing/.

Improves existing mtime-based recovery:
- adaptive stuck timeout from complexity, worker, PEV
- optional last_heartbeat in task metadata / sidecar lease
- attempts → requeue incoming or terminal errors
- TD-006: touch_lease CAS — only the owning worker may refresh the lease

Does NOT replace loopguard (live but spinning worker).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable
import sys

# Defaults; runtime/config may override via kwargs
BASE_STUCK_SEC = max(60, int(os.getenv("AGENTBUS_STUCK_BASE_SEC", "300") or "300"))
STUCK_TIMEOUT_MAX = max(BASE_STUCK_SEC, int(os.getenv("AGENTBUS_STUCK_TIMEOUT_MAX", "1800") or "1800"))


def _soft_log(where: str, err: BaseException) -> None:
    """Non-fatal reclaim errors — never silent."""
    try:
        import logging

        logging.getLogger("agentbus.reclaim").warning(
            "reclaim.%s: %s: %s", where, type(err).__name__, err
        )
    except Exception:
        try:
            sys.stderr.write(f"[agentbus.reclaim] {where}: {type(err).__name__}: {err}\n")
        except Exception:
            pass


def _atomic_write_json(path: Path, data: dict[str, Any]) -> bool:
    """tmp + os.replace; never leave a partial task/lease JSON."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as e:
        _soft_log(f"atomic_write:{path.name}", e)
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return False


_COMPLEXITY_FACTOR: dict[int, float] = {
    1: 1.0,
    2: 1.0,
    3: 1.5,
    4: 2.5,
    5: 3.0,
}

_WORKER_HINTS: tuple[tuple[str, float], ...] = (
    ("opencode", 2.0),
    ("big", 1.8),
    ("cloud", 1.5),
    ("silicon", 1.4),
    ("openrouter", 1.4),
    ("aider", 1.2),
    ("ollama", 1.0),
    ("local", 1.0),
    ("skill", 0.6),
    ("cache", 0.5),
)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def task_complexity(raw: dict[str, Any] | None) -> int:
    """Resolve complexity 1–5 from task fields / metadata."""
    if not isinstance(raw, dict):
        return 2
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    for key in ("complexity", "stage", "complexity_stage"):
        for src in (raw, meta):
            if key in src:
                c = _as_int(src.get(key), 0)
                if 1 <= c <= 5:
                    return c
    files = raw.get("files") or meta.get("files") or []
    n_files = len(files) if isinstance(files, list) else 0
    msg = str(raw.get("message") or "")
    if n_files >= 5 or len(msg) > 800:
        return 4
    if n_files >= 2 or len(msg) > 300:
        return 3
    return 2


def worker_factor(worker: str | None) -> float:
    name = (worker or "").strip().lower()
    if not name:
        return 1.0
    for hint, factor in _WORKER_HINTS:
        if hint in name:
            return factor
    return 1.0


def compute_stuck_timeout_sec(
    raw: dict[str, Any] | None = None,
    *,
    base_sec: float | None = None,
    max_sec: float | None = None,
    worker: str | None = None,
    complexity: int | None = None,
) -> float:
    """Adaptive lease length for one task (delegates to timeout_policy)."""
    try:
        from core.timeout_policy import stuck_timeout_sec

        return float(
            stuck_timeout_sec(
                raw,
                base_sec=base_sec if base_sec is not None else BASE_STUCK_SEC,
                max_sec=max_sec if max_sec is not None else STUCK_TIMEOUT_MAX,
                worker=worker,
                complexity=complexity,
            )
        )
    except Exception:
        pass
    base = float(base_sec if base_sec is not None else BASE_STUCK_SEC)
    cap = float(max_sec if max_sec is not None else STUCK_TIMEOUT_MAX)
    raw = raw if isinstance(raw, dict) else {}
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    explicit = meta.get("stuck_timeout_sec") or raw.get("stuck_timeout_sec")
    if explicit is not None:
        return max(60.0, min(cap, _as_float(explicit, base)))
    c = complexity if complexity is not None else task_complexity(raw)
    c = max(1, min(5, int(c)))
    factor = _COMPLEXITY_FACTOR.get(c, 1.5)
    w = worker or str(raw.get("worker") or meta.get("worker") or raw.get("executor") or "")
    factor *= worker_factor(w)
    pev = bool(meta.get("pev") or meta.get("pev_plan") or raw.get("pev"))
    bonus = 180.0 if pev else 0.0
    if meta.get("multi_step") or meta.get("subtasks"):
        bonus += 120.0
    timeout = base * factor + bonus
    return max(60.0, min(cap, timeout))


def last_active_epoch(path: Path, raw: dict[str, Any] | None = None) -> float:
    """Best-effort last activity: metadata heartbeat, lease sidecar, else mtime."""
    candidates: list[float] = []
    try:
        candidates.append(path.stat().st_mtime)
    except OSError as e:
        _soft_log("stat_mtime", e)

    if isinstance(raw, dict):
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        for key in ("last_heartbeat", "claimed_at", "updated_at_epoch"):
            for src in (meta, raw):
                if key in src:
                    candidates.append(_as_float(src.get(key), 0.0))
        updated = meta.get("updated_at")
        if isinstance(updated, str) and len(updated) >= 19:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                candidates.append(dt.timestamp())
            except Exception as e:
                _soft_log("parse_updated_at", e)

    lease = path.with_suffix(path.suffix + ".lease") if path.suffix else path.with_name(path.name + ".lease")
    lease_json = path.with_name(path.stem + ".lease.json")
    for lf in (lease_json, lease):
        if not lf.is_file():
            continue
        try:
            candidates.append(lf.stat().st_mtime)
            data = json.loads(lf.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "last_heartbeat" in data:
                candidates.append(_as_float(data.get("last_heartbeat"), 0.0))
        except Exception as e:
            _soft_log("read_lease", e)

    positive = [x for x in candidates if x > 0]
    return max(positive) if positive else 0.0


def write_lease(
    processing_path: Path,
    *,
    task_id: str,
    worker: str = "",
    complexity: int = 2,
    attempts: int = 0,
    stuck_timeout_sec: float = 0.0,
    phase: str = "claim",
) -> Path | None:
    """Write/update sidecar lease next to processing task JSON.

    Claim path: always overwrites (this worker is taking ownership).
    """
    lease_path = processing_path.with_name(processing_path.stem + ".lease.json")
    now = time.time()
    payload = {
        "task_id": task_id,
        "worker_id": worker,
        "complexity": complexity,
        "attempts": attempts,
        "last_heartbeat": now,
        "stuck_timeout_sec": stuck_timeout_sec or compute_stuck_timeout_sec(
            {"worker": worker, "metadata": {"complexity": complexity}}
        ),
        "phase": phase,
    }
    if _atomic_write_json(lease_path, payload):
        return lease_path
    try:
        lease_path.touch()
        return lease_path
    except OSError:
        return None


def touch_lease(
    processing_path: Path,
    phase: str | None = None,
    *,
    expected_worker_id: str | None = None,
) -> bool:
    """Refresh heartbeat without rewriting full task body.

    TD-006 CAS: if *expected_worker_id* is set and the lease already names a
    different non-empty worker_id, refuse to touch (another owner holds it).
    Returns True if the lease was updated (or created empty).
    """
    lease_path = processing_path.with_name(processing_path.stem + ".lease.json")
    now = time.time()
    data: dict[str, Any] = {}
    if lease_path.is_file():
        try:
            loaded = json.loads(lease_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception as e:
            _soft_log("touch_lease_read", e)
            data = {}

    if expected_worker_id is not None:
        owner = str(data.get("worker_id") or "").strip()
        want = str(expected_worker_id or "").strip()
        if owner and want and owner != want:
            _soft_log(
                "touch_lease_cas",
                RuntimeError(f"lease owner={owner!r} != expected={want!r}"),
            )
            return False

    data["last_heartbeat"] = now
    if phase:
        data["phase"] = phase
    if expected_worker_id and not str(data.get("worker_id") or "").strip():
        data["worker_id"] = str(expected_worker_id)

    if _atomic_write_json(lease_path, data):
        return True
    try:
        processing_path.touch()
        return True
    except OSError:
        return False


def _attach_failure_evidence(
    raw: dict[str, Any],
    meta: dict[str, Any],
    reclaim_reason: str,
    state_folder: str,
) -> None:
    """Record failure_layer / recoverable / execution_evidence on a reclaimed task."""
    try:
        from core.execution_evidence import classify_failure_layer

        layer, recoverable = classify_failure_layer(reclaim_reason=reclaim_reason)
    except Exception as e:
        _soft_log(f"classify_failure_layer:{reclaim_reason}", e)
        layer = ("reclaim_max_attempts"
                 if reclaim_reason == "stuck_max_attempts" else "reclaim_no_heartbeat")
        recoverable = reclaim_reason != "stuck_max_attempts"
    meta["failure_layer"] = layer
    meta["recoverable"] = bool(recoverable)
    try:
        from core.execution_evidence import evidence_from_task_payload

        meta["execution_evidence"] = evidence_from_task_payload(
            {**raw, "metadata": meta},
            terminal_state="ERROR" if state_folder == "errors" else "",
            state_folder=state_folder,
        )
    except Exception as e:
        _soft_log(f"evidence_from_task_payload:{reclaim_reason}", e)


def reclaim_stuck(
    *,
    processing_dir: Path,
    incoming_dir: Path,
    errors_dir: Path,
    move_fn: Callable[[str, str, str], bool] | None = None,
    channel: str = "",
    bus_move: Callable[[str, str, str, str], bool] | None = None,
    max_attempts: int = 3,
    base_sec: float | None = None,
    max_sec: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Scan processing/ and requeue or error stuck tasks.

    Prefer bus_move(channel, from, to, filename) when provided (AgentBus FileBus).
    Else falls back to Path.replace.
    """
    results: list[dict[str, Any]] = []
    if not processing_dir.is_dir():
        return results

    ts = time.time() if now is None else now
    incoming_dir.mkdir(parents=True, exist_ok=True)
    errors_dir.mkdir(parents=True, exist_ok=True)

    for lease_path in list(processing_dir.glob("*.lease.json")):
        task_path = lease_path.with_name(lease_path.name[: -len(".lease.json")] + ".json")
        if task_path.is_file():
            continue
        try:
            lease_path.unlink()
        except OSError as e:
            _soft_log(f"orphan_lease_unlink:{lease_path.name}", e)
            continue
        results.append({
            "id": lease_path.name[: -len(".lease.json")],
            "action": "LEASE_REMOVED",
            "reason": "orphan_lease_no_task_file",
            "from": "processing",
            "to": "",
        })

    for path in list(processing_dir.glob("*.json")):
        if path.name.endswith(".lease.json"):
            continue
        raw: dict[str, Any] | None = None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except Exception as e:
            _soft_log(f"read_task:{path.name}", e)
            raw = None

        timeout = compute_stuck_timeout_sec(raw, base_sec=base_sec, max_sec=max_sec)
        active = last_active_epoch(path, raw)
        age = ts - active if active > 0 else timeout + 1
        if age <= timeout:
            continue

        attempts = 0
        if raw:
            attempts = _as_int(raw.get("attempts"), 0)
            meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            attempts = max(attempts, _as_int(meta.get("attempts"), 0))

        lease_path = path.with_name(path.stem + ".lease.json")
        if lease_path.is_file():
            try:
                ld = json.loads(lease_path.read_text(encoding="utf-8"))
                if isinstance(ld, dict):
                    attempts = max(attempts, _as_int(ld.get("attempts"), 0))
            except Exception as e:
                _soft_log(f"lease_attempts:{path.name}", e)

        to_errors = attempts >= max_attempts
        dest_state = "errors" if to_errors else "incoming"
        action = "ERROR" if to_errors else "REQUEUE"

        if raw is not None and not to_errors:
            raw["attempts"] = attempts + 1
            raw["status"] = "PENDING"
            meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            meta = dict(meta)
            meta["attempts"] = raw["attempts"]
            meta["reclaim_reason"] = "stuck_no_heartbeat"
            meta["reclaim_age_sec"] = round(age, 1)
            meta["reclaim_timeout_sec"] = round(timeout, 1)
            meta["last_heartbeat"] = ts
            _attach_failure_evidence(raw, meta, "stuck_no_heartbeat", "incoming")
            raw["metadata"] = meta
            if not _atomic_write_json(path, raw):
                _soft_log(f"write_requeue:{path.name}", OSError("atomic write failed"))

        if to_errors and raw is not None:
            meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            meta = dict(meta)
            meta["reclaim_reason"] = "stuck_max_attempts"
            meta["reclaim_age_sec"] = round(age, 1)
            raw["status"] = "ERROR"
            _attach_failure_evidence(raw, meta, "stuck_max_attempts", "errors")
            raw["metadata"] = meta
            if not _atomic_write_json(path, raw):
                _soft_log(f"write_error:{path.name}", OSError("atomic write failed"))

        moved = False
        if bus_move and channel:
            try:
                moved = bool(bus_move(channel, "processing", dest_state, path.name))
            except Exception as e:
                _soft_log(f"bus_move:{path.name}", e)
                moved = False
        if not moved:
            target_dir = errors_dir if to_errors else incoming_dir
            target = target_dir / path.name
            try:
                if target.exists():
                    target = target_dir / f"{path.stem}_{int(ts)}{path.suffix}"
                path.replace(target)
                moved = True
            except OSError as e:
                _soft_log(f"path_replace:{path.name}", e)
                moved = False

        if moved:
            try:
                if lease_path.is_file():
                    lease_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except TypeError:
                try:
                    lease_path.unlink()
                except OSError as e:
                    _soft_log(f"lease_unlink:{path.name}", e)
            except OSError as e:
                _soft_log(f"lease_unlink:{path.name}", e)

        results.append({
            "file": path.name,
            "action": action,
            "attempts": attempts + (0 if to_errors else 1),
            "age_sec": round(age, 1),
            "timeout_sec": round(timeout, 1),
            "moved": moved,
        })

    return results
