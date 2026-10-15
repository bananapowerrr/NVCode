# -*- coding: utf-8 -*-
"""Source-level guards for TD-006 wire and W3 metrics (GROK-B1)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_ops_wires_expected_worker_id():
    src = (ROOT / "src" / "core" / "runtime_ops.py").read_text(encoding="utf-8")
    assert "expected_worker_id=wid" in src or "expected_worker_id=wid or None" in src
    assert "def _touch_task_lease" in src


def test_w3_no_double_record_task_after_finish_in_cache_skill():
    src = (ROOT / "src" / "core" / "rp_cache_skills.py").read_text(encoding="utf-8")
    # After W3 the explicit terminal record_task on cache/skill DONE is removed.
    assert "W3: record_task omitted" in src
    assert 'record_task(task, "cache"' not in src
    # residual DONE+record_task lines (excluding comments)
    lines = src.replace("\r\n", "\n").splitlines()
    bad = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            continue
        if "record_task" in s and ("cache" in s or "skill" in s):
            window = "\n".join(lines[i : i + 4])
            if "DONE" in window:
                bad.append(i + 1)
    assert not bad, f"found residual record_task DONE at lines {bad}"
