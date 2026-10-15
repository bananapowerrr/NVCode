"""Interactive terminal frontend for AppShell.

Mock mode (safe, no server required):
    python -m nvcode_client.interactive_demo

Live Protocol v0 server:
    python -m nvcode_client.interactive_demo --live
    python -m nvcode_client.interactive_demo --live --chat-task-id TASK_ID

Commands: help, status, tasks, workers, logs, submit <message>, simulate (mock only), quit.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
from collections.abc import Mapping
from typing import Any

from .app_shell import AppShell, _jsonable
from .client import HttpRestAdapter, ProtocolClient, WebSocketConnector
from .store import StateStore


class _MockSocket:
    def __init__(self, on_event, on_close):
        self.on_event, self.on_close, self.closed = on_event, on_close, False

    def emit(self, event: Mapping[str, Any]) -> None:
        if not self.closed:
            self.on_event(dict(event))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.on_close(None)


class _MockProtocol:
    """Small REST/WS double with the same public response envelope shapes."""
    def __init__(self):
        self.tasks = [{"task_id": "demo-1", "status": "PENDING", "message": "Example task"}]
        self.workers: list[dict[str, Any]] = []
        self.leases: list[dict[str, Any]] = []
        self.logs: list[str] = ["interactive demo ready"]
        self.metrics: dict[str, Any] = {"events": 0}
        self.socket: _MockSocket | None = None
        self._next_id = 2

    def get(self, path: str) -> Mapping[str, Any]:
        if path == "/v1/status":
            return {"ok": True, "queues": {"desktop": len(self.tasks)}}
        if path == "/v1/tasks":
            return {"ok": True, "tasks": [dict(task) for task in self.tasks]}
        if path == "/v1/workers":
            return {"ok": True, "workers": [dict(worker) for worker in self.workers]}
        if path == "/v1/leases":
            return {"ok": True, "leases": [dict(lease) for lease in self.leases]}
        if path.startswith("/v1/logs/tail"):
            return {"ok": True, "lines": list(self.logs)}
        if path == "/v1/metrics":
            return {"ok": True, "metrics": dict(self.metrics)}
        raise ValueError(f"Unsupported mock endpoint: {path}")

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        if path != "/v1/tasks":
            raise ValueError(f"Unsupported mock endpoint: {path}")
        task_id = f"demo-{self._next_id}"
        self._next_id += 1
        task = {"task_id": task_id, "status": "PENDING", "message": str(body["message"])}
        self.tasks.append(task)
        self._emit("task.created", task)
        self._emit("log.line", {"level": "info", "task_id": task_id,
                                "source": "interactive-demo", "text": "Task accepted by mock"})
        return {"ok": True, "task_id": task_id, "status": "PENDING"}

    def connect(self, on_event, on_close):
        self.socket = _MockSocket(on_event, on_close)
        return self.socket

    def _emit(self, kind: str, payload: Mapping[str, Any]) -> None:
        self.metrics["events"] += 1
        self.socket.emit({"type": kind, "ts": f"mock-{self.metrics['events']}",
                          "payload": dict(payload)}) if self.socket else None

    def simulate(self, task_id: str) -> None:
        task = next((item for item in self.tasks if item["task_id"] == task_id), None)
        if task is None:
            print(f"No mock task with ID {task_id!r}. Submit a task first.", flush=True)
            return
        task["status"] = "PROCESSING"
        worker = {"worker_id": "worker-demo", "status": "BUSY", "busy_task_id": task_id}
        self.workers = [worker]
        self.leases = [{"task_id": task_id, "worker_id": "worker-demo", "phase": "processing"}]
        self._emit("task.status", {"task_id": task_id, "to": "PROCESSING"})
        self._emit("worker.busy", {"worker_id": "worker-demo", "task_id": task_id})
        self._emit("log.line", {"level": "info", "task_id": task_id,
                                "source": "worker-demo", "text": "Mock worker started"})
        self._emit("metric.tick", {"simulated_runs": self.metrics["events"]})
        self._emit("task.terminal", {"task_id": task_id, "status": "DONE"})
        task["status"] = "DONE"
        worker["status"] = "READY"
        worker["busy_task_id"] = None
        self.leases = []
        self._emit("worker.ready", {"worker_id": "worker-demo"})


class _TerminalLoop:
    """Drain AppShell's UI dispatcher on the main thread while stdin runs separately."""
    def __init__(self):
        self.callbacks: queue.Queue[Any] = queue.Queue()
        self.commands: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()

    def post_to_ui(self, callback) -> None:
        self.callbacks.put(callback)

    def _read_commands(self) -> None:
        while not self.stop_event.is_set():
            try:
                line = input("nvcode> ")
            except (EOFError, KeyboardInterrupt):
                self.commands.put("quit")
                return
            self.commands.put(line)

    def pump_callbacks(self) -> None:
        while True:
            try:
                callback = self.callbacks.get_nowait()
            except queue.Empty:
                return
            callback()


