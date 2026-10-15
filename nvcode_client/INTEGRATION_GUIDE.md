# UI Render Guidelines and Integration Guide

This guide describes how a future NVCode desktop or web-facing UI should consume the existing `nvcode_client` package. The UI is a presentation layer over Protocol v0; it must not import internal runtime/executor/reclaim modules or call server internals directly.

## 1. Responsibilities and boundaries

- `ProtocolClient`: owns REST calls, WebSocket lifecycle, reconnect/backoff, and REST reconciliation.
- `StateStore`: owns the in-memory snapshot of connection status, status payload, tasks, workers, leases, logs, metrics, and event deduplication.
- `EventReducer`: applies incoming protocol envelopes to the store.
- Controllers: project store changes into panel-specific snapshots and provide task-intake actions.
- Views/widgets: render controller snapshots and send user intent to controllers. They should not parse protocol envelopes or maintain a second authoritative copy of task state.

Use only the public `protocol/` REST/WebSocket contract. The client currently targets a local server by default (`127.0.0.1:8340`). Keep the base URL configurable for development and deployment.

## 2. Controller contract

Each controller exposes `subscribe(callback, emit_current=True)`, returning an unsubscribe function. The initial callback receives the current snapshot by default. Call `close()` when the controller is no longer needed.

| Controller | Snapshot shape | Intended panel |
| --- | --- | --- |
| `ChatController(client, task_id)` | `task_id`, `task`, task-filtered `events`, `connection` | Selected task / conversation timeline |
| `QueueController(store)` | `tasks`, `count`, `connection` | Queue table, filters, status badges |
| `WorkersController(store)` | `workers`, `leases`, `connection` | Worker and active-lease view |
| `LogsMetricsController(store, level=None, source=None, task_id=None)` | filtered `logs`, `metrics`, `connection` | Log console and metrics strip |

The callback argument is a snapshot dictionary, not a widget or a protocol event. Treat snapshots as read-only and copy values before mutating them.

### Important chat semantics

Protocol v0 currently exposes task intake via `POST /v1/tasks`; it does not expose a dedicated endpoint for appending a message to an existing task conversation. `ChatController.send_message(...)` submits a **new task** and attaches `metadata.related_task_id` as informational context. The selected task's event/log view is observational; do not present this API as an established multi-turn chat backend.

## 3. Thread-safety: never render directly from a subscriber callback

Store changes can be delivered on whichever thread processes the change: for example, the WebSocket reader thread, a caller performing REST reconciliation, or a UI/service thread. The controller does not promise GUI-main-thread delivery.

**Required pattern:** subscriber callback → framework's thread-safe dispatcher/queue → UI-thread render function. Do not call widget methods, mutate a GUI model, or touch UI-owned state directly from the subscriber callback.

Keep dispatched snapshots immutable by convention. If updates arrive faster than the view can paint, coalesce them (for example, keep only the latest queue snapshot) rather than scheduling an unbounded number of render operations.

### PyQt / PySide

Use a `QObject` bridge with a queued signal. Emit the snapshot from the controller callback; connect the signal to the view's slot using queued delivery so the slot runs on the GUI thread.

```python
# Illustrative adapter; define Bridge as a QObject in the GUI application.
class QueuePresenter:
    def __init__(self, controller, bridge):
        self.bridge = bridge
        self.unsubscribe = controller.subscribe(self.bridge.snapshot_changed.emit)

    # Connect bridge.snapshot_changed to a GUI-thread slot that updates the model.
    def close(self):
        self.unsubscribe()
```

Create the `QObject` signal with a suitable object/dict payload in the actual application. Do not use a plain Python callback as a substitute for Qt queued delivery.

### Tk / CustomTkinter (new UI only)

Use the event loop's `after(...)` mechanism to enqueue work on the UI thread. The callback itself must only enqueue; it must not update widgets.

```python
import queue

ui_updates = queue.Queue()

def on_queue_snapshot(snapshot):
    ui_updates.put(snapshot)  # safe from a worker / WS thread

def drain_queue():
    latest = None
    while True:
        try:
            latest = ui_updates.get_nowait()
        except queue.Empty:
            break
    if latest is not None:
        render_queue(latest)  # called on the UI thread
    root.after(50, drain_queue)

unsubscribe = queue_controller.subscribe(on_queue_snapshot)
root.after(50, drain_queue)
```

This is a pattern example for a future, isolated UI. It does not authorize edits to or reuse of the legacy Tkinter application. For high-volume logs, consider a bounded queue and drop/coalesce policy.

### Web UI / browser wrapper

