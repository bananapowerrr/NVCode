# NVCode Client SDK — Integration and Release Guide

This guide covers the current end-to-end development setup for the `nvcode_client` SDK, the Protocol v0 server, its bundled static web debug page, and the interactive AppShell terminal demo. All client communication goes through public Protocol v0 HTTP/WebSocket endpoints; the SDK does not import `src/core/` or the legacy Tkinter UI.

## 1. Repository and environment

Use Python 3.11 or newer. From a clean repository checkout:

```bash
git clone https://github.com/bananapowerrr/NVCode.git
cd NVCode
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The full repository dependencies are for the server/runtime. The SDK itself uses the Python standard library and has no third-party runtime dependencies.

## 2. Install the SDK as an independent package

The standalone packaging metadata lives in `nvcode_client/pyproject.toml`. From the repository root:

```bash
python -m pip install -e ./nvcode_client
```

Editable installation is intended for SDK development. It installs the `nvcode-client-demo`, `nvcode-interactive-demo`, and `nvcode-app-shell` entry points. The source-tree commands below also work without installing the SDK, provided the current working directory is the repository root.

Build distributable artifacts from the SDK directory:

```bash
python -m pip install build
python -m build --wheel --sdist
```

Expected artifacts are written to `nvcode_client/dist/`. To smoke-test the wheel in a clean virtual environment, install the generated wheel and run `nvcode-client-demo` (mock mode) and `nvcode-interactive-demo --help`. Keep generated `dist/`, `build/`, and `*.egg-info` out of the source diff.

The public API is exported by `nvcode_client/__init__.py`; core entry points include `ProtocolClient`, `StateStore`, `AppShell`, `UiThreadDispatcher`, and the four panel controllers. No external `requests`, `websockets`, or SSE package is required by this SDK build.

## 3. Start the Protocol v0 server and bundled static Web UI

Open a terminal at the repository root with the virtual environment active:

```bash
PYTHONPATH=.:src python -m protocol.server --port 8340
```

Check liveness from another terminal:

```bash
curl http://127.0.0.1:8340/v1/health
curl http://127.0.0.1:8340/v1/status
curl http://127.0.0.1:8340/v1/tasks
```

Open **http://127.0.0.1:8340/** in a browser. The static debug UI is served from `protocol/static/index.html` by the same Protocol server. It polls status, metrics, tasks, and redacted log tails and subscribes to `/v1/events/stream` via SSE. In the current repository there is no separate JavaScript package manifest or npm/Vite dev-server command: the Protocol server is also the local static UI server. Do not assume an independent frontend build step exists.

The API binds to loopback by default. Keep it local; do not expose it to an untrusted network. The static page's Submit action and live SDK submission both create real tasks via `POST /v1/tasks`.

### Runtime / worker process (optional for UI-only checks)

The Protocol server exposes status and intake, but task execution depends on the normal NVCode runtime. If end-to-end task processing is required, start the runtime in a separate terminal using the repository's documented command:

```bash
python dispatcher.py
```

Run this only in a prepared development environment. For API/UI smoke tests, the Protocol server can be checked separately; an accepted task is not proof that a worker completed it.

## 4. Exercise the client SDK

### Safe offline demos

These modes use deterministic in-memory mocks and do not require a server or create real work:

```bash
python -m nvcode_client.demo
python -m nvcode_client.interactive_demo
```

The interactive demo supports `help`, `status`, `tasks`, `workers`, `logs`, `submit <message>`, `simulate` (mock only), and `quit`. In mock mode, `submit` only mutates the fake Protocol facade.

### Live integration against the local server

With the Protocol server running:

```bash
python -m nvcode_client.interactive_demo --live
# Or use the installed command:
nvcode-interactive-demo --live
```

Live mode connects to `http://127.0.0.1:8340` and `ws://127.0.0.1:8340/v1/events`. You can override them with `--base-url` and `--ws-url`. In live mode, entering `submit <message>` creates real work. The non-interactive demo's `--live` mode also submits a task, so only use it when that side effect is intended.

To run the framework-neutral AppShell directly:

```bash
python -m nvcode_client.app_shell --chat-task-id TASK_ID
# Explicitly opt into a real task submission:
python -m nvcode_client.app_shell --submit "Describe the project" --project /path/to/project
```

## 5. GUI framework wiring and thread safety

Prefer `AppShell` when integrating a graphical frontend. Construct it on the GUI thread and provide `post_to_ui(callback)`, which must safely enqueue work onto the GUI event loop. It must not execute the callback inline on a network/background thread.

- **Qt / PySide:** use a queued signal connected to a slot on the GUI thread.
- **Tk / CustomTkinter:** bridge callbacks into the main event loop (for example, queue work and drain it from `root.after(...)`); never update widgets directly from a WebSocket thread.
- **Web wrapper:** use the UI framework's event dispatcher and keep browser-side network access within the same-origin/server policy.

Render from `AppShell.on_panel_update` or read `shell.panel_state` on the UI thread. On window close call `shell.stop()`; detach any extra subscriptions and cancel frontend timers. Controller notifications are synchronous on the event-processing thread unless wrapped by AppShell's dispatcher.

See [APP_SHELL_GUIDE.md](APP_SHELL_GUIDE.md) for the panel view-model contract and framework-specific wiring examples.

## 6. Validation and release gate

From the repository root:

```bash
python -m compileall -q nvcode_client tests
python -m pytest -q tests/test_ui_client.py tests/test_client_server_integration.py
python -m pytest -q tests/test_app_shell.py tests/test_ui_adapter.py tests/test_interactive_demo.py
python -m pytest -q
git diff --check
git diff --name-only origin/main...HEAD
```

From `nvcode_client/`, additionally validate packaging:

```bash
python -m pip install -e .
python -m build --wheel --sdist
```

Before marking a PR Ready for Review or merging:

1. Run the targeted client, controller, AppShell, demo, and server integration tests.
2. Run the full repository suite and record the actual exit status.
3. Build the SDK wheel and source distribution, then smoke-test installation in a clean environment.
4. Confirm the diff contains only the intended SDK, tests, packaging configuration, and documentation.
5. Confirm no `src/core/` files (including runtime, reclaim, executor) or legacy Tkinter files changed.
6. Inspect CI results and have the repository owner perform the merge.

Do not report tests or package builds as passing unless those commands have actually completed successfully.

## 7. Protocol v0 caveats

- `POST /v1/tasks` is task intake, not an append-to-existing-chat API. `ChatController.send_message` submits a new task; `metadata.related_task_id` is informational.
- Workers and leases are read-only API snapshots.
- WebSocket/SSE event envelopes do not currently guarantee a stable event ID or sequence number. Dedupe and REST reconciliation are best-effort, not exactly-once delivery.
- `/v1/logs/tail` is a bounded redacted tail, not a replay cursor for the complete log history.
