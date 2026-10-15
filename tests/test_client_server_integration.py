"""End-to-end nvcode_client checks against the real Protocol v0 HTTP/WS server.

Task intake is mocked at core.task_service.submit so this test exercises the public
HTTP route without writing real tasks or invoking an executor.
"""
from __future__ import annotations

import threading
import time
from unittest import mock

import pytest

from nvcode_client import (
    ChatController,
    HttpRestAdapter,
    LogsMetricsController,
    ProtocolClient,
    QueueController,
    StateStore,
    WebSocketConnector,
    WorkersController,
)


@pytest.fixture
def live_protocol_server(monkeypatch):
    from protocol import event_bridge
    from protocol.event_bridge import EventBridge
    from protocol.server import DEFAULT_HOST, ProtocolHandler, ProtocolServer

    # Isolate WS fan-out from other tests' clients and any process-global bus listeners.
    bridge = EventBridge(max_queue=64)
    monkeypatch.setattr(event_bridge, "BRIDGE", bridge)
    server = ProtocolServer((DEFAULT_HOST, 0), ProtocolHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{DEFAULT_HOST}:{port}"
    for _ in range(60):
        try:
            import urllib.request
            with urllib.request.urlopen(base + "/v1/health", timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    else:
        server.shutdown()
        server.server_close()
        pytest.fail("Protocol v0 server did not become ready")
    yield base, bridge
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def test_client_server_rest_post_websocket_and_controller_notifications(
    live_protocol_server, monkeypatch
):
    base, bridge = live_protocol_server
    created_tasks = []
    submit_calls = []

    def submit(message, **kwargs):
        submit_calls.append((message, kwargs))
        created_tasks.append({
            "task_id": "ui-int-1", "status": "PENDING", "message": message,
            "channel": kwargs.get("extra", {}).get("channel", "desktop"),
        })
        return "ui-int-1", None

    # Keep the public POST /v1/tasks route real while making its intake side effect safe.
    monkeypatch.setattr("core.task_service.submit", submit)
    monkeypatch.setattr(
        "protocol.rest_handlers.list_tasks",
        lambda state=None, channel=None, limit=100: {
            "ok": True, "tasks": list(created_tasks), "count": len(created_tasks)
        },
    )

    rest = HttpRestAdapter(base, timeout=2.0)
    store = StateStore()
    client = ProtocolClient(
        rest_get=rest.get,
        rest_post=rest.post,
        ws_connect=WebSocketConnector(base.replace("http://", "ws://") + "/v1/events", timeout=2.0),
        store=store,
    )

    queue_updates, worker_updates, log_updates, chat_updates = [], [], [], []
    queue_controller = QueueController(store)
    workers_controller = WorkersController(store)
    logs_controller = LogsMetricsController(store)
    chat_controller = ChatController(client, "ui-int-1")
    queue_controller.subscribe(queue_updates.append)
    workers_controller.subscribe(worker_updates.append)
    logs_controller.subscribe(log_updates.append)
    chat_controller.subscribe(chat_updates.append)

    try:
        assert client.start() is True
        assert client.state == "LIVE"
        assert store.state.last_sync_at is not None
        assert isinstance(store.state.status, dict)
        assert store.state.tasks == {}

        # This traverses the actual HTTP handler and HttpRestAdapter POST implementation.
        result = chat_controller.send_message("integration test task", project="/tmp/nvcode-ui-test")
        assert result["ok"] is True
        assert result["task_id"] == "ui-int-1"
        assert submit_calls and submit_calls[0][0] == "integration test task"

        # Emulate server-side eventbus publication through the real WS fan-out bridge.
        from protocol.envelope import make_envelope
        for kind, payload in (
            ("task.created", {"task_id": "ui-int-1", "status": "PENDING", "message": "integration test task"}),
            ("task.status", {"task_id": "ui-int-1", "to": "PROCESSING"}),
            ("worker.busy", {"worker_id": "worker-int-1", "task_id": "ui-int-1"}),
            ("log.line", {"task_id": "ui-int-1", "level": "info", "text": "task started"}),
            ("metric.tick", {"tasks_started": 1}),
        ):
            bridge.broadcast(make_envelope(kind, payload))

        assert _wait_for(lambda: store.state.tasks.get("ui-int-1", {}).get("status") == "PROCESSING")
        assert _wait_for(lambda: any(
            item.get("worker_id") == "worker-int-1" for item in store.state.workers.values()
        ))
        assert _wait_for(lambda: any(item.get("text") == "task started" for item in store.state.logs))
        assert _wait_for(lambda: store.state.metrics.get("tasks_started") == 1)

        assert queue_updates[-1]["tasks"][0]["task_id"] == "ui-int-1"
        assert worker_updates[-1]["workers"][0]["worker_id"] == "worker-int-1"
        assert log_updates[-1]["metrics"]["tasks_started"] == 1
        assert any(item.get("type") == "task.status" for item in chat_updates[-1]["events"])
    finally:
        client.stop()
        queue_controller.close()
        workers_controller.close()
        logs_controller.close()
        chat_controller.close()


def test_live_websocket_disconnect_reconnects_and_rest_reconciles(live_protocol_server):
    base, _bridge = live_protocol_server
    rest = HttpRestAdapter(base, timeout=2.0)
    scheduled = []

    class FakeTimer:
        def __init__(self, callback):
            self.callback, self.cancelled = callback, False
        def cancel(self):
            self.cancelled = True

    def schedule(delay, callback):
        timer = FakeTimer(callback)
        scheduled.append((delay, timer))
        return timer

    client = ProtocolClient(
        rest_get=rest.get,
        ws_connect=WebSocketConnector(base.replace("http://", "ws://") + "/v1/events", timeout=2.0),
        store=StateStore(),
        schedule_fn=schedule,
        random_fn=lambda: 0.25,
    )
    try:
        assert client.start() is True
        active_ws = client._ws
        assert active_ws is not None

        # Closing the real socket invokes the transport's on_close callback.
        active_ws.close()
        assert _wait_for(lambda: bool(scheduled))
        delay, timer = scheduled[-1]
        assert delay >= 0
        assert not timer.cancelled

        # Run the captured retry callback deterministically instead of sleeping.
        assert timer.callback() is True
        assert client.state == "LIVE"
        assert client.store.state.last_sync_at is not None
    finally:
        client.stop()
