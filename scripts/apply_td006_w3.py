#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply TD-006 (lease CAS wire) + W3 (no double record_task) in-place.

Run from repo root:

    python scripts/apply_td006_w3.py

Safe: only string-replaces known blocks; aborts if patterns missing.
Does not touch PR #2 / docs / UI.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def patch_runtime_ops(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "expected_worker_id=wid" in text or "expected_worker_id=wid or None" in text:
        print("TD-006 already applied:", path)
        return
    old = '''    def _touch_task_lease(self, task: "Task", phase: str = "work") -> None:
        """Refresh processing lease heartbeat for long-running tasks."""
        try:
            from core.reclaim import touch_lease, write_lease, compute_stuck_timeout_sec, task_complexity
            p = self.bus.paths(task.channel)["processing"] / f"{task.id}.json"
            if not p.is_file():
                return
            lease = p.with_name(p.stem + ".lease.json")
            if not lease.is_file():
                write_lease(
                    p,
                    task_id=str(task.id),
                    worker=str(getattr(task, "worker", "") or ""),
                    complexity=task_complexity(task.to_dict()),
                    attempts=int(getattr(task, "attempts", 0) or 0),
                    stuck_timeout_sec=compute_stuck_timeout_sec(task.to_dict()),
                    phase=phase,
                )
            else:
                touch_lease(p, phase=phase)
'''
    new = '''    def _touch_task_lease(self, task: "Task", phase: str = "work") -> None:
        """Refresh processing lease heartbeat for long-running tasks.

        TD-006: pass worker_id so touch_lease refuses to refresh a foreign lease.
        """
        try:
            from core.reclaim import touch_lease, write_lease, compute_stuck_timeout_sec, task_complexity
            p = self.bus.paths(task.channel)["processing"] / f"{task.id}.json"
            if not p.is_file():
                return
            wid = str(
                getattr(task, "worker", "")
                or getattr(task, "executor", "")
                or getattr(self, "worker_id", "")
                or ""
            )
            lease = p.with_name(p.stem + ".lease.json")
            if not lease.is_file():
                write_lease(
                    p,
                    task_id=str(task.id),
                    worker=wid,
                    complexity=task_complexity(task.to_dict()),
                    attempts=int(getattr(task, "attempts", 0) or 0),
                    stuck_timeout_sec=compute_stuck_timeout_sec(task.to_dict()),
                    phase=phase,
                )
            else:
                touch_lease(p, phase=phase, expected_worker_id=wid or None)
'''
    # tolerate CRLF in working tree
    old_crlf = old.replace("\n", "\r\n")
    if old in text:
        text = text.replace(old, new, 1)
    elif old_crlf in text:
        text = text.replace(old_crlf, new.replace("\n", "\r\n"), 1)
    else:
        raise SystemExit("TD-006: pattern not found in runtime_ops.py — abort")
    path.write_text(text, encoding="utf-8", newline="\n")
    print("TD-006 applied:", path)


def patch_cache_skills(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "W3: record_task omitted" in text:
        print("W3 already applied:", path)
        return
    # drop trailing garbage if present
    marker = "\n# ---------------------------------------------------------------------------\n# Runtime orchestration"
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n"
        print("stripped trailing Runtime orchestration garbage")
    blocks = [
        (
            '''        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record_task(task, "cache", True, 0.0, status="DONE")
        except Exception:
            pass
''',
            "        # W3: record_task omitted — finish_task already recorded terminal metrics\n",
        ),
        (
            '''        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record_task(
                task, "skill", True, 0.0, status="DONE")
        except Exception:
            pass
''',
            "        # W3: record_task omitted — finish_task already recorded terminal metrics\n",
        ),
    ]
    n = 0
    for old, new in blocks:
        if old in text:
            text = text.replace(old, new, 1)
            n += 1
        else:
            old_crlf = old.replace("\n", "\r\n")
            if old_crlf in text:
                text = text.replace(old_crlf, new.replace("\n", "\r\n"), 1)
                n += 1
    if n != 2:
        raise SystemExit(f"W3: expected 2 replacements, got {n} — abort")
    path.write_text(text, encoding="utf-8", newline="\n")
    print("W3 applied:", path, f"({n} blocks)")


def main() -> int:
    ops = ROOT / "src" / "core" / "runtime_ops.py"
    cache = ROOT / "src" / "core" / "rp_cache_skills.py"
    if not ops.is_file() or not cache.is_file():
        print("Run from NVCode repo root", file=sys.stderr)
        return 2
    patch_runtime_ops(ops)
    patch_cache_skills(cache)
    print("Done. Review git diff, then commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
