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
