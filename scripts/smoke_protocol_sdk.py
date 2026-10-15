#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end smoke: protocol server + ProtocolClient full cycle.

Exit 0 on success. Does not mutate core/reclaim/executor.

  PYTHONPATH=.:src python scripts/smoke_protocol_sdk.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _fail(msg: str) -> int:
    print(f"SMOKE FAIL: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    from protocol.server import ProtocolServer, ProtocolHandler, DEFAULT_HOST
    from protocol.client import ProtocolClient

    httpd = ProtocolServer((DEFAULT_HOST, 0), ProtocolHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://{DEFAULT_HOST}:{port}"
    client = ProtocolClient(base_url=base, timeout=5.0)

    try:
        health = None
        for _ in range(50):
            try:
                health = client.health()
                if health.get("ok"):
                    break
            except Exception:
                time.sleep(0.05)
        if not health or not health.get("ok"):
            return _fail("health not ready")
        print("OK health", health.get("service"))

        status = client.status()
        if "queues" not in status:
            return _fail("status missing queues")
        print("OK status")

        with mock.patch("core.task_service.submit", return_value=("smoke-task-1", None)):
            code, body = client.submit("smoke test message", project="/tmp/smoke")
        if code != 200 or not body.get("ok") or body.get("task_id") != "smoke-task-1":
            return _fail(f"submit failed: {code} {body}")
        print("OK submit", body.get("task_id"))

        tasks = client.list_tasks(limit=10)
        if not tasks.get("ok"):
            return _fail(f"tasks: {tasks}")
        print("OK tasks count", tasks.get("count"))

        for name, fn in (
            ("metrics", client.metrics),
            ("logs", lambda: client.logs_tail(n=5)),
            ("workers", client.workers),
            ("leases", client.leases),
        ):
            r = fn()
            if not r.get("ok"):
                return _fail(f"{name}: {r}")
            print(f"OK {name}")

        events: list[str] = []
        n = client.watch_sse(max_events=1, on_event=events.append)
        if n != 1 or not events:
            return _fail(f"sse events={n} data={events!r}")
        env = json.loads(events[0])
        if env.get("v") != 1 or "type" not in env:
            return _fail(f"bad envelope: {env}")
        print("OK sse", env.get("type"))

        print("SMOKE PASS")
        return 0
    except Exception as e:
        return _fail(repr(e))
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
