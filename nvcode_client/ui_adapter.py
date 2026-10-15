"""Framework-neutral UI-thread dispatch and immutable view models.

The dispatcher deliberately depends on a host-provided, thread-safe scheduling
primitive (for example a Qt signal, a GUI event-loop callback, or a browser bridge).
It never assumes that invoking a callback from an arbitrary thread is safe.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock, get_ident
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol, TypeVar

T = TypeVar("T")
Callback = Callable[..., Any]
PostToUi = Callable[[Callable[[], None]], Any]


class ControllerLike(Protocol):
    def subscribe(self, callback: Callable[[dict[str, Any]], None], *,
                  emit_current: bool = True) -> Callable[[], None]: ...


class UiThreadDispatcher:
    """Serialize callbacks onto the UI thread using a host event-loop scheduler.

    Construct on the UI/main thread and supply a `post_to_ui` function which
    is safe to call from worker threads and which executes the supplied function
    on that UI thread. The dispatcher batches queued callbacks into one scheduled
    drain and keeps subscriber exceptions from blocking unrelated callbacks.
    """

    def __init__(self, post_to_ui: PostToUi, *,
                 owner_thread_id: int | None = None,
                 on_error: Callable[[BaseException], None] | None = None):
        if not callable(post_to_ui):
            raise TypeError("post_to_ui must be callable")
        self._post_to_ui = post_to_ui
        self.owner_thread_id = get_ident() if owner_thread_id is None else owner_thread_id
        self._on_error = on_error
        self._queue: deque[tuple[Callback, tuple[Any, ...], dict[str, Any]]] = deque()
        self._lock = RLock()
        self._scheduled = False
        self._closed = False
        self._subscriptions: list[Callable[[], None]] = []

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def dispatch(self, callback: Callback, *args: Any, **kwargs: Any) -> bool:
        """Queue a callback; return False if dispatcher is already closed."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        should_schedule = False
        with self._lock:
            if self._closed:
                return False
            self._queue.append((callback, args, kwargs))
            if not self._scheduled:
                self._scheduled = True
                should_schedule = True
        if should_schedule:
            try:
                self._post_to_ui(self._drain)
            except BaseException:
                with self._lock:
                    self._scheduled = False
                raise
        return True

    def wrap(self, callback: Callable[[T], Any]) -> Callable[[T], bool]:
        """Return a callback suitable for StateStore/controller subscriptions."""
        def wrapped(value: T) -> bool:
            return self.dispatch(callback, value)
        return wrapped

    def subscribe(self, controller: ControllerLike,
                  callback: Callable[[dict[str, Any]], Any], *,
                  emit_current: bool = True) -> Callable[[], None]:
        """Subscribe to a controller and marshal every notification onto UI thread."""
        controller_unsubscribe = controller.subscribe(
            self.wrap(callback), emit_current=emit_current
        )
        active = True
        local_lock = RLock()

        def unsubscribe() -> None:
            nonlocal active
            with local_lock:
                if not active:
                    return
                active = False
            controller_unsubscribe()
            with self._lock:
                try:
                    self._subscriptions.remove(unsubscribe)
                except ValueError:
                    pass

        with self._lock:
            if self._closed:
                controller_unsubscribe()
                return unsubscribe
            self._subscriptions.append(unsubscribe)
        return unsubscribe

    def _drain(self) -> None:
        if get_ident() != self.owner_thread_id:
            with self._lock:
                self._scheduled = False
            raise RuntimeError(
                "post_to_ui executed on the wrong thread; provide a scheduler "
                "that marshals callbacks to the UI thread"
            )
        while True:
            with self._lock:
                if self._closed:
                    self._queue.clear()
                    self._scheduled = False
                    return
                if not self._queue:
                    self._scheduled = False
                    return
                callback, args, kwargs = self._queue.popleft()
            try:
                callback(*args, **kwargs)
            except BaseException as exc:
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except BaseException:
                        pass

    def close(self, *, cancel_pending: bool = True) -> None:
        """Unsubscribe all controller listeners and optionally discard queued work."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions, self._subscriptions = self._subscriptions, []
            if cancel_pending:
                self._queue.clear()
        for unsubscribe in subscriptions:
            try:
                unsubscribe()
            except Exception:
                continue


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


@dataclass(frozen=True)
class TaskViewModel:
    task_id: str
    status: str = "UNKNOWN"
    message: str = ""
    channel: str = ""
    worker_id: str | None = None
    attempts: int | None = None
    verified: bool | None = None
    changed_files: tuple[Any, ...] = ()

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "TaskViewModel":
        attempts = record.get("attempts")
        try:
            attempts = int(attempts) if attempts is not None else None
        except (TypeError, ValueError):
            attempts = None
        verified = record.get("verified")
        if verified not in (True, False, None):
            verified = None
        return cls(
            task_id=str(record.get("task_id") or record.get("id") or ""),
            status=str(record.get("status") or "UNKNOWN").upper(),
            message=str(record.get("message") or ""),
            channel=str(record.get("channel") or ""),
            worker_id=_text(record.get("worker_id")),
            attempts=attempts,
            verified=verified,
            changed_files=_tuple(record.get("changed_files")),
        )


@dataclass(frozen=True)
class WorkerViewModel:
    worker_id: str
    status: str = "UNKNOWN"
    busy_task_id: str | None = None
    phase: str | None = None
    last_heartbeat: Any = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "WorkerViewModel":
        status = record.get("status")
        if status is None:
            status = "BUSY" if record.get("busy_task_id") else "READY"
        return cls(
            worker_id=str(record.get("worker_id") or record.get("id") or ""),
            status=str(status).upper(),
            busy_task_id=_text(record.get("busy_task_id") or record.get("task_id")),
            phase=_text(record.get("phase")),
            last_heartbeat=record.get("last_heartbeat"),
        )


@dataclass(frozen=True)
class MetricsViewModel:
    """Immutable shallow snapshot; preserves protocol-defined metric names/values."""
    values: Mapping[str, Any]

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any] | None) -> "MetricsViewModel":
        if not isinstance(snapshot, Mapping):
            return cls(MappingProxyType({}))
        values = snapshot.get("metrics", snapshot)
        if not isinstance(values, Mapping):
            values = {}
        return cls(MappingProxyType(dict(values)))

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)


def filter_tasks(tasks: Iterable[TaskViewModel], *,
                 statuses: Iterable[str] | None = None,
                 query: str | None = None) -> list[TaskViewModel]:
    """Filter task view models by normalized status and case-insensitive text."""
    allowed = {str(status).upper() for status in statuses} if statuses is not None else None
    needle = (query or "").strip().casefold()
    result = []
    for task in tasks:
        if allowed is not None and task.status.upper() not in allowed:
            continue
        haystack = " ".join((task.task_id, task.message, task.channel, task.worker_id or "")).casefold()
        if needle and needle not in haystack:
            continue
        result.append(task)
    return result


def filter_workers(workers: Iterable[WorkerViewModel], *,
                   statuses: Iterable[str] | None = None,
                   query: str | None = None) -> list[WorkerViewModel]:
    """Filter worker view models by status, worker ID, or active task ID."""
    allowed = {str(status).upper() for status in statuses} if statuses is not None else None
    needle = (query or "").strip().casefold()
    result = []
    for worker in workers:
        if allowed is not None and worker.status.upper() not in allowed:
            continue
        haystack = " ".join((worker.worker_id, worker.status, worker.busy_task_id or "")).casefold()
        if needle and needle not in haystack:
            continue
        result.append(worker)
    return result
