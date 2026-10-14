# -*- coding: utf-8 -*-
from pathlib import Path

from app.project_workflow import ProjectWorkflow
from app.plan_service import PlanService
from intelligence.dynamic_queue import notify_plan_task_terminal
from intelligence.living_plan import LivingPlan, LivingStep, save_living_plan


def test_run_preview(tmp_path: Path):
    (tmp_path / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    wf = ProjectWorkflow(tmp_path)
    d = wf.run_preview(limit=3)
    assert "analysis" in d
    assert "advice" in d
    assert "plan" in d
    text = wf.format_preview(limit=3)
    assert "workflow" in text.lower() or "Status" in text or "Advice" in text


def test_build_plan_no_persist(tmp_path: Path):
    (tmp_path / "x.py").write_text("a=1\n", encoding="utf-8")
    plan = ProjectWorkflow(tmp_path).build_plan_from_advice(limit=2, persist=False)
    assert "steps" in plan
    # steps may be empty if advisor fails soft — still structured
    assert isinstance(plan["steps"], list)


def test_analyze_shape(tmp_path: Path):
    (tmp_path / "x.py").write_text("a=1\n", encoding="utf-8")
    a = ProjectWorkflow(tmp_path).analyze()
    assert a.get("project")
    assert "health" in a


def test_enqueue_first_pending_keeps_plan_identity_through_terminal(tmp_path: Path, monkeypatch):
    plan = LivingPlan(
        version=7,
        project_id=str(tmp_path),
        steps=[LivingStep(id="s1", action="Add coverage", status="PENDING")],
    )
    save_living_plan(tmp_path, plan)
    submitted = []

    def fake_submit(payload, **_kwargs):
        submitted.append(dict(payload))
        return str(payload["id"]), None

    import core.task_service
    monkeypatch.setattr(core.task_service, "submit_payload", fake_submit)

    result = ProjectWorkflow(tmp_path).enqueue_first_pending()

    assert result["ok"] is True
    task = submitted[0]
    meta = task["metadata"]
    assert meta["plan_step_id"] == "s1"
    assert meta["plan_version"] == 7
    assert meta["project_root"] == str(tmp_path)
    assert PlanService(tmp_path).get_step("s1")["status"] == "IN_PROGRESS"
    assert notify_plan_task_terminal(
        tmp_path,
        task_id=task["id"],
        plan_step_id="s1",
        status="DONE",
        metadata=meta,
    )
    assert PlanService(tmp_path).get_step("s1")["status"] == "DONE"
