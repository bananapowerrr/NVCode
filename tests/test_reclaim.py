# -*- coding: utf-8 -*-
"""Unit tests for adaptive stuck reclaim (offline)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def test_compute_stuck_timeout_scales_with_complexity():
    from core.reclaim import compute_stuck_timeout_sec

    t1 = compute_stuck_timeout_sec({"metadata": {"complexity": 1}}, base_sec=300, max_sec=1800)
    t5 = compute_stuck_timeout_sec({"metadata": {"complexity": 5}}, base_sec=300, max_sec=1800)
    assert t5 > t1
    assert t1 >= 60
    assert t5 <= 1800


def test_worker_factor_opencode_longer_than_skill():
    from core.reclaim import compute_stuck_timeout_sec

    local = compute_stuck_timeout_sec(
        {"worker": "ollama_aider", "metadata": {"complexity": 3}},
        base_sec=300,
        max_sec=1800,
    )
    heavy = compute_stuck_timeout_sec(
        {"worker": "opencode_big", "metadata": {"complexity": 3}},
        base_sec=300,
        max_sec=1800,
    )
    assert heavy >= local


def test_reclaim_requeues_stale_under_max_attempts(tmp_path: Path):
    from core.reclaim import reclaim_stuck
    import os

    proc = tmp_path / "processing"
    inc = tmp_path / "incoming"
    err = tmp_path / "errors"
    for d in (proc, inc, err):
        d.mkdir()
    tid = "task-stale"
    name = f"{tid}.json"
    payload = {"id": tid, "message": "x", "attempts": 1, "metadata": {"complexity": 2}}
    p = proc / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    # force stale mtime
    old = time.time() - 10_000
    os.utime(p, (old, old))
    results = reclaim_stuck(
        processing_dir=proc,
        incoming_dir=inc,
        errors_dir=err,
        max_attempts=3,
        base_sec=60,
        max_sec=300,
    )
    assert any(r.get("moved") for r in results)
    assert (inc / name).is_file() or (err / name).is_file()


def test_write_and_touch_lease(tmp_path: Path):
    from core.reclaim import write_lease, touch_lease, last_active_epoch

    p = tmp_path / "t1.json"
    p.write_text("{}", encoding="utf-8")
    lp = write_lease(p, task_id="t1", worker="aider", complexity=4, attempts=0)
    assert lp is not None and lp.is_file()
    t0 = last_active_epoch(p, {})
    time.sleep(0.05)
    touch_lease(p, phase="execute")
    t1 = last_active_epoch(p, {})
    assert t1 >= t0


def test_fresh_lease_not_reclaimed(tmp_path: Path):
    """Active heartbeat must prevent reclaim (no double execution)."""
    from core.reclaim import reclaim_stuck, write_lease
    import os

    proc = tmp_path / "processing"
    inc = tmp_path / "incoming"
    err = tmp_path / "errors"
    for d in (proc, inc, err):
        d.mkdir()
    tid = "task-live"
    name = f"{tid}.json"
    payload = {"id": tid, "message": "x", "attempts": 0, "metadata": {"complexity": 2}}
    p = proc / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    write_lease(p, task_id=tid, worker="mock", complexity=2, attempts=0)
    now = time.time()
    os.utime(p, (now, now))
    results = reclaim_stuck(
        processing_dir=proc,
        incoming_dir=inc,
        errors_dir=err,
        max_attempts=3,
        base_sec=300,
        max_sec=1800,
    )
    assert not any(r.get("moved") for r in results)
    assert (proc / name).is_file()


def test_touch_lease_cas_rejects_foreign_worker(tmp_path: Path):
    """TD-006: foreign expected_worker_id must not refresh lease."""
    from core.reclaim import write_lease, touch_lease

    p = tmp_path / "t-cas.json"
    p.write_text("{}", encoding="utf-8")
    write_lease(p, task_id="t-cas", worker="owner-a", complexity=2, attempts=0)
    lp = p.with_name(p.stem + ".lease.json")
    before = json.loads(lp.read_text(encoding="utf-8"))
    hb0 = before.get("last_heartbeat")

    ok = touch_lease(p, phase="execute", expected_worker_id="owner-b")
    assert ok is False
    after = json.loads(lp.read_text(encoding="utf-8"))
    assert after.get("worker_id") == "owner-a"
    assert after.get("last_heartbeat") == hb0


def test_touch_lease_cas_same_owner_ok(tmp_path: Path):
    """TD-006: matching expected_worker_id refreshes heartbeat."""
    from core.reclaim import write_lease, touch_lease

    p = tmp_path / "t-same.json"
    p.write_text("{}", encoding="utf-8")
    write_lease(p, task_id="t-same", worker="owner-a", complexity=2, attempts=0)
    lp = p.with_name(p.stem + ".lease.json")
    hb0 = json.loads(lp.read_text(encoding="utf-8")).get("last_heartbeat")
    time.sleep(0.05)
    ok = touch_lease(p, phase="stream", expected_worker_id="owner-a")
    assert ok is True
    after = json.loads(lp.read_text(encoding="utf-8"))
    assert after.get("worker_id") == "owner-a"
    assert after.get("last_heartbeat") >= hb0
    assert after.get("phase") == "stream"