Do not pass Python callbacks into browser components directly. Put a small application service around `nvcode_client` and forward controller snapshots through an explicit bridge (for example, a local IPC/WebSocket endpoint or the web framework's supported server-to-client channel). Serialize only the data required by the view. Validate actions on the server-side adapter and keep the Protocol v0 service bound to localhost unless a separately reviewed deployment design changes that security boundary.

### Terminal UI

If the terminal library is not thread-safe, enqueue snapshots and let its main loop drain the queue. Redraw from the main loop only.

## 4. Initialization and shutdown lifecycle

1. Construct `HttpRestAdapter`, `WebSocketConnector`, `StateStore` (optional), then `ProtocolClient`.
2. Construct controllers from that client/store and subscribe render adapters before starting the client, so the initial snapshots and connection transitions can be observed.
3. Run `client.start()` outside the GUI event thread: the WebSocket handshake and initial REST synchronization can block on I/O. Forward the result and subsequent snapshots through the UI dispatcher.
4. Render the initial state and keep the UI reactive while the client reports `LIVE`, `DEGRADED`, `RECONNECT_WAIT`, or `OFFLINE`. Do not clear the last known snapshot merely because the connection is down.
5. Submit work through `ChatController.send_message` / `ProtocolClient.submit_task` and surface rejected/failed requests as UI errors; do not optimistically mark a task as accepted until the POST result confirms it.
6. During shutdown, stop accepting user actions, call `client.stop()`, invoke each subscription's unsubscribe function, then call each controller's `close()`. Stop/join any UI adapter worker before destroying its event loop.

Example wiring:

```python
from nvcode_client import (
    ChatController, HttpRestAdapter, LogsMetricsController, ProtocolClient,
    QueueController, StateStore, WebSocketConnector, WorkersController,
)

rest = HttpRestAdapter("http://127.0.0.1:8340")
store = StateStore()
client = ProtocolClient(
    rest_get=rest.get,
    rest_post=rest.post,
    ws_connect=WebSocketConnector("ws://127.0.0.1:8340/v1/events"),
    store=store,
)
chat = ChatController(client, task_id="selected-task")
queue = QueueController(store)
workers = WorkersController(store)
logs = LogsMetricsController(store, level=None)

# Each callback below should enqueue/dispatch to the framework's UI thread.
unsubscribers = [
    chat.subscribe(on_chat_snapshot),
    queue.subscribe(on_queue_snapshot),
    workers.subscribe(on_workers_snapshot),
    logs.subscribe(on_logs_metrics_snapshot),
]
# Start this on a service/background thread, not a GUI event thread.
client.start()

# Shutdown:
client.stop()
for unsubscribe in unsubscribers:
    unsubscribe()
for controller in (chat, queue, workers, logs):
    controller.close()
```

The callback names above are application-provided dispatcher functions, not APIs supplied by `nvcode_client`.

## 5. Recommended future desktop component structure

Keep the presentation code separate from `nvcode_client`. A proposed next-stage structure (names are a plan, not existing modules):

```text
nvcode_ui/
  app.py                    # app bootstrap, configuration, lifecycle
  services/
    client_service.py       # owns ProtocolClient + StateStore
    ui_dispatch.py          # framework-specific thread/queue bridge
  shell/
    main_window.py          # navigation, split-pane layout, global status
  panels/
    chat_panel.py           # selected task, events, task-intake composer
    queue_panel.py          # task table, status/channel filters
    workers_panel.py        # workers and active leases
    logs_panel.py           # searchable/filterable log stream
    metrics_panel.py        # metric summary and connection state
  models/
    queue_model.py          # UI-native model derived from snapshots
    log_model.py            # bounded display model, not source of truth
  widgets/
    connection_indicator.py
    task_status_badge.py
    empty_error_states.py
  theme/
    tokens.py
    styles.py
  tests/
    test_ui_dispatch.py
    test_panel_presenters.py
```

### Suggested screen layout

- **Application shell:** connection indicator, reconnect/degraded warning, global actions, and navigation.
- **Main work area:** queue on the left; selected task details / event timeline in the center; workers/leases in a collapsible side panel.
- **Bottom drawer:** logs and metrics, with independent filters and a bounded visible history.
- **Shared UX states:** loading, empty, stale/offline, permission/intake rejection, and recoverable error states.

### Implementation sequence

1. Build and test the UI-thread dispatch adapter independently of any framework.
2. Add presentation models that transform controller snapshots into native table/list models; keep filtering and sorting presentation-only.
3. Implement the shell and connection indicator, including stale-data and reconnect states.
4. Build queue and task-detail panels, then workers/leases and logs/metrics.
5. Add error/loading/empty states, keyboard navigation and accessibility, then polish layout/theme.
6. Add UI-level tests for dispatch, unsubscribe-on-close, coalescing, and no widget calls from worker threads.
7. Keep live-server behavior covered by the existing Protocol v0 integration tests; add only UI-boundary tests in the UI package.

## 6. Data consistency and security notes

- Treat controller snapshots as current in-memory projections, not durable storage.
- Protocol v0 event IDs/sequence numbers are not guaranteed, so deduplication and log-tail reconciliation are best-effort. Do not promise exactly-once delivery or a complete log replay after a long outage.
- Keep UI actions on the documented REST contract. Worker/lease views are observational/read-only.
- Do not expose raw server exceptions or secrets in the UI. Log safe diagnostics, show a concise user-facing error, and retain redaction behavior from the server.
- Do not import `src/core/` from UI components, call internal service functions, or add GUI dependencies to `nvcode_client`.

## 7. Validation checklist

- [ ] Subscriber callbacks enqueue work and never directly touch GUI widgets.
- [ ] Initial snapshots render even when no events have arrived.
- [ ] `LIVE`, `DEGRADED`, `RECONNECT_WAIT`, and `OFFLINE` are visible and do not erase last known data.
- [ ] Unsubscribe and shutdown stop future rendering callbacks cleanly.
- [ ] Queue/log rendering is bounded and does not freeze on bursts.
- [ ] Task submission reports server acceptance/rejection and does not imply multi-turn chat support.
- [ ] All UI tests run without a real server; protocol integration tests remain separate.
- [ ] Changes remain outside `src/core/` and the legacy Tkinter code.
