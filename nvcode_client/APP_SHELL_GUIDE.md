# Reference App Shell

The reference shell connects the Protocol v0 client to any frontend without depending on a particular GUI toolkit. It owns the client, store, four panel controllers, UI-thread dispatcher, and view-model projection.

## Run the console reference

Start the Protocol v0 server on localhost, then run:

```bash
python -m nvcode_client.app_shell
```

To submit one real task after the initial connection:

```bash
python -m nvcode_client.app_shell --submit "Summarize the current project" --project /path/to/project
```

To scope the chat panel to an existing task:

```bash
python -m nvcode_client.app_shell --chat-task-id TASK_ID
```

Options `--base-url` and `--ws-url` override the local REST and WebSocket URLs. The console example prints JSON panel snapshots as newline-delimited output. Task submission is opt-in because it creates real work.

## Embed in a desktop frontend

Construct the shell on the GUI thread and provide a thread-safe event-loop scheduling function. That function must enqueue callbacks to the main thread; it must not execute a callback inline when called from a background thread.

```python
from nvcode_client import AppShell

def on_panel_update(panel_name, view_model_snapshot):
    # Called on the UI thread. Adapt this to your widgets/model binding.
    if panel_name == "queue":
        queue_view.set_tasks(view_model_snapshot["tasks"])
    elif panel_name == "workers":
        worker_view.set_workers(view_model_snapshot["workers"])
    elif panel_name == "logs_metrics":
        log_view.set_entries(view_model_snapshot["logs"])
        metrics_view.set_metrics(view_model_snapshot["metrics"])

shell = AppShell(
    post_to_ui=gui_event_loop_post,
    on_panel_update=on_panel_update,
    chat_task_id="optional-task-id",
)
shell.start()

# From a user action, call shell.submit_task(...).
# On application close, call shell.stop().
```

`gui_event_loop_post` is framework-specific. Examples:
- **Qt / PySide:** emit a queued signal connected to a slot on the GUI thread; do not use a direct cross-thread widget call.
- **Tk / CustomTkinter:** schedule through the UI loop (for example, `root.after(0, callback)`) using a thread-safe bridge; avoid touching widgets from the WebSocket thread.
- **Other toolkits:** use the toolkit's documented thread-safe dispatcher or event-loop posting API.

The shell converts raw controller snapshots to these view-model shapes:
- `chat`: selected task as `TaskViewModel | None`, scoped event/log tuples, connection state.
- `queue`: tuple of `TaskViewModel`, count, connection state.
- `workers`: tuple of `WorkerViewModel`, lease records, connection state.
- `logs_metrics`: log tuple, immutable `MetricsViewModel`, connection state.

`shell.panel_state` is intended to be read on the UI thread. The `on_panel_update` callback is invoked on the dispatcher/UI thread, and should update the relevant view model or widget. Keep expensive formatting/rendering out of the network callback itself.

## Lifecycle and error handling

1. Construct the shell on the UI thread.
2. Call `start()` once; it begins REST synchronization and WebSocket reception.
3. Render only from `on_panel_update` (or inspect `panel_state` on the UI thread).
4. Submit tasks using `submit_task(message, project=..., channel=...)`; this uses public `POST /v1/tasks`.
5. Call `stop()` when closing the window. It stops the client, closes dispatcher subscriptions, and closes controllers. Repeated `stop()` calls are safe; a stopped shell is not restartable.
6. Treat `LIVE`, `DEGRADED`, `RECONNECT_WAIT`, and `OFFLINE` as connection information rather than task outcomes. REST snapshots and event deduplication are best-effort under Protocol v0's current envelope.

The shell does not import `src/core/` or the legacy Tkinter application. It communicates only through `ProtocolClient`, `StateStore`, and public Protocol v0 REST/WebSocket endpoints.

## Verify

```bash
python -m pytest -q tests/test_ui_adapter.py tests/test_ui_client.py tests/test_app_shell.py
python -m pytest -q
git diff --check
```

The test suite uses an injected client and deterministic fake event loop, so it does not require a live server or create real tasks.


## Interactive terminal demo

Run the AppShell-based interactive frontend in safe mock mode:

```bash
python -m nvcode_client.interactive_demo
```

The main thread drains the same UI dispatcher used by graphical frontends while a separate input reader waits for commands. All controller snapshots therefore pass through the AppShell dispatcher before being rendered. Use `tasks`, `workers`, and `logs` to inspect panel projections; `submit <message>` to exercise task intake; and `simulate` to produce mock task, worker, log, and metric events. This mode does not need a server and cannot create real work.

For a live server, opt in with:

```bash
python -m nvcode_client.interactive_demo --live
```

Optional `--base-url`, `--ws-url`, and `--chat-task-id` arguments select the Protocol v0 endpoints and task observed by the chat panel. In live mode, the `submit` command sends a real `POST /v1/tasks` request. Use this only when creating a real task is intended.

Automated mock-flow coverage:

```bash
python -m pytest -q tests/test_interactive_demo.py tests/test_app_shell.py
```
