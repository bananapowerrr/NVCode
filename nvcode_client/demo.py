"""Reference wiring for the UI-independent Protocol v0 client.

By default this runs offline against deterministic mocks:
    python -m nvcode_client.demo

To connect to a running local Protocol v0 server:
    python -m nvcode_client.demo --live [--task-id TASK_ID]
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from . import (
    ChatController,
    HttpRestAdapter,
    LogsMetricsController,
    ProtocolClient,
    QueueController,
    WebSocketConnector,
    WorkersController,
)


class MockWebSocket:
    def __init__(self, on_event, on_close):
        self.on_event, self.on_close, self.closed = on_event, on_close, False

    def emit(self, event: Mapping[str, Any]) -> None:
        if not self.closed:
            self.on_event(event)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.on_close(None)


class MockProtocol:
    """Small in-memory REST/WS facade matching the public Protocol v0 shape."""
    def __init__(self):
        self.tasks = [{"task_id": "demo-task", "status": "PENDING", "message": "Example task"}]
        self.workers: list[dict[str, Any]] = []
        self.leases: list[dict[str, Any]] = []
        self.logs: list[str] = ["demo: mock protocol ready"]
        self.metrics: dict[str, Any] = {"events": 0}
        self.ws: MockWebSocket | None = None

    def get(self, path: str) -> Mapping[str, Any]:
        if path == "/v1/status":
            return {"ok": True, "queues": {"desktop": len(self.tasks)}}
        if path == "/v1/tasks":
            return {"ok": True, "tasks": [dict(x) for x in self.tasks]}
        if path == "/v1/workers":
            return {"ok": True, "workers": [dict(x) for x in self.workers]}
        if path == "/v1/leases":
            return {"ok": True, "leases": [dict(x) for x in self.leases]}
        if path.startswith("/v1/logs/tail"):
            return {"ok": True, "lines": list(self.logs)}
        if path == "/v1/metrics":
            return {"ok": True, "metrics": dict(self.metrics)}
        raise ValueError(f"Unsupported mock endpoint: {path}")

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        if path != "/v1/tasks":
            raise ValueError(f"Unsupported mock endpoint: {path}")
        task_id = f"mock-task-{len(self.tasks) + 1}"
        task = {"task_id": task_id, "status": "PENDING", "message": body["message"]}
        self.tasks.append(task)
        if self.ws:
            self.ws.emit({"type": "task.created", "ts": "mock-create",
                          "payload": dict(task)})
        return {"ok": True, "task_id": task_id, "status": "PENDING"}

    def connect(self, on_event, on_close):
        self.ws = MockWebSocket(on_event, on_close)
        return self.ws

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.metrics["events"] += 1
        if self.ws:
            self.ws.emit({"type": event_type, "ts": f"mock-{self.metrics['events']}",
                          "payload": dict(payload)})


def _print_panel(name: str):
    def render(snapshot: dict[str, Any]) -> None:
        # JSON keeps this example easy to adapt to a terminal or graphical view.
        print(f"[{name}] {json.dumps(snapshot, ensure_ascii=False, default=str)}")
    return render


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="connect to the local server instead of using mocks")
    parser.add_argument("--base-url", default="http://127.0.0.1:8340")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8340/v1/events")
    parser.add_argument("--task-id", default="demo-task",
                        help="task ID scoped by ChatController in live mode")
    args = parser.parse_args()

    mock = None
    if args.live:
        rest = HttpRestAdapter(args.base_url)
        client = ProtocolClient(rest_get=rest.get, rest_post=rest.post,
                                ws_connect=WebSocketConnector(args.ws_url))
    else:
        mock = MockProtocol()
        client = ProtocolClient(rest_get=mock.get, rest_post=mock.post,
                                ws_connect=mock.connect)

    chat = ChatController(client, args.task_id)
    queue = QueueController(client.store)
    workers = WorkersController(client.store)
    logs_metrics = LogsMetricsController(client.store)
    controllers = (chat, queue, workers, logs_metrics)
    unsubscribers = [
        chat.subscribe(_print_panel("chat")),
        queue.subscribe(_print_panel("queue")),
        workers.subscribe(_print_panel("workers")),
        logs_metrics.subscribe(_print_panel("logs/metrics")),
    ]

    try:
        connected = client.start()
        print(f"Client started: {client.state} (sync success={connected})")
        result = chat.send_message(
            "Demo task submitted by the reference UI",
            metadata={"demo": True},
        )
        print(f"Task submission response: {dict(result)}")

        if mock:
            # Simulate the server's live event stream after initial REST sync.
            mock.emit("task.status", {"task_id": args.task_id, "to": "PROCESSING"})
            mock.emit("worker.busy", {"worker_id": "worker-demo", "task_id": args.task_id})
            mock.emit("log.line", {"task_id": args.task_id, "level": "info",
                                   "source": "demo", "text": "Task started"})
            mock.emit("metric.tick", {"events_seen": mock.metrics["events"]})
            mock.emit("task.terminal", {"task_id": args.task_id, "status": "DONE"})
        else:
            print("Live mode: panel updates will be printed as WebSocket events arrive.")
            print("Press Ctrl+C to stop.")
            import threading
            stop_wait = threading.Event()
            try:
                while not stop_wait.wait(1):
                    pass
            except KeyboardInterrupt:
                pass
    except (KeyboardInterrupt, OSError, RuntimeError, ValueError) as exc:
        print(f"Demo stopped: {exc}")
    finally:
        for unsubscribe in unsubscribers:
            unsubscribe()
        for controller in controllers:
            controller.close()
        client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
