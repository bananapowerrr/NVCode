from __future__ import annotations

from threading import Thread, get_ident

import pytest

from nvcode_client import QueueController, StateStore
from nvcode_client.ui_adapter import (
    MetricsViewModel,
    TaskViewModel,
    UiThreadDispatcher,
    WorkerViewModel,
    filter_tasks,
    filter_workers,
)


class FakeUiLoop:
    """A deterministic event-loop stand-in; callbacks run when pump() is called."""
    def __init__(self):
        self.owner = get_ident()
        self.callbacks = []

    def post(self, callback):
        self.callbacks.append(callback)

    def pump(self):
        while self.callbacks:
            self.callbacks.pop(0)()


def test_dispatch_from_worker_runs_callback_only_when_ui_loop_pumps():
    loop = FakeUiLoop()
    dispatcher = UiThreadDispatcher(loop.post, owner_thread_id=loop.owner)
    seen = []

    worker = Thread(target=lambda: dispatcher.dispatch(lambda value: seen.append((value, get_ident())), "ready"))
    worker.start()
    worker.join()

    assert seen == []
    assert len(loop.callbacks) == 1
    loop.pump()
    assert seen == [("ready", loop.owner)]
    dispatcher.close()


def test_dispatch_batches_callbacks_and_preserves_order():
    loop = FakeUiLoop()
    dispatcher = UiThreadDispatcher(loop.post, owner_thread_id=loop.owner)
    seen = []
    dispatcher.dispatch(seen.append, 1)
    dispatcher.dispatch(seen.append, 2)
    dispatcher.dispatch(seen.append, 3)
    assert len(loop.callbacks) == 1
    assert dispatcher.pending_count == 3
    loop.pump()
    assert seen == [1, 2, 3]
    assert dispatcher.pending_count == 0


def test_controller_subscription_is_dispatched_and_unsubscribable():
    loop, store, seen = FakeUiLoop(), StateStore(), []
    dispatcher = UiThreadDispatcher(loop.post, owner_thread_id=loop.owner)
    controller = QueueController(store)
    unsubscribe = dispatcher.subscribe(controller, seen.append)
    assert seen == []  # even emit_current is marshalled onto the UI loop
    loop.pump()
    assert seen[-1]["tasks"] == []

    worker = Thread(target=lambda: store.apply_event({
        "type": "task.created", "ts": "1", "payload": {"task_id": "t1", "status": "PENDING"}
    }))
    worker.start()
    worker.join()
    loop.pump()
    assert seen[-1]["tasks"][0]["task_id"] == "t1"

    unsubscribe()
    store.apply_event({"type": "task.status", "ts": "2", "payload": {"task_id": "t1", "to": "PROCESSING"}})
    loop.pump()
    assert seen[-1]["tasks"][0]["status"] == "PENDING"
    dispatcher.close()
    controller.close()


def test_dispatcher_close_cancels_queued_callbacks_and_rejects_new_ones():
    loop = FakeUiLoop()
    dispatcher = UiThreadDispatcher(loop.post, owner_thread_id=loop.owner)
    seen = []
    dispatcher.dispatch(seen.append, "pending")
    dispatcher.close()
    assert dispatcher.dispatch(seen.append, "late") is False
    loop.pump()
    assert seen == []
    assert dispatcher.closed


def test_scheduler_must_really_marshal_to_owner_thread():
    loop = FakeUiLoop()
    dispatcher = UiThreadDispatcher(lambda callback: callback(), owner_thread_id=loop.owner)
    errors = []

    def worker():
        try:
            dispatcher.dispatch(lambda: None)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = Thread(target=worker)
    thread.start()
    thread.join()
    assert errors and "wrong thread" in errors[0]
    dispatcher.close()


def test_callback_exception_does_not_prevent_following_callbacks():
    loop, errors, seen = FakeUiLoop(), [], []
    dispatcher = UiThreadDispatcher(loop.post, owner_thread_id=loop.owner, on_error=errors.append)
    dispatcher.dispatch(lambda: (_ for _ in ()).throw(ValueError("bad view")))
    dispatcher.dispatch(seen.append, "still delivered")
    loop.pump()
    assert len(errors) == 1 and isinstance(errors[0], ValueError)
    assert seen == ["still delivered"]
    dispatcher.close()


def test_task_view_model_normalizes_fields_and_filters():
    tasks = [
        TaskViewModel.from_record({"id": "a", "status": "pending", "message": "Build NVCode", "channel": "desktop"}),
        TaskViewModel.from_record({"task_id": "b", "status": "DONE", "message": "Write docs", "attempts": "2",
                                   "changed_files": ["README.md", "guide.md"], "verified": True}),
        TaskViewModel.from_record({"task_id": "c", "status": "ERROR", "message": "build failed"}),
    ]
    assert tasks[0].task_id == "a" and tasks[0].status == "PENDING"
    assert tasks[1].attempts == 2 and tasks[1].changed_files == ("README.md", "guide.md")
    assert tasks[1].verified is True
    filtered = filter_tasks(tasks, statuses={"done", "error"}, query="build")
    assert [task.task_id for task in filtered] == ["c"]


def test_worker_view_model_defaults_status_and_filters_by_task():
    workers = [
        WorkerViewModel.from_record({"worker_id": "w1", "busy_task_id": "task-1"}),
        WorkerViewModel.from_record({"worker_id": "w2", "status": "READY"}),
    ]
    assert workers[0].status == "BUSY"
    assert workers[1].status == "READY"
    assert filter_workers(workers, statuses={"busy"}, query="TASK-1") == [workers[0]]


def test_metrics_view_model_accepts_api_envelope_and_is_immutable():
    model = MetricsViewModel.from_snapshot({"ok": True, "metrics": {"active": 3, "errors": 1}})
    assert model.get("active") == 3
    assert model.get("missing", 7) == 7
    with pytest.raises(TypeError):
        model.values["active"] = 4
    assert MetricsViewModel.from_snapshot(None).values == {}
