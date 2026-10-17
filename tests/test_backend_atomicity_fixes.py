# -*- coding: utf-8 -*-
"""Regression tests for backend atomicity / CAS fixes.

Covers TD-002..TD-008, TD-024..TD-026 (see docs/TECHNICAL_DEBT_REGISTRY.md).
Run: pytest tests/test_backend_atomicity_fixes.py
"""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _no_tmp_left(directory) -> None:
    leftovers = [p for p in Path(directory).rglob("*.tmp")]
    assert not leftovers, f"stray tmp files: {leftovers}"


# ---------------------------------------------------------------- sub_agent
def test_spawn_many_registry_atomic(tmp_path):
    from intelligence.sub_agent import SubAgent

    bus = SubAgent(bus_root=tmp_path, channel="gpt")
    res = bus.spawn_many(
        [{"message": "do a"}, {"message": "do b"}],
        parent_id="parent-1",
    )
    assert len(res.task_ids) == 2
    # registry exists and is valid JSON (TD-002)
    reg = bus._registry_path("parent-1")
    assert reg.is_file()
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert data["task_ids"] == res.task_ids
    # every child task is complete JSON, no .tmp leftovers (TD-003)
    for tid in res.task_ids:
        p = tmp_path / "channels" / res.channel / "incoming" / f"{tid}.json"
        assert p.is_file()
        json.loads(p.read_text(encoding="utf-8"))
    _no_tmp_left(tmp_path)


def test_cancel_pending_atomic(tmp_path):
    from intelligence.sub_agent import SubAgent

    bus = SubAgent(bus_root=tmp_path, channel="gpt")
    res = bus.spawn_many([{"message": "task one"}], parent_id="parent-2")
    cancelled = bus.cancel_pending("parent-2")
    assert cancelled == res.task_ids
    err = tmp_path / "channels" / res.channel / "errors" / f"{res.task_ids[0]}.json"
    assert err.is_file()
    raw = json.loads(err.read_text(encoding="utf-8"))
    assert raw["status"] == "CANCELLED"
    _no_tmp_left(tmp_path)


# ---------------------------------------------------------------- living_plan
def test_save_living_plan_atomic(tmp_path):
    from intelligence.living_plan import LivingPlan, save_living_plan

    plan = LivingPlan()
    path = save_living_plan(tmp_path, plan)
    assert path.is_file()
    json.loads(path.read_text(encoding="utf-8"))
    assert (tmp_path / ".agentbus" / "living_plan.md").is_file()
    _no_tmp_left(tmp_path)


# ---------------------------------------------------------------- report
def test_nightly_report_atomic(tmp_path):
    from intelligence.report import NightlyReport

    rep = NightlyReport(report_dir=tmp_path)
    path = rep.save()
    assert path is not None and path.is_file()
    assert path.read_text(encoding="utf-8")
    _no_tmp_left(tmp_path)


# ---------------------------------------------------------------- conversation
def test_conversation_store_atomic(tmp_path):
    from intelligence.conversation import ConversationStore

    store = ConversationStore(root=tmp_path)
    conv = store.create(project="demo")
    store.save(conv)
    got = store.get(conv.session_id)
    assert got is not None and got.session_id == conv.session_id
    _no_tmp_left(tmp_path)


# ---------------------------------------------------------------- local_queue
def test_local_queue_spill_atomic_put_and_claim(tmp_path):
    from core.local_queue import LocalTaskQueue

    spill = tmp_path / "spill"
    q = LocalTaskQueue(spill_dir=spill)
    tid = q.put({"task": "hello"})
    spilled = spill / f"{tid}.json"
    assert spilled.is_file()
    assert json.loads(spilled.read_text(encoding="utf-8"))["id"] == tid
    _no_tmp_left(spill)

    # claim from a fresh queue instance (cross-process recovery path, TD-026)
    q2 = LocalTaskQueue(spill_dir=spill)
    claimed = q2.claim()
    assert claimed is not None and claimed["id"] == tid
    # second claim must not duplicate
    assert q2.claim() is None


# ---------------------------------------------------------------- reclaim CAS
def test_lease_cas_owner_check(tmp_path):
    from core.reclaim import touch_lease, write_lease

    task = tmp_path / "t1.json"
    task.write_text("{}", encoding="utf-8")

    first = write_lease(task, task_id="t1", worker="worker-a", stuck_timeout_sec=60.0)
    assert first is not None
    # foreign worker must NOT refresh a live lease (TD-006 CAS)
    assert touch_lease(task, "refresh", expected_worker_id="worker-b") is False
    # the owner may refresh
    assert touch_lease(task, "refresh", expected_worker_id="worker-a") is True
    lease = json.loads((tmp_path / "t1.lease.json").read_text(encoding="utf-8"))
    assert lease["worker_id"] == "worker-a"
