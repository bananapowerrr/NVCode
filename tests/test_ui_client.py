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
