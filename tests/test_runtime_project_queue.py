from __future__ import annotations

from core.local_queue import LocalQueue, reset_local_queue
from core.runtime import Runtime


def test_dispatcher_claims_task_from_registered_project_queue(tmp_path, monkeypatch):
    import core.config as config

    base = tmp_path / "agent"
    project = tmp_path / "selected-project"
    base.mkdir()
    project.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", base)
    monkeypatch.setattr(config, "PROJECTS", {"selected": project})
    monkeypatch.setattr(config, "PROJECT_ROOT", project)

    q = LocalQueue(spill_dir=project / ".agentbus" / "desktop_queue")
    q.put({"id": "project-task-1", "message": "run from project queue"})
    # Simulate a process boundary: Runtime must recover the persistent file.
    reset_local_queue()
    runtime = Runtime.__new__(Runtime)
    runtime._desktop_queue_cursor = 0

    task = runtime._claim_desktop_queue()

    assert task is not None
    assert task["id"] == "project-task-1"
    assert task["message"] == "run from project queue"


def test_dispatcher_round_robins_project_queues(tmp_path, monkeypatch):
    import core.config as config

    base = tmp_path / "agent"
    project = tmp_path / "selected-project"
    base.mkdir()
    project.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", base)
    monkeypatch.setattr(config, "PROJECTS", {"selected": project})
    monkeypatch.setattr(config, "PROJECT_ROOT", project)

    LocalQueue(spill_dir=base / ".agentbus" / "desktop_queue").put(
        {"id": "base-task", "message": "base"}
    )
    LocalQueue(spill_dir=project / ".agentbus" / "desktop_queue").put(
        {"id": "project-task", "message": "project"}
    )
    reset_local_queue()
    runtime = Runtime.__new__(Runtime)
    runtime._desktop_queue_cursor = 0

    first = runtime._claim_desktop_queue()
    second = runtime._claim_desktop_queue()

    assert {first["id"], second["id"]} == {"base-task", "project-task"}


def test_runtime_resolves_registered_project_by_absolute_path(tmp_path, monkeypatch):
    import core.config as config

    monkeypatch.setattr(config, "PROJECTS", {"selected": tmp_path})

    assert config.resolve_project(str(tmp_path)) == tmp_path