def _render(panel: str, snapshot: Mapping[str, Any]) -> None:
    print(f"\n[{panel}] {json.dumps(_jsonable(snapshot), ensure_ascii=False, default=str)}",
          flush=True)


def _print_help(mock_mode: bool) -> None:
    print("Commands:", flush=True)
    print("  status              show connection state and panel names", flush=True)
    print("  tasks               print the latest task panel snapshot", flush=True)
    print("  workers             print workers and active leases", flush=True)
    print("  logs                print log/metrics panel snapshot", flush=True)
    print("  submit <message>    submit a task via POST /v1/tasks", flush=True)
    if mock_mode:
        print("  simulate            emit mock task/worker/log/metric events", flush=True)
    print("  help                show this help", flush=True)
    print("  quit                close client and exit", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="connect to the real local Protocol v0 server; default is safe mock mode")
    parser.add_argument("--base-url", default="http://127.0.0.1:8340")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8340/v1/events")
    parser.add_argument("--chat-task-id", default="demo-1")
    args = parser.parse_args(argv)

    loop = _TerminalLoop()
    mock = None
    if args.live:
        shell = AppShell(post_to_ui=loop.post_to_ui, on_panel_update=_render,
                         base_url=args.base_url, ws_url=args.ws_url,
                         chat_task_id=args.chat_task_id)
    else:
        mock = _MockProtocol()
        client = ProtocolClient(rest_get=mock.get, rest_post=mock.post,
                                ws_connect=mock.connect, store=StateStore())
        shell = AppShell(post_to_ui=loop.post_to_ui, on_panel_update=_render,
                         client=client, chat_task_id=args.chat_task_id)

    try:
        connected = shell.start()
        loop.pump_callbacks()
        print(f"Connected={connected}; state={shell.client.state}; mode={'live' if args.live else 'mock'}",
              flush=True)
        _print_help(mock is not None)
    except Exception as exc:
        shell.stop()
        print(f"Startup failed: {exc}", flush=True)
        return 1

    reader = threading.Thread(target=loop._read_commands, name="nvcode-cli-input", daemon=True)
    reader.start()
    try:
        while not loop.stop_event.is_set():
            loop.pump_callbacks()
            try:
                command = loop.commands.get(timeout=0.1)
            except queue.Empty:
                continue
            command = command.strip()
            if not command:
                continue
            if command in ("quit", "exit", "q"):
                break
            if command == "help":
                _print_help(mock is not None)
            elif command == "status":
                print(f"connection={shell.client.state}; panels={', '.join(shell.panel_state)}", flush=True)
            elif command == "tasks":
                print(json.dumps(_jsonable(shell.panel_state.get("queue", {})), ensure_ascii=False, default=str), flush=True)
            elif command == "workers":
                print(json.dumps(_jsonable(shell.panel_state.get("workers", {})), ensure_ascii=False, default=str), flush=True)
            elif command == "logs":
                print(json.dumps(_jsonable(shell.panel_state.get("logs_metrics", {})), ensure_ascii=False, default=str), flush=True)
            elif command.startswith("submit "):
                message = command[len("submit "):].strip()
                if not message:
                    print("Usage: submit <message>", flush=True)
                    continue
                try:
                    result = shell.submit_task(message, metadata={"source": "interactive_demo"})
                    print(f"POST /v1/tasks -> {json.dumps(dict(result), ensure_ascii=False)}", flush=True)
                except Exception as exc:
                    print(f"Task submission failed: {exc}", flush=True)
            elif command == "simulate" and mock is not None:
                task_id = next((str(t.get("task_id")) for t in reversed(list(shell.store.state.tasks.values()))
                                if t.get("status") not in ("DONE", "ERROR", "DEFERRED")), "")
                mock.simulate(task_id or "demo-1")
            else:
                print("Unknown command. Type 'help'.", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop_event.set()
        shell.stop()
        print("NVCode client stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
