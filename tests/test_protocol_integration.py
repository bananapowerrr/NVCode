# -*- coding: utf-8 -*-
"""Protocol v0 integration tests (mock intake, real HTTP server, no core mutation)."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
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


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=3) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(base: str, path: str, body: dict):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_post_create_task_uses_intake_mock(protocol_server):
    """POST /v1/tasks must call task_service.submit (not write bus directly)."""
    with mock.patch("core.task_service.submit", return_value=("task-mock-1", None)) as submit:
        code, body = _post(
            protocol_server,
            "/v1/tasks",
            {"message": "fix something", "project": "/tmp/p"},
        )
    assert code == 200
    assert body["ok"] is True
    assert body["task_id"] == "task-mock-1"
    assert body["status"] == "PENDING"
    submit.assert_called_once()
    args, kwargs = submit.call_args
    assert args[0] == "fix something"


def test_post_create_task_intake_error(protocol_server):
    with mock.patch("core.task_service.submit", return_value=(None, "rejected by policy")):
        code, body = _post(protocol_server, "/v1/tasks", {"message": "x"})
    assert code == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "INTAKE_REJECTED"


def test_get_task_not_found(protocol_server):
    code, body = _get(protocol_server, "/v1/tasks/does-not-exist-xyz")
    assert code == 200
    assert body.get("ok") is False
    assert body.get("error", {}).get("code") == "NOT_FOUND"


def test_readonly_workers_leases_metrics_logs(protocol_server):
    for path in ("/v1/workers", "/v1/leases", "/v1/metrics", "/v1/logs/tail?n=5"):
        code, body = _get(protocol_server, path)
        assert code == 200
        assert body.get("ok") is True
    req = urllib.request.Request(
        protocol_server + "/v1/leases",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
        assert False, "POST leases should not succeed"
    except urllib.error.HTTPError as e:
        assert e.code in (404, 405)


def test_sse_stream_hello(protocol_server):
    """Raw socket: SSE responds 200 + event-stream + first data line."""
    from urllib.parse import urlparse

    u = urlparse(protocol_server)
    sock = socket.create_connection((u.hostname, u.port), timeout=2)
    sock.sendall(
        b"GET /v1/events/stream HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Accept: text/event-stream\r\n"
        b"Connection: close\r\n\r\n"
    )
    sock.settimeout(1.5)
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"data:" in data:
                break
    except (TimeoutError, socket.timeout):
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    text = data.decode("utf-8", errors="replace")
    assert "200" in text.split("\r\n", 1)[0]
    assert "text/event-stream" in text
    assert "data:" in text
    assert "runtime.health" in text or "ok" in text


def test_openapi_file_exists():
    root = Path(__file__).resolve().parents[1]
    p = root / "protocol" / "openapi.yaml"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "openapi: 3" in text
    assert "/v1/tasks" in text
    assert "/v1/events" in text


def test_bridge_broadcast_to_client_queue():
    from protocol.event_bridge import EventBridge
    from protocol.envelope import make_envelope

    br = EventBridge(max_queue=8)
    q = br.register_client()
    br.broadcast(make_envelope("metric.tick", {"counters": {"ok": 1}}))
    env = q.get(timeout=1)
    assert env["type"] == "metric.tick"
    br.unregister_client(q)


def test_get_task_after_mock_create_roundtrip(protocol_server, tmp_path, monkeypatch):
    """Simulate create + get by placing a task JSON where handlers look."""
    ch = tmp_path / "channels" / "desktop" / "incoming"
    ch.mkdir(parents=True)
    task_id = "roundtrip-1"
    payload = {
        "id": task_id,
        "message": "hello",
        "status": "PENDING",
        "attempts": 0,
    }
    (ch / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("AGENTBUS_ROOT", str(tmp_path))
    monkeypatch.setenv("NVCODE_ROOT", str(tmp_path))

    code, body = _get(protocol_server, f"/v1/tasks/{task_id}")
    assert code == 200
    assert body.get("ok") is True
    assert body["task"]["task_id"] == task_id
    assert body["task"]["folder"] == "incoming"
