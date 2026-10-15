# NVCode Protocol v0 client core

This UI-independent package consumes only the public localhost REST/WebSocket API. It does not import or modify `src/core/`, runtime/executor/reclaim code, or the legacy Tkinter UI.

## Wiring

```python
from nvcode_client import HttpRestAdapter, ProtocolClient, WebSocketConnector

rest = HttpRestAdapter("http://127.0.0.1:8340")
client = ProtocolClient(rest_get=rest.get, ws_connect=WebSocketConnector())
client.start()
state = client.store.state  # connection, tasks, workers, leases, logs, metrics
# On application shutdown:
client.stop()
```

The WebSocket connector is receive-only, standard-library-only, and restricted to localhost. UI frameworks should marshal state notifications to their own UI thread. The retry policy uses full jitter: uniform(0, min(cap, base * 2**attempt)); the scheduler is injectable for deterministic tests.

## Consistency limits

- `GET /v1/status` and `GET /v1/tasks` are required for successful REST synchronization; workers, leases, logs and metrics are optional enrichments.
- Snapshot generations are checked, and REST task/worker/lease records do not overwrite entities changed by a newer WS event while the request is in flight.
- Terminal task states do not regress to non-terminal states.
- Protocol v0 does not require event IDs or sequence numbers. Event fingerprint dedupe and log-tail reconciliation are best-effort, not exactly-once delivery or guaranteed full log replay.
- The client preserves in-memory state through outages and cancels its pending retry timer on `stop()`.


## UI-002 panel controllers

All controllers observe `StateStore` and publish immutable-by-convention snapshots to subscribers. Each `subscribe(callback)` immediately emits the current snapshot by default and returns an unsubscribe function. Call `close()` when disposing a panel/controller.

```python
from nvcode_client import (
    ChatController, QueueController, WorkersController, LogsMetricsController,
    HttpRestAdapter, ProtocolClient, WebSocketConnector,
)

rest = HttpRestAdapter("http://127.0.0.1:8340")
client = ProtocolClient(rest_get=rest.get, rest_post=rest.post, ws_connect=WebSocketConnector())
queue = QueueController(client.store)
unsubscribe = queue.subscribe(lambda snapshot: render_queue(snapshot["tasks"]))
# later: unsubscribe(); queue.close()

chat = ChatController(client, task_id="task-id")
chat.subscribe(render_chat)
# Sends a new task through POST /v1/tasks:
# chat.send_message("Please investigate", project="/workspace")
# later: chat.close()

workers = WorkersController(client.store)
workers.subscribe(render_workers)

logs = LogsMetricsController(client.store, level="error", task_id="task-id")
logs.subscribe(render_logs_and_metrics)
```

- `ChatController` filters state changes for the selected task. Protocol v0 currently exposes task intake rather than a separate append-to-existing-chat endpoint; `send_message` therefore creates a new task via `POST /v1/tasks` and tags it with `metadata.related_task_id`. This metadata is informational unless the server's intake logic gives it meaning.
- `QueueController` emits the full task list after task events and snapshots.
- `WorkersController` emits worker and lease snapshots. Leases are read-only.
- `LogsMetricsController` supports optional exact filters for `level`, `source` (or `worker_id`), and `task_id`; metric updates are emitted alongside the filtered log list.
- Subscriber exceptions are isolated so a broken view cannot interrupt reducers or other listeners. StateStore notifications are synchronous on the thread that processes the event; UI frameworks must dispatch rendering onto their own UI thread when required.

## Owner verification for PR #3

From a clean checkout of the PR branch:

```bash
git fetch origin
git switch feat/ui-000-protocol-client
git pull --ff-only origin feat/ui-000-protocol-client
python -m compileall -q nvcode_client tests
python -m pytest -q tests/test_ui_client.py
python -m pytest -q
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
```

Expected changed paths for this UI work are limited to `nvcode_client/` and `tests/test_ui_client.py`. Verify the diff contains no `src/core/` files or legacy Tkinter changes. Do not mark the PR ready to merge until the targeted and repository-wide suites both exit 0 in the same checkout.
