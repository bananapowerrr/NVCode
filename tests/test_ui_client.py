from __future__ import annotations
from nvcode_client import ProtocolClient, RetryPolicy, StateStore

class FakeWs:
    def __init__(self): self.closed = False
    def close(self): self.closed = True

def fake_rest(tasks=None, fail=()):
    tasks = tasks if tasks is not None else [{"task_id": "t1", "status": "PENDING"}]
    def get(path):
        if path in fail: raise OSError("offline")
        if path == "/v1/status": return {"ok": True, "queues": {}}
        if path == "/v1/tasks": return {"ok": True, "tasks": tasks}
        if path == "/v1/workers": return {"ok": True, "workers": [{"worker_id": "w1"}]}
        if path == "/v1/leases": return {"ok": True, "leases": []}
        if path.startswith("/v1/logs/tail"): return {"ok": True, "lines": ["boot"]}
        if path == "/v1/metrics": return {"ok": True, "metrics": {"count": 1}}
        raise AssertionError(path)
    return get

def test_initial_sync_populates_store_and_stop_closes_ws():
    store, ws = StateStore(), FakeWs()
    client = ProtocolClient(rest_get=fake_rest(), ws_connect=lambda event, close: ws, store=store)
    assert client.start() and client.state == "LIVE"
    assert store.state.tasks["t1"]["status"] == "PENDING"
    assert "w1" in store.state.workers and store.state.logs == [{"text": "boot"}]
    assert store.state.metrics == {"count": 1}
    client.stop()
    assert client.state == "STOPPED" and ws.closed

def test_rest_up_ws_down_is_degraded():
    def fail_ws(*args): raise OSError("ws unavailable")
    client = ProtocolClient(rest_get=fake_rest(), ws_connect=fail_ws)
    assert client.start() is False
    assert client.state == "DEGRADED" and "t1" in client.store.state.tasks

def test_both_transports_down_is_offline():
    def fail_ws(*args): raise OSError("ws unavailable")
    client = ProtocolClient(rest_get=fake_rest(fail=("/v1/status",)), ws_connect=fail_ws)
    assert not client.start() and client.state == "OFFLINE"

def test_event_dedupe_and_terminal_state_monotonicity():
    store = StateStore()
    event = {"type": "task.status", "ts": "one", "payload": {"task_id": "t", "to": "PROCESSING"}}
    assert store.apply_event(event)
    revision = store.revision
    assert not store.apply_event(event) and store.revision == revision
    store.apply_event({"type": "task.terminal", "ts": "two", "payload": {"task_id": "t", "status": "DONE"}})
    store.apply_event({"type": "task.status", "ts": "three", "payload": {"task_id": "t", "to": "PROCESSING"}})
    assert store.state.tasks["t"]["status"] == "DONE"

def test_stale_rest_record_cannot_overwrite_newer_ws_event():
    store = StateStore()
    ticket = store.begin_sync()
    store.apply_event({"type": "task.status", "ts": "new", "payload": {"task_id": "t1", "to": "PROCESSING"}})
    assert store.apply_snapshot(ticket, tasks={"tasks": [{"task_id": "t1", "status": "PENDING"}]})
    assert store.state.tasks["t1"]["status"] == "PROCESSING"

def test_old_sync_generation_is_rejected():
    store = StateStore()
    old = store.begin_sync(); store.begin_sync()
    assert not store.apply_snapshot(old, tasks={"tasks": [{"task_id": "old"}]})
    assert not store.state.tasks

def test_snapshot_deletes_missing_untouched_but_keeps_ws_changed_entity():
    store = StateStore()
    store.apply_snapshot(store.begin_sync(), tasks={"tasks": [
        {"task_id": "gone", "status": "PENDING"}, {"task_id": "live", "status": "PENDING"}]})
    ticket = store.begin_sync()
    store.apply_event({"type": "task.status", "ts": "later", "payload": {"task_id": "live", "to": "PROCESSING"}})
    store.apply_snapshot(ticket, tasks={"tasks": []})
    assert "gone" not in store.state.tasks
    assert store.state.tasks["live"]["status"] == "PROCESSING"

def test_retry_policy_is_full_jitter_and_capped():
    policy = RetryPolicy(base_seconds=1, cap_seconds=5)
    assert policy.delay(0, .5) == .5
    assert policy.delay(2, .5) == 2
    assert policy.delay(8, 1) == 5
    assert policy.delay(1, 0) == 0

def test_disconnect_reconciles_rest_and_schedules_retry():
    scheduled, store = [], StateStore()
    client = ProtocolClient(rest_get=fake_rest(), ws_connect=lambda e, c: FakeWs(), store=store,
                            random_fn=lambda: .5,
                            schedule_fn=lambda delay, callback: scheduled.append((delay, callback)))
    assert client.start()
    delay = client.on_ws_closed(ConnectionError("lost"))
    assert delay == .5 and client.state == "DEGRADED"
    assert len(scheduled) == 1 and scheduled[0][0] == delay

