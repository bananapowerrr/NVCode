# -*- coding: utf-8 -*-
"""Protocol cancel endpoint tests."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.fixture
def bus_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBUS_ROOT", str(tmp_path))
    monkeypatch.setenv("NVCODE_ROOT", str(tmp_path))
    ch = tmp_path / "channels" / "desktop"
    for st in ("incoming", "processing", "done", "errors", "deferred"):
        (ch / st).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def protocol_server(bus_root):
    from protocol.server import ProtocolServer, ProtocolHandler, DEFAULT_HOST

    httpd = ProtocolServer((DEFAULT_HOST, 0), ProtocolHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://{DEFAULT_HOST}:{port}"
    for _ in range(40):
        try:
            with urllib.request.urlopen(base + "/v1/health", timeout=0.5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    yield base, bus_root
    httpd.shutdown()
    httpd.server_close()


def _post(base, path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_cancel_incoming_moves_to_deferred(protocol_server):
    base, root = protocol_server
    tid = "cancel-me-1"
    path = root / "channels" / "desktop" / "incoming" / f"{tid}.json"
    path.write_text(json.dumps({"id": tid, "message": "x", "status": "PENDING"}), encoding="utf-8")

    code, body = _post(base, f"/v1/tasks/{tid}/cancel", {"reason": "test"})
    assert code == 200, body
    assert body["ok"] is True
    assert body["status"] == "CANCELLED"
    assert body["folder"] == "deferred"
    assert not path.exists()
    dst = root / "channels" / "desktop" / "deferred" / f"{tid}.json"
    assert dst.is_file()
    data = json.loads(dst.read_text(encoding="utf-8"))
    assert data.get("cancelled") is True


def test_cancel_missing_404(protocol_server):
    base, root = protocol_server
    code, body = _post(base, "/v1/tasks/no-such-task/cancel", {})
    assert code == 404
    assert body["ok"] is False


def test_cancel_done_409(protocol_server):
    base, root = protocol_server
    tid = "already-done"
    path = root / "channels" / "desktop" / "done" / f"{tid}.json"
    path.write_text(json.dumps({"id": tid, "status": "DONE"}), encoding="utf-8")
    code, body = _post(base, f"/v1/tasks/{tid}/cancel", {})
    assert code == 409
    assert body["error"]["code"] == "ALREADY_TERMINAL"


def test_client_cancel(protocol_server):
    base, root = protocol_server
    tid = "cli-cancel"
    (root / "channels" / "desktop" / "incoming" / f"{tid}.json").write_text(
        json.dumps({"id": tid, "message": "y"}), encoding="utf-8"
    )
    from protocol.client import ProtocolClient

    client = ProtocolClient(base, timeout=5)
    code, body = client.cancel(tid, reason="cli")
    assert code == 200
    assert body["ok"] is True
