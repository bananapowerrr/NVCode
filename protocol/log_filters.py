# -*- coding: utf-8 -*-
"""Filtered log tail helpers for Protocol v0 (no core/FSM dependency)."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def filter_logs_tail(
    root: Path,
    n: int = 200,
    *,
    task_id: str | None = None,
    level: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    n = max(1, min(int(n or 200), 2000))
    task_f = (task_id or "").strip() or None
    level_f = (level or "").strip().upper() or None
    if level_f == "WARN":
        level_f = "WARNING"
    source_f = (source or "").strip() or None

    candidates = [
        root / "logs" / "runtime.log",
        root / "logs" / "agentbus.log",
        root / "channels" / "desktop" / "logs",
    ]
    log_dir = root / "logs"
    if source_f and log_dir.is_dir():
        extra = sorted(
            log_dir.glob(f"*{source_f}*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        candidates = list(extra) + candidates

    window = n if not (task_f or level_f or source_f) else min(2000, max(n * 20, 500))

    lines: list[str] = []
    chosen_source = None
    for c in candidates:
        if c.is_file():
            try:
                text = c.read_text(encoding="utf-8", errors="replace").splitlines()
                lines = text[-window:]
                chosen_source = c.name
                break
            except Exception:
                continue
        if c.is_dir():
            logs = sorted(c.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if source_f:
                logs = [p for p in logs if source_f.lower() in p.name.lower()] or logs
            if logs:
                try:
                    lines = logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-window:]
                    chosen_source = logs[0].name
                    break
                except Exception:
                    pass

    def _match(line: str) -> bool:
        if task_f and task_f not in line:
            return False
        if level_f:
            up = line.upper()
            if level_f == "WARNING":
                if "WARNING" not in up and " WARN" not in up and not up.startswith("WARN"):
                    return False
            elif level_f not in up:
                return False
        if source_f:
            if source_f.lower() not in line.lower() and (
                not chosen_source or source_f.lower() not in chosen_source.lower()
            ):
                return False
        return True

    if task_f or level_f or source_f:
        lines = [ln for ln in lines if _match(ln)][-n:]
    else:
        lines = lines[-n:]

    redacted = []
    for line in lines:
        low = line.lower()
        if "api_key" in low or "password" in low or "secret" in low:
            redacted.append("[redacted]")
        else:
            redacted.append(line[:2000])
    out: dict[str, Any] = {"ok": True, "lines": redacted, "count": len(redacted)}
    if chosen_source:
        out["source_file"] = chosen_source
    filters = {k: v for k, v in (("task_id", task_f), ("level", level_f), ("source", source_f)) if v}
    if filters:
        out["filters"] = filters
    return out