def test_ws_event_during_sync_is_replayed_after_snapshot():
    store, callbacks = StateStore(), {}
    def connect(on_event, on_close):
        callbacks["event"] = on_event
        return FakeWs()
    def get(path):
        if path == "/v1/status": return {"ok": True}
        if path == "/v1/tasks":
            callbacks["event"]({"type": "task.status", "ts": "new", "payload": {"task_id": "t1", "to": "PROCESSING"}})
            return {"ok": True, "tasks": [{"task_id": "t1", "status": "PENDING"}]}
        if path == "/v1/workers": return {"ok": True, "workers": []}
        if path == "/v1/leases": return {"ok": True, "leases": []}
        if path.startswith("/v1/logs"): return {"ok": True, "lines": []}
        return {"ok": True, "metrics": {}}
    client = ProtocolClient(rest_get=get, ws_connect=connect, store=store)
    assert client.start()
    assert store.state.tasks["t1"]["status"] == "PROCESSING"


# UI-002 controller observer tests

from nvcode_client import ChatController, LogsMetricsController, QueueController, WorkersController


def test_queue_controller_notifies_on_task_create_and_status_change():
    store, received = StateStore(), []
    controller = QueueController(store)
    unsubscribe = controller.subscribe(received.append)
    store.apply_event({"type": "task.created", "ts": "1", "payload": {"task_id": "q1", "message": "hello"}})
    store.apply_event({"type": "task.status", "ts": "2", "payload": {"task_id": "q1", "to": "PROCESSING"}})
    assert len(received) == 3  # initial snapshot plus both state changes
    assert received[-1]["tasks"][0]["status"] == "PROCESSING"
    unsubscribe()
    store.apply_event({"type": "task.terminal", "ts": "3", "payload": {"task_id": "q1", "status": "DONE"}})
    assert len(received) == 3
    controller.close()


def test_chat_controller_filters_task_events_and_posts_to_v1_tasks():
    store, posts, received = StateStore(), [], []
    client = ProtocolClient(rest_get=fake_rest(), ws_connect=lambda e, c: FakeWs(),
                            rest_post=lambda path, body: posts.append((path, dict(body))) or {"ok": True, "task_id": "new"})
    client.store = store
    controller = ChatController(client, "task-a")
    controller.subscribe(received.append)
    store.apply_event({"type": "task.status", "ts": "a", "payload": {"task_id": "task-b", "to": "PROCESSING"}})
    assert len(received) == 1
    store.apply_event({"type": "log.line", "ts": "b", "payload": {"task_id": "task-a", "text": "hello"}})
    assert len(received) == 2
    assert received[-1]["events"][-1]["type"] == "log.line"
    result = controller.send_message("follow up")
    assert result["task_id"] == "new"
    assert posts == [("/v1/tasks", {"message": "follow up", "project": "", "channel": "desktop",
                                    "metadata": {"related_task_id": "task-a"}})]
    controller.close()


def test_workers_controller_notifies_on_worker_events_and_snapshots():
    store, received = StateStore(), []
    controller = WorkersController(store)
    controller.subscribe(received.append)
    store.apply_event({"type": "worker.busy", "ts": "1", "payload": {"worker_id": "w1", "task_id": "t1"}})
    assert received[-1]["workers"][0]["status"] == "BUSY"
    ticket = store.begin_sync()
    store.apply_snapshot(ticket, workers={"workers": [{"worker_id": "w2", "status": "READY"}]},
                         leases={"leases": [{"task_id": "t2", "worker_id": "w2"}]})
    assert received[-1]["workers"][0]["worker_id"] == "w2"
    assert received[-1]["leases"][0]["task_id"] == "t2"
    controller.close()


def test_logs_metrics_controller_filters_logs_and_emits_metric_ticks():
    store, received = StateStore(), []
    controller = LogsMetricsController(store, level="error", task_id="t1")
    controller.subscribe(received.append)
    store.apply_event({"type": "log.line", "ts": "1", "payload": {"level": "info", "task_id": "t1", "text": "ignore"}})
    store.apply_event({"type": "log.line", "ts": "2", "payload": {"level": "error", "task_id": "t2", "text": "ignore"}})
    store.apply_event({"type": "log.line", "ts": "3", "payload": {"level": "error", "task_id": "t1", "text": "keep"}})
    assert received[-1]["logs"] == [{"level": "error", "task_id": "t1", "text": "keep"}]
    store.apply_event({"type": "metric.tick", "ts": "4", "payload": {"jobs": 2}})
    assert received[-1]["metrics"] == {"jobs": 2}
    controller.close()


def test_store_subscriber_failures_do_not_block_other_subscribers():
    store, received = StateStore(), []
    store.subscribe(lambda change: (_ for _ in ()).throw(RuntimeError("broken view")))
    store.subscribe(received.append)
    store.apply_event({"type": "task.created", "ts": "1", "payload": {"task_id": "t1"}})
    assert received[-1]["kind"] == "event"
