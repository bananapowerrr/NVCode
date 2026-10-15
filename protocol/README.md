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

Does not modify core/reclaim/executor/Tkinter.
