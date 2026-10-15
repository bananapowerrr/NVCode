"""In-memory Protocol v0 state and event reduction."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib, json, time
from typing import Any, Mapping

TERMINAL = {"DONE", "ERROR", "DEFERRED", "CANCELLED", "FAILED"}
RANK = {"": 0, "PENDING": 1, "CLAIMED": 2, "PROCESSING": 3, "VERIFYING": 4}

@dataclass(frozen=True)
class SyncTicket:
    generation: int
    revision: int

@dataclass
class ClientState:
    connection: str = "OFFLINE"
    status: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    workers: dict[str, dict[str, Any]] = field(default_factory=dict)
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    last_sync_at: float | None = None
    last_error: str | None = None

def _records(value: Any, collection: str) -> list[dict[str, Any]]:
    names = {"tasks": ("tasks", "items"), "workers": ("workers", "items"),
             "leases": ("leases", "items"), "logs": ("lines", "logs", "items")}[collection]
    raw = value if isinstance(value, list) else next(
        (value.get(k) for k in names if isinstance(value, Mapping) and isinstance(value.get(k), list)), [])
    return [dict(x) if isinstance(x, Mapping) else {"text": str(x)}
            for x in raw if collection == "logs" or isinstance(x, Mapping)]

class StateStore:
    """UI-independent in-memory snapshot with bounded best-effort event dedupe."""
    def __init__(self, max_logs: int = 2000, max_event_ids: int = 5000, dedupe_ttl: float = 900):
        self.state = ClientState()
        self.max_logs, self.max_event_ids, self.dedupe_ttl = max(1, max_logs), max(1, max_event_ids), max(1, dedupe_ttl)
        self._generation = self._revision = 0
        self._entity_revision: dict[tuple[str, str], int] = {}
        self._seen: OrderedDict[str, float] = OrderedDict()

    @property
    def revision(self) -> int:
        return self._revision

    def begin_sync(self) -> SyncTicket:
        self._generation += 1
        return SyncTicket(self._generation, self._revision)

    def set_connection(self, state: str, error: str | None = None) -> None:
        self.state.connection, self.state.last_error = state, error

    def _duplicate(self, event: Mapping[str, Any]) -> bool:
        eid = event.get("event_id") or event.get("id")
        data = json.dumps({k: event.get(k) for k in ("type", "ts", "payload")},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        key = "id:" + str(eid) if eid else "fp:" + hashlib.sha256(data.encode()).hexdigest()
        now = time.monotonic()
        while self._seen and (now - next(iter(self._seen.values())) > self.dedupe_ttl or len(self._seen) >= self.max_event_ids):
            self._seen.popitem(last=False)
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = now
        return False

    def apply_event(self, event: Mapping[str, Any]) -> bool:
        if not isinstance(event, Mapping) or not event.get("type") or self._duplicate(event):
            return False
        self._revision += 1
        EventReducer.reduce(self, event)
        return True

    def apply_snapshot(self, ticket: SyncTicket, *, status=None, tasks=None, workers=None,
                       leases=None, logs=None, metrics=None) -> bool:
        if ticket.generation != self._generation:
            return False
        if isinstance(status, Mapping):
            self.state.status = dict(status)
        for name, incoming, key_name in (("tasks", tasks, "task_id"), ("workers", workers, "worker_id"),
                                         ("leases", leases, "task_id")):
            if incoming is None:
                continue
            target = getattr(self.state, name)
            records = _records(incoming, name)
            keys = set()
            for record in records:
                raw_key = record.get(key_name) or record.get("id")
                if raw_key in (None, ""):
                    continue
                key = str(raw_key); keys.add(key)
                if self._entity_revision.get((name, key), 0) <= ticket.revision:
                    target[key] = record
            for key in list(target):
                if key not in keys and self._entity_revision.get((name, key), 0) <= ticket.revision:
                    del target[key]
        if logs is not None:
            known = {json.dumps(x, sort_keys=True, default=str) for x in self.state.logs}
            for item in _records(logs, "logs"):
                sig = json.dumps(item, sort_keys=True, default=str)
                if sig not in known:
                    self.state.logs.append(item); known.add(sig)
            self.state.logs = self.state.logs[-self.max_logs:]
        if isinstance(metrics, Mapping):
            self.state.metrics = dict(metrics.get("metrics", metrics))
        self.state.last_sync_at = time.time()
        return True

class EventReducer:
    @staticmethod
    def reduce(store: StateStore, event: Mapping[str, Any]) -> None:
        kind = str(event.get("type", ""))
        p = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if kind in ("task.created", "task.status", "task.terminal"):
            raw_id = p.get("task_id") or p.get("id")
            if not raw_id:
                return
            tid = str(raw_id); item = dict(store.state.tasks.get(tid, {"task_id": tid}))
            if kind == "task.created":
                item.setdefault("status", "PENDING")
                item.update({k: v for k, v in p.items() if v is not None})
            else:
                status = str(p.get("status") or p.get("to") or "").upper()
                old = str(item.get("status", "")).upper()
                if old not in TERMINAL and status:
                    if status in TERMINAL or RANK.get(status, 0) >= RANK.get(old, 0):
                        item["status"] = status
                for k, v in p.items():
                    if k not in ("to", "status") and v is not None:
                        item[k] = v
            store.state.tasks[tid] = item
            store._entity_revision[("tasks", tid)] = store.revision
        elif kind in ("worker.busy", "worker.ready"):
            raw_id = p.get("worker_id") or p.get("id")
            if not raw_id:
                return
            wid = str(raw_id); item = dict(store.state.workers.get(wid, {"worker_id": wid}))
            item["status"] = "BUSY" if kind == "worker.busy" else "READY"
            if p.get("task_id"):
                item["busy_task_id"] = p["task_id"]
            elif kind == "worker.ready":
                item["busy_task_id"] = None
            store.state.workers[wid] = item
            store._entity_revision[("workers", wid)] = store.revision
        elif kind == "log.line":
            store.state.logs = (store.state.logs + [dict(p)])[-store.max_logs:]
        elif kind == "metric.tick":
            store.state.metrics.update(p)
        elif kind == "runtime.health":
            store.state.status["runtime_health"] = dict(p)
