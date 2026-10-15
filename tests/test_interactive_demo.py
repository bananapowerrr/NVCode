from __future__ import annotations

from nvcode_client import AppShell, ProtocolClient, StateStore
from nvcode_client.interactive_demo import _MockProtocol, _jsonable


class QueuedUi:
    def __init__(self):
        self.callbacks = []

    def post(self, callback):
        self.callbacks.append(callback)

    def pump(self):
        while self.callbacks:
            self.callbacks.pop(0)()


def test_interactive_demo_mock_mode_wires_app_shell_panels_and_live_events():
    mock, loop, updates = _MockProtocol(), QueuedUi(), []
    client = ProtocolClient(rest_get=mock.get, rest_post=mock.post,
                            ws_connect=mock.connect, store=StateStore())
    shell = AppShell(post_to_ui=loop.post, on_panel_update=lambda panel, snap: updates.append((panel, snap)),
                     client=client, chat_task_id="demo-1")

    assert shell.start() is True
    loop.pump()
    assert {panel for panel, _ in updates} == {"chat", "queue", "workers", "logs_metrics"}

    result = shell.submit_task("test interactive command", metadata={"source": "test"})
    loop.pump()
    task_id = result["task_id"]
    assert shell.store.state.tasks[task_id]["status"] == "PENDING"

    mock.simulate(task_id)
    loop.pump()
    assert shell.store.state.tasks[task_id]["status"] == "DONE"
    assert shell.store.state.workers["worker-demo"]["status"] == "READY"
    assert shell.store.state.leases == {}
    assert any("Mock worker started" in item.get("text", "") for item in shell.store.state.logs)
    assert shell.panel_state["queue"]["tasks"]
    assert "metrics" in _jsonable(shell.panel_state["logs_metrics"])

    shell.stop()
    assert shell.client.state == "STOPPED"


def test_interactive_demo_mock_task_submission_emits_task_created():
    mock = _MockProtocol()
    events = []
    socket = mock.connect(events.append, lambda error: None)
    result = mock.post("/v1/tasks", {"message": "hello"})
    assert result["ok"] is True
    assert result["task_id"] == "demo-2"
    assert events[0]["type"] == "task.created"
    assert events[0]["payload"]["message"] == "hello"
    socket.close()


def test_client_package_has_no_backend_or_legacy_gui_imports():
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "nvcode_client"
    forbidden = ("core", "src.core", "tkinter", "customtkinter")
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported if any(
            name == prefix or name.startswith(prefix + ".") for prefix in forbidden
        )], f"forbidden backend/legacy import in {source.name}"
