# NVCode Protocol v0

UI-independent localhost API for NVCode. The GUI (future VS Code–like client, CLI, or scripts) talks **only** to this layer — never imports `src/core` reclaim/executor/FSM or Tkinter.

| Piece | Path |
|-------|------|
| Server | `protocol/server.py` |
| REST handlers | `protocol/rest_handlers.py` |
| Event bridge | `protocol/event_bridge.py` |
| Python SDK + CLI | `protocol/client.py` |
| OpenAPI 3 | `protocol/openapi.yaml` |

---

## Run the server

```bash
PYTHONPATH=.:src python -m protocol.server --port 8340
# → http://127.0.0.1:8340/v1/health
```

Bind is forced to loopback (`127.0.0.1` / `localhost` / `::1`).

---

## REST endpoints (summary)

| Method | Path | Notes |
|--------|------|--------|
| GET | `/v1/health` | liveness |
| GET | `/v1/status` | queues, policy |
| GET | `/v1/tasks` | `?state=&channel=&limit=` |
| GET | `/v1/tasks/{id}` | snapshot + lease |
| POST | `/v1/tasks` | **intake only** (`core.task_service.submit`) |
| GET | `/v1/workers` | read-only |
| GET | `/v1/leases` | read-only |
| GET | `/v1/metrics` | counters snapshot |
| GET | `/v1/logs/tail` | `?n=` redacted tail |
| GET | `/v1/events` | WebSocket upgrade |
| GET | `/v1/events/stream` | SSE |

Full schema: [`openapi.yaml`](./openapi.yaml).

---

## Python SDK — `ProtocolClient`

Use this class from a future UI or scripts instead of raw `urllib`/`fetch`.

```python
from protocol.client import ProtocolClient

client = ProtocolClient(base_url="http://127.0.0.1:8340", timeout=10.0)

# Health / status
print(client.health())     # {"ok": true, "service": "nvcode-protocol", ...}
print(client.status())     # queues, policy, root

# Tasks
print(client.list_tasks(state="processing", limit=20))
print(client.get_task("some-task-id"))

# Create task (goes through intake — fail-closed)
code, body = client.submit(
    "Fix the null check in auth.py",
    project="/path/to/project",
    channel="desktop",
)
# code == 200, body == {"ok": true, "task_id": "...", "status": "PENDING"}

# Observability (read-only)
print(client.metrics())
print(client.logs_tail(n=50))
print(client.workers())
print(client.leases())
```

### Real-time events (SSE)

```python
def on_event(raw_json: str) -> None:
    import json
    env = json.loads(raw_json)
    # env: {"v": 1, "type": "task.status"|"task.terminal"|"log.line"|..., "payload": {...}}
    print(env["type"], env.get("payload"))

# Block until N events (or Ctrl+C if max_events=0)
client.watch_sse(max_events=20, on_event=on_event)
```

Envelope shape (Protocol v0):

```json
{
  "v": 1,
  "type": "task.terminal",
  "ts": "2026-10-15T00:00:00.000Z",
  "payload": {
    "task_id": "...",
    "status": "DONE",
    "verified": true,
    "changed_files": ["a.py"]
  }
}
```

WebSocket endpoint `/v1/events` is available on the server; the shipped client uses **SSE** (`watch_sse`) for simplicity and stdlib-only streaming. UI stacks can open WS directly using the same envelope schema.

### Method reference

| Method | Maps to | Returns |
|--------|---------|---------|
| `health()` | `GET /v1/health` | `dict` |
| `status()` | `GET /v1/status` | `dict` |
| `list_tasks(...)` | `GET /v1/tasks` | `dict` |
| `get_task(id)` | `GET /v1/tasks/{id}` | `dict` |
| `submit(message, ...)` | `POST /v1/tasks` | `(http_code, dict)` |
| `metrics()` | `GET /v1/metrics` | `dict` |
| `logs_tail(n)` | `GET /v1/logs/tail` | `dict` |
| `workers()` | `GET /v1/workers` | `dict` |
| `leases()` | `GET /v1/leases` | `dict` |
| `watch_sse(...)` | `GET /v1/events/stream` | `int` (events received) |

---

## CLI

Same module, argparse front-end:

```bash
PYTHONPATH=.:src python -m protocol.client --base http://127.0.0.1:8340 health
PYTHONPATH=.:src python -m protocol.client status
PYTHONPATH=.:src python -m protocol.client tasks --state processing
PYTHONPATH=.:src python -m protocol.client task <task_id>
PYTHONPATH=.:src python -m protocol.client submit "Fix the bug" --project /path
PYTHONPATH=.:src python -m protocol.client logs --n 50
PYTHONPATH=.:src python -m protocol.client metrics
PYTHONPATH=.:src python -m protocol.client workers
PYTHONPATH=.:src python -m protocol.client leases
PYTHONPATH=.:src python -m protocol.client watch --max-events 10
```

---

## Guarantees (v0)

- **No Tkinter / no UI imports** in this package.
- **No writes to leases/workers** via API (read-only).
- **Task create** only through existing intake (`submit` / `task_service`).
- **Localhost** bind by default.

For UI work: depend on `ProtocolClient` + OpenAPI, not on `src/core/*` internals.
