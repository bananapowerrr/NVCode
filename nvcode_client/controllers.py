"""Observer-style UI controllers projected from ProtocolClient's StateStore."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .client import ProtocolClient
from .store import StateStore


Subscriber = Callable[[dict[str, Any]], None]


class _Controller:
    def __init__(self, store: StateStore):
        self.store = store
        self._subscribers: list[Subscriber] = []
        self._unsubscribe_store = store.subscribe(self._on_store_change)

    def subscribe(self, callback: Subscriber, *, emit_current: bool = True) -> Callable[[], None]:
        """Register a subscriber and return an idempotent unsubscribe function."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._subscribers.append(callback)
        if emit_current:
            self._deliver(callback, self.snapshot())
        active = True
        def unsubscribe():
            nonlocal active
            if active:
                active = False
                try: self._subscribers.remove(callback)
                except ValueError: pass
        return unsubscribe

    def close(self) -> None:
        self._unsubscribe_store()
        self._subscribers.clear()

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    def _matches(self, change: Mapping[str, Any]) -> bool:
        return True

    def _on_store_change(self, change: dict[str, Any]) -> None:
        if self._matches(change):
            value = self.snapshot()
            for callback in tuple(self._subscribers):
                self._deliver(callback, value)

    @staticmethod
    def _deliver(callback: Subscriber, value: dict[str, Any]) -> None:
        try:
            callback(value)
        except Exception:
            # A view subscriber must not break state reduction or other subscribers.
            return


class ChatController(_Controller):
    """Project task-scoped protocol events and submit new work through POST /v1/tasks.

    Protocol v0 exposes task intake, not a separate append-to-existing-chat endpoint.
    send_message therefore submits a new task and tags it with related_task_id metadata.
    """
    def __init__(self, client: ProtocolClient, task_id: str):
        self.client, self.task_id = client, str(task_id)
        super().__init__(client.store)

    def snapshot(self) -> dict[str, Any]:
        state = self.store.state
        logs = [dict(item) for item in state.logs
                if str(item.get("task_id", "")) == self.task_id]
        return {"task_id": self.task_id,
                "task": dict(state.tasks[self.task_id]) if self.task_id in state.tasks else None,
                "events": logs,
                "connection": state.connection}

    def _matches(self, change: Mapping[str, Any]) -> bool:
        if change.get("kind") in ("snapshot", "connection"):
            return True
        event = change.get("event") or {}
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        return str(payload.get("task_id", "")) == self.task_id

    def send_message(self, message: str, *, project: str = "", channel: str = "desktop",
                     metadata: Mapping[str, Any] | None = None, recipe: str | None = None) -> Mapping[str, Any]:
        meta = dict(metadata or {})
        meta.setdefault("related_task_id", self.task_id)
        return self.client.submit_task(message, project=project, channel=channel, metadata=meta, recipe=recipe)


class QueueController(_Controller):
    """Observe the current task queue, including status changes."""
    def snapshot(self) -> dict[str, Any]:
        tasks = [dict(task) for task in self.store.state.tasks.values()]
        tasks.sort(key=lambda item: (str(item.get("status", "")), str(item.get("task_id", item.get("id", "")))))
        return {"tasks": tasks, "count": len(tasks), "connection": self.store.state.connection}

    def _matches(self, change: Mapping[str, Any]) -> bool:
        if change.get("kind") in ("snapshot", "connection"):
            return True
        event = change.get("event") or {}
        return str(event.get("type", "")).startswith("task.")


class WorkersController(_Controller):
    """Observe worker records and the read-only lease snapshot."""
    def snapshot(self) -> dict[str, Any]:
        workers = [dict(item) for item in self.store.state.workers.values()]
        leases = [dict(item) for item in self.store.state.leases.values()]
        workers.sort(key=lambda item: str(item.get("worker_id", item.get("id", ""))))
        leases.sort(key=lambda item: str(item.get("task_id", item.get("id", ""))))
        return {"workers": workers, "leases": leases,
                "connection": self.store.state.connection}

    def _matches(self, change: Mapping[str, Any]) -> bool:
        if change.get("kind") in ("snapshot", "connection"):
            return True
        event = change.get("event") or {}
        return str(event.get("type", "")).startswith("worker.") or str(event.get("type", "")) in ("lease.created", "lease.updated", "lease.released")


class LogsMetricsController(_Controller):
    """Observe logs and metric ticks, with optional level/source/task filters."""
    def __init__(self, store: StateStore, *, level: str | None = None,
                 source: str | None = None, task_id: str | None = None):
        self.level, self.source, self.task_id = level, source, str(task_id) if task_id is not None else None
        super().__init__(store)

    def _filtered_logs(self) -> list[dict[str, Any]]:
        result = []
        for raw in self.store.state.logs:
            item = dict(raw)
            if self.level and str(item.get("level", "")).lower() != self.level.lower():
                continue
            if self.source and str(item.get("source", item.get("worker_id", ""))) != self.source:
                continue
            if self.task_id is not None and str(item.get("task_id", "")) != self.task_id:
                continue
            result.append(item)
        return result

    def snapshot(self) -> dict[str, Any]:
        return {"logs": self._filtered_logs(), "metrics": dict(self.store.state.metrics),
                "connection": self.store.state.connection}

    def _matches(self, change: Mapping[str, Any]) -> bool:
        if change.get("kind") in ("snapshot", "connection"):
            return True
        event = change.get("event") or {}
        return event.get("type") in ("log.line", "metric.tick", "runtime.health")
