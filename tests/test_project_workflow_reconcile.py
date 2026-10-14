from __future__ import annotations

import json

from app.project_workflow import ProjectWorkflow
from intelligence.living_plan import LivingPlan, LivingStep, load_living_plan, save_living_plan


def _plan(root, task_id):
    plan = LivingPlan(
        project_id=str(root),
        steps=[
            LivingStep(
                id="step-1",
                action="Do work",
                status="IN_PROGRESS",
                meta={"task_id": task_id},
            )
        ],
    )
    save_living_plan(root, plan)


def test_reconcile_keeps_task_in_persistent_desktop_queue(tmp_path):
    task_id = "task-persistent-queue"
    _plan(tmp_path, task_id)
    queue = tmp_path / ".agentbus" / "desktop_queue"
    queue.mkdir(parents=True)
    (queue / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, "message": "still queued", "status": "PENDING"}),
        encoding="utf-8",
    )

    result = ProjectWorkflow(tmp_path)._reconcile_plan(tmp_path)

    assert result["demoted_to_pending"] == []
    assert load_living_plan(tmp_path).get("step-1").status == "IN_PROGRESS"


def test_reconcile_syncs_terminal_done_record_after_restart(tmp_path):
    task_id = "task-terminal-recovery"
    _plan(tmp_path, task_id)
    done = tmp_path / "channels" / "desktop" / "done"
    done.mkdir(parents=True)
    (done / f"{task_id}.json").write_text(
        json.dumps({
            "id": task_id,
            "status": "DONE",
            "result": {"summary": "verified", "verified": True, "verification": {"ok": True}},
        }),
        encoding="utf-8",
    )

    result = ProjectWorkflow(tmp_path)._reconcile_plan(tmp_path)

    assert result["terminal_synced"] == ["step-1"]
    assert load_living_plan(tmp_path).get("step-1").status == "DONE"


def test_reconcile_does_not_trust_done_folder_without_verification(tmp_path):
    task_id = "task-unverified-done-record"
    _plan(tmp_path, task_id)
    done = tmp_path / "channels" / "desktop" / "done"
    done.mkdir(parents=True)
    (done / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, "status": "DONE", "result": {"summary": "unverified"}}),
        encoding="utf-8",
    )

    result = ProjectWorkflow(tmp_path)._reconcile_plan(tmp_path)

    assert result["terminal_synced"] == []
    assert result["demoted_to_pending"] == ["step-1"]
    assert load_living_plan(tmp_path).get("step-1").status == "PENDING"
