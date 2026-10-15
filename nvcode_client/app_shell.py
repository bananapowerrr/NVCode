"""Framework-neutral reference application shell for NVCode Protocol v0.

The shell owns client/store/controller lifecycles and projects controller snapshots
into view models. A host UI supplies `post_to_ui`, a thread-safe scheduler that
executes callbacks on its main/UI thread (Qt signal, Tk `after`, etc.).
"""
from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .client import HttpRestAdapter, ProtocolClient, WebSocketConnector
from .controllers import (
    ChatController,
    LogsMetricsController,
    QueueController,
    WorkersController,
)
from .store import StateStore
from .ui_adapter import (
    MetricsViewModel,
    TaskViewModel,
    UiThreadDispatcher,
    WorkerViewModel,
)


PanelCallback = Callable[[str, Mapping[str, Any]], None]


class AppShell:
    """Lifecycle and view-model wiring shared by desktop and terminal frontends.

    Construct this on the UI thread. `post_to_ui` must be safe to call from
    background threads and must enqueue (not inline-execute) the supplied
    callback on the UI thread.
    """

    def __init__(
        self,
        *,
        post_to_ui: Callable[[Callable[[], None]], Any],
        on_panel_update: PanelCallback | None = None,
        client: ProtocolClient | None = None,
        base_url: str = "http://127.0.0.1:8340",
        ws_url: str = "ws://127.0.0.1:8340/v1/events",
        chat_task_id: str = "",
    ) -> None:
        if client is None:
            rest = HttpRestAdapter(base_url)
            store = StateStore()
            client = ProtocolClient(
                rest_get=rest.get,
                rest_post=rest.post,
                ws_connect=WebSocketConnector(ws_url),
                store=store,
            )
        self.client = client
        self.store = client.store
        self.on_panel_update = on_panel_update
        self.dispatcher = UiThreadDispatcher(post_to_ui)
        self.chat = ChatController(client, chat_task_id)
        self.queue = QueueController(self.store)
        self.workers = WorkersController(self.store)
        self.logs_metrics = LogsMetricsController(self.store)
        self._controllers = (self.chat, self.queue, self.workers, self.logs_metrics)
        self._panel_state: dict[str, Mapping[str, Any]] = {}
        self._closed = False
        self._started = False
        self._unsubscribers = [
            self.dispatcher.subscribe(self.chat, lambda value: self._update("chat", value)),
            self.dispatcher.subscribe(self.queue, lambda value: self._update("queue", value)),
            self.dispatcher.subscribe(self.workers, lambda value: self._update("workers", value)),
            self.dispatcher.subscribe(self.logs_metrics, lambda value: self._update("logs_metrics", value)),
        ]

    @property
    def panel_state(self) -> Mapping[str, Mapping[str, Any]]:
        """Latest view-model projection per panel; read/update on the UI thread."""
        return dict(self._panel_state)

    def _update(self, panel: str, snapshot: Mapping[str, Any]) -> None:
        if self._closed:
            return
        if panel == "chat":
            raw = snapshot.get("task")
            projected = {
                "task_id": snapshot.get("task_id", ""),
                "task": TaskViewModel.from_record(raw) if isinstance(raw, Mapping) else None,
                "events": tuple(dict(item) for item in snapshot.get("events", ())),
                "logs": tuple(dict(item) for item in snapshot.get("logs", ())),
                "connection": str(snapshot.get("connection", "OFFLINE")),
            }
        elif panel == "queue":
            tasks = tuple(
                TaskViewModel.from_record(item)
                for item in snapshot.get("tasks", ())
                if isinstance(item, Mapping)
            )
            projected = {
                "tasks": tasks,
                "count": len(tasks),
                "connection": str(snapshot.get("connection", "OFFLINE")),
            }
        elif panel == "workers":
            workers = tuple(
                WorkerViewModel.from_record(item)
                for item in snapshot.get("workers", ())
                if isinstance(item, Mapping)
            )
            projected = {
                "workers": workers,
                "leases": tuple(dict(item) for item in snapshot.get("leases", ())),
                "connection": str(snapshot.get("connection", "OFFLINE")),
            }
        else:
            projected = {
                "logs": tuple(dict(item) for item in snapshot.get("logs", ())),
                "metrics": MetricsViewModel.from_snapshot(snapshot.get("metrics", {})),
                "connection": str(snapshot.get("connection", "OFFLINE")),
            }
        self._panel_state[panel] = projected
        if self.on_panel_update is not None:
            try:
                self.on_panel_update(panel, projected)
            except Exception:
                # A frontend rendering exception must not affect client/event processing.
                return

    def start(self) -> bool:
        """Start REST/WS synchronization; controller updates are dispatched to UI."""
        if self._closed:
            raise RuntimeError("AppShell has been stopped and cannot be restarted")
        if self._started:
            return self.client.state == "LIVE"
        self._started = True
        return bool(self.client.start())

    def submit_task(
        self,
        message: str,
        *,
        project: str = "",
        channel: str = "desktop",
        metadata: Mapping[str, Any] | None = None,
        recipe: str | None = None,
    ) -> Mapping[str, Any]:
        """Submit work through the public Protocol v0 REST intake endpoint."""
        if self._closed:
            raise RuntimeError("AppShell is stopped")
        return self.client.submit_task(
            message, project=project, channel=channel, metadata=metadata, recipe=recipe
        )

    def stop(self) -> None:
        """Stop transport and detach all UI subscriptions; safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        try:
            self.client.stop()
        finally:
            self.dispatcher.close()
            for controller in self._controllers:
                controller.close()
            self._unsubscribers.clear()

    def __enter__(self) -> "AppShell":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class _ConsoleLoop:
    """Minimal main-thread queue used by the runnable reference shell."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._stop = threading.Event()

    def post(self, callback: Callable[[], None]) -> None:
        self._queue.put(callback)

    def stop(self) -> None:
        self._stop.set()

    def run(self, shell: AppShell) -> None:
        while not self._stop.is_set():
            try:
                callback = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            callback()
        shell.stop()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {name: _jsonable(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "values") and isinstance(getattr(value, "values"), Mapping):
        return _jsonable(value.values)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NVCode Protocol v0 reference app shell")
    parser.add_argument("--base-url", default="http://127.0.0.1:8340")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8340/v1/events")
    parser.add_argument("--chat-task-id", default="", help="task ID to scope the chat panel to")
    parser.add_argument("--submit", help="submit one real task after connecting (optional)")
    parser.add_argument("--project", default="")
    args = parser.parse_args(argv)

    loop = _ConsoleLoop()

    def render(panel: str, state: Mapping[str, Any]) -> None:
        print(json.dumps({"panel": panel, "state": _jsonable(state)}, ensure_ascii=False, default=str), flush=True)

    shell = AppShell(
        post_to_ui=loop.post,
        on_panel_update=render,
        base_url=args.base_url,
        ws_url=args.ws_url,
        chat_task_id=args.chat_task_id,
    )
    try:
        connected = shell.start()
        # Deliver initial snapshots queued while REST synchronization was running.
        while not loop._queue.empty():
            loop._queue.get_nowait()()
        print(json.dumps({"event": "started", "connected": connected, "state": shell.client.state}), flush=True)
        if args.submit:
            print(json.dumps({"event": "task_submitted", "result": shell.submit_task(args.submit, project=args.project)}, default=str), flush=True)
    except Exception as exc:
        shell.stop()
        print(f"AppShell startup failed: {exc}", file=sys.stderr)
        return 1

    def request_stop(signum=None, frame=None):
        loop.stop()

    try:
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
    except (ValueError, AttributeError):
        pass
    try:
        loop.run(shell)
    finally:
        shell.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
