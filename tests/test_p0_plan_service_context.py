# -*- coding: utf-8 -*-
from pathlib import Path
from intelligence.living_plan import LivingPlan, LivingStep, save_living_plan
from app.plan_service import PlanService, classify_plan_input, apply_input_policy

def test_plan_service_add_cancel_move(tmp_path: Path):
    plan = LivingPlan(version=1, summary="auth")
    plan.steps.append(LivingStep(id="a", action="Design OAuth", status="PENDING"))
    plan.steps.append(LivingStep(id="b", action="Add tests", status="PENDING", depends_on=["a"]))
    save_living_plan(tmp_path, plan)
    svc = PlanService(tmp_path)
    s = svc.add_step(action="Docs", note="later")
    assert s["id"]
    data = svc.list_plan()
    assert len(data["steps"]) == 3
    r = svc.move_step("b", delta=-1)
    assert r["ok"]
    # b may warn if depends on a now below
    assert svc.cancel_step(s["id"], reason="not needed")
    data2 = svc.list_plan()
    cancelled = [x for x in data2["steps"] if x["id"] == s["id"]][0]
    assert cancelled["status"] == "CANCELLED"

def test_replan_explicit(tmp_path: Path):
    plan = LivingPlan(version=1, summary="v1")
    plan.steps.append(LivingStep(id="old", action="Old approach", status="PENDING"))
    save_living_plan(tmp_path, plan)
    svc = PlanService(tmp_path)
    out = svc.replan(
        summary="v2 oauth",
        supersede_ids=["old"],
        add=[{"action": "Redesign OAuth", "id": "new"}],
        reason="architecture change",
    )
    assert out["ok"]
    assert out["version"] >= 2
    steps = {s["id"]: s for s in out["plan"]["steps"]}
    assert steps["old"]["status"] == "SUPERSEDED"
    assert any(s["action"] == "Redesign OAuth" for s in out["plan"]["steps"])

def test_classify_no_silent():
    assert classify_plan_input("добавь в план логирование") == "ADD"
    assert classify_plan_input("перепланируй с нуля") == "REPLAN"
    assert classify_plan_input("просто вопрос") == "INDEPENDENT"
    r = apply_input_policy(".", "переделай oauth", auto=False)
    assert r["requires_decision"] is True
    assert r["applied"] is False

def test_finished_not_superseded(tmp_path: Path):
    plan = LivingPlan(version=1)
    plan.steps.append(LivingStep(id="d", action="Finished", status="DONE"))
    save_living_plan(tmp_path, plan)
    svc = PlanService(tmp_path)
    out = svc.replan(supersede_ids=["d"], reason="try")
    steps = {s["id"]: s for s in out["plan"]["steps"]}
    assert steps["d"]["status"] == "DONE"  # frozen
