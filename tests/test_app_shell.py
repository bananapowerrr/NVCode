from __future__ import annotations

from threading import Thread, get_ident

from nvcode_client import AppShell, ProtocolClient, StateStore


class FakeUiLoop:
    def __init__(self):
        self.owner = get_ident()
        self.callbacks = []

    def post(self, callback):
        self.callbacks.append(callback)

    def pump(self):
        while self.callbacks:
            self.callbacks.pop(0)()


class FakeWs:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def make_client():
    callbacks = {}
    ws = FakeWs()
    posts = []

    def connect(on_event, on_close):
        callbacks["event"] = on_event
        callbacks["close"] = on_close
        return ws

    def get(path):
        if path == "/v1/status":
            return {"ok": True, "queues": {}}
        if path == "/v1/tasks":
            return {"ok": True, "tasks": [{"task_id": "t1", "status": "PENDING", "message": "build"}]}
        if path == "/v1/workers":
            return {"ok": True, "workers": [{"worker_id": "w1", "status": "READY"}]}
        if path == "/v1/leases":
            return {"ok": True, "leases": []}
        if path.startswith("/v1/logs/tail"):
            return {"ok": True, "lines": ["boot"]}
        if path == "/v1/metrics":
            return {"ok": True, "metrics": {"active": 1}}
        raise AssertionError(path)

    def post(path, body):
        posts.append((path, dict(body)))
        return {"ok": True, "task_id": "created-1", "status": "PENDING"}

    client = ProtocolClient(rest_get=get, rest_post=post, ws_connect=connect, store=StateStore())
    return client, callbacks, ws, posts


def test_app_shell_starts_projects_view_models_and_stops_idempotently():
    loop, updates = FakeUiLoop(), []
    client, callbacks, ws, posts = make_client()
    shell = AppShell(post_to_ui=loop.post, client=client, chat_task_id="t1",
                     on_panel_update=lambda panel, state: updates.append((panel, state, get_ident())))

    assert shell.start() is True
    loop.pump()
    assert set(shell.panel_state) == {"chat", "queue", "workers", "logs_metrics"}
    assert shell.panel_state["queue"]["tasks"][0].task_id == "t1"
    assert shell.panel_state["queue"]["tasks"][0].status == "PENDING"
    assert shell.panel_state["workers"]["workers"][0].worker_id == "w1"
    assert shell.panel_state["logs_metrics"]["metrics"].get("active") == 1
    assert shell.panel_state["chat"]["task"].task_id == "t1"
    assert updates and all(thread_id == loop.owner for _, _, thread_id in updates)

    worker = Thread(target=lambda: callbacks["event"]({
        "type": "task.terminal", "ts": "terminal-1",
        "payload": {"task_id": "t1", "status": "DONE"},
    }))
    worker.start()
    worker.join()
    # WebSocket-thread changes are queued; view models mutate only when UI loop pumps.
    assert shell.panel_state["queue"]["tasks"][0].status == "PENDING"
    loop.pump()
    assert shell.panel_state["queue"]["tasks"][0].status == "DONE"

    result = shell.submit_task("follow-up", project="/workspace")
    assert result["task_id"] == "created-1"
    assert posts == [("/v1/tasks", {"message": "follow-up", "project": "/workspace",
                                    "channel": "desktop"})]

    shell.stop()
    shell.stop()
    assert shell.client.state == "STOPPED"
    assert ws.closed
    assert shell.dispatcher.closed
    assert all(controller._subscribers == [] for controller in shell._controllers)


def test_app_shell_rejects_start_and_submit_after_stop():
    loop = FakeUiLoop()
    client, _, _, _ = make_client()
    shell = AppShell(post_to_ui=loop.post, client=client)
    shell.stop()
    import pytest
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        shell.start()
    with pytest.raises(RuntimeError, match="stopped"):
        shell.submit_task("late")
