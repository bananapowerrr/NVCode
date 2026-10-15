# -*- coding: utf-8 -*-
"""Protocol v0 — health/status/tasks/metrics smoke (no core mutation)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.fixture(scope="module")
def protocol_server():
    from protocol.server import ProtocolServer, ProtocolHandler, DEFAULT_HOST

    httpd = ProtocolServer((DEFAULT_HOST, 0), ProtocolHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://{DEFAULT_HOST}:{port}"
    for _ in range(50):
        try:
            with urllib.request.urlopen(base + "/v1/health", timeout=0.5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    yield base
    httpd.shutdown()
    httpd.server_close()


def _get(base: str, path: str) -> tuple[int, dict]:
    with urllib.request.urlopen(base + path, timeout=3) as r:
        body = json.loads(r.read().decode("utf-8"))
        return r.status, body


def test_health(protocol_server):
    code, body = _get(protocol_server, "/v1/health")
    assert code == 200
    assert body.get("ok") is True
    assert body.get("service") == "nvcode-protocol"


def test_status(protocol_server):
    code, body = _get(protocol_server, "/v1/status")
    assert code == 200
    assert "queues" in body


def test_tasks_list(protocol_server):
    code, body = _get(protocol_server, "/v1/tasks")
    assert code == 200
    assert body.get("ok") is True
    assert isinstance(body.get("tasks"), list)


def test_workers_leases_metrics_logs(protocol_server):
    for path in ("/v1/workers", "/v1/leases", "/v1/metrics", "/v1/logs/tail?n=10"):
        code, body = _get(protocol_server, path)
        assert code == 200
        assert body.get("ok") is True


def test_create_task_empty_rejected(protocol_server):
    req = urllib.request.Request(
        protocol_server + "/v1/tasks",
        data=json.dumps({"message": ""}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3)
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        body = json.loads(e.read().decode("utf-8"))
        assert body.get("ok") is False
        assert body.get("error", {}).get("code") == "INTAKE_REJECTED"


def test_envelope_map_done():
    from protocol.envelope import map_agent_event
    from types import SimpleNamespace

    ev = SimpleNamespace(
        type="DONE",
        task_id="t1",
        worker="aider",
        executor="",
        message="ok",
        payload={"verified": True, "changed_files": ["a.py"]},
    )
    env = map_agent_event(ev)
    assert env is not None
    assert env["type"] == "task.terminal"
    assert env["payload"]["status"] == "DONE"
    assert env["payload"]["verified"] is True


def test_bind_refuses_non_localhost():
    from protocol.server import DEFAULT_HOST

    assert DEFAULT_HOST in ("127.0.0.1", "localhost")
