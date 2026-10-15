# NVCode Protocol v0

UI-independent localhost API.

## Run

```bash
PYTHONPATH=.:src python -m protocol.server --port 8340
# http://127.0.0.1:8340/v1/health
```

## Endpoints

- `GET /v1/health` `/v1/status` `/v1/tasks` `/v1/workers` `/v1/leases` `/v1/metrics` `/v1/logs/tail`
- `POST /v1/tasks` — intake only (`core.task_service.submit`)
- `GET /v1/events` WebSocket · `GET /v1/events/stream` SSE

## Spec

See `protocol/openapi.yaml` (OpenAPI 3).

## CLI client

```bash
PYTHONPATH=.:src python -m protocol.client --base http://127.0.0.1:8340 health
PYTHONPATH=.:src python -m protocol.client submit "Fix the bug"
PYTHONPATH=.:src python -m protocol.client tasks
PYTHONPATH=.:src python -m protocol.client logs --n 50
PYTHONPATH=.:src python -m protocol.client watch --max-events 5
```

Does not modify core/reclaim/executor/Tkinter.
