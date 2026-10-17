# -*- coding: utf-8 -*-
"""Day-2 offline: Plan → step outcomes → replan → serial eligibility."""
from __future__ import annotations

from pathlib import Path

from intelligence.living_plan import LivingPlan, LivingStep, save_living_plan, load_living_plan
from intelligence.plan_runtime_bridge import (
    apply_task_outcome,
    eligible_serial,
    has_in_progress,
    next_step,
    plan_blocks_enqueue,
    replan_after_error,
)


def _plan_two_steps() -> LivingPlan:
    return LivingPlan(
        project_id="demo",
        summary="auth flow",
        version=1,
        steps=[
            LivingStep(id="s1", action="Add login", status="PENDING"),
            LivingStep(
                id="s2",
                action="Add logout",
                status="PENDING",
                depends_on=["s1"],
            ),
            LivingStep(
                id="s3",
                action="Add tests",
                status="PENDING",
                depends_on=["s2"],
            ),
        ],
    )


def test_s1_done_unlocks_s2_not_s3():
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    assert plan.get("s1").status == "DONE"
    elig = {s.id for s in plan.eligible_for_queue()}
    assert "s2" in elig
    assert "s3" not in elig
    assert "s1" not in elig


def test_s2_error_blocks_s3():
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    apply_task_outcome(plan, "s2", task_status="ERROR", task_id="t2", reason="verify fail")
    assert plan.get("s2").status == "ERROR"
    elig = {s.id for s in plan.eligible_for_queue()}
    assert "s3" not in elig
    # s2 finished ERROR — not active
    assert "s2" not in elig


def test_serial_blocks_while_in_progress():
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="PROCESSING", task_id="t1")
    assert has_in_progress(plan)
    assert eligible_serial(plan) == []
    assert next_step(plan) is None


def test_done_frozen_against_error():
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    out = apply_task_outcome(plan, "s1", task_status="ERROR", task_id="t9")
    assert out["ok"] is False
    assert plan.get("s1").status == "DONE"


def test_replan_after_error_keeps_error_history(tmp_path: Path):
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    apply_task_outcome(plan, "s2", task_status="ERROR", task_id="t2")
    out = replan_after_error(plan, "s2", new_action="Fix logout properly")
    assert out["ok"] is True
    assert plan.get("s2").status == "ERROR"  # history frozen
    new_id = out["new_step_id"]
    assert plan.get(new_id) is not None
    assert plan.get(new_id).status == "PENDING"
    # s3 still blocked until new retry path completes — depends on s2 DONE only
    # new step depends on s1 (from s2.deps), not on ERROR s2
    elig_ids = {s.id for s in plan.eligible_for_queue()}
    assert new_id in elig_ids
    assert "s3" not in elig_ids  # still depends on s2 DONE


def test_persist_reload(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    save_living_plan(root, plan)
    loaded = load_living_plan(root)
    assert loaded.get("s1").status == "DONE"
    assert loaded.version == plan.version


def test_blocks_when_error_stalls_plan():
    plan = _plan_two_steps()
    apply_task_outcome(plan, "s1", task_status="DONE", task_id="t1")
    apply_task_outcome(plan, "s2", task_status="ERROR", task_id="t2")
    gate = plan_blocks_enqueue(plan)
    assert gate["block"] is True
    assert "error" in gate["reason"]


def test_classify_and_decision_not_in_this_file():
    # smoke: import path still works
    from app.plan_service import classify_plan_input, apply_input_policy
    assert classify_plan_input("перепланируй всё с нуля") == "REPLAN"
    assert classify_plan_input("добавь в план логирование") == "ADD"
