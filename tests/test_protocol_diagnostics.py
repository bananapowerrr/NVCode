# -*- coding: utf-8 -*-
"""End-to-end protocol diagnostics: status consistency + log filters + cancel path."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest


@pytest.fixture
def diag_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBUS_ROOT", str(tmp_path))
    monkeypatch.setenv("NVCODE_ROOT", str(tmp_path))
    ch = tmp_path / "channels" / "desktop"
    for st in ("incoming", "processing", "done", "errors", "deferred"):
        (ch / st).mkdir(parents=True)
    (ch / "incoming" / "t-in.json").write_text(
        json.dumps({"id": "t-in", "message": "in", "status": "PENDING"}), encoding="utf-8"
    )
    (ch / "processing" / "t-proc.json").write_text(
        json.dumps({"id": "t-proc", "message": "run", "status": "PROCESSING"}), encoding="utf-8"
    )
    (ch / "processing" / "t-proc.lease.json").write_text(
        json.dumps({"worker_id": "w1", "phase": "execute", "last_heartbeat": time.time()}),
        encoding="utf-8",
    )
    (ch / "done" / "t-done.json").write_text(
        json.dumps({"id": "t-done", "status": "DONE", "verified": True}), encoding="utf-8"
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "runtime.log").write_text(
        "INFO start\nERROR t-proc failed\nDEBUG t-in detail\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def server(diag_env):
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
    yield base, diag_env
    httpd.shutdown()
    httpd.server_close()


def _get(base, path, **q):
    qs = urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})
    url = base + path + (("?" + qs) if qs else "")
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(base, path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_status_diagnostics_consistent_with_endpoints(server):
    base, root = server
    st = _get(base, "/v1/status")
    assert st["ok"] is True
    assert "queues" in st
    diag = st.get("diagnostics") or {}
    assert diag.get("incoming") == 1
    assert diag.get("processing") == 1
    assert diag.get("done") == 1
    assert diag.get("active_tasks") == 2
    assert diag.get("leases") == 1
    assert diag.get("workers_busy") == 1

    tasks = _get(base, "/v1/tasks", limit=20)
    assert tasks["ok"] is True
    ids = {t["task_id"] for t in tasks["tasks"]}
    assert "t-in" in ids and "t-proc" in ids

    leases = _get(base, "/v1/leases")
    assert leases["count"] == 1
    assert leases["leases"][0]["task_id"] == "t-proc"

    workers = _get(base, "/v1/workers")
    assert workers["count"] == 1
    assert workers["workers"][0]["worker_id"] == "w1"

    metrics = _get(base, "/v1/metrics")
    assert metrics["ok"] is True

    logs = _get(base, "/v1/logs/tail", n=20, task_id="t-proc", level="ERROR")
    assert logs["ok"] is True
    assert logs["count"] >= 1
    assert all("t-proc" in ln for ln in logs["lines"])


def test_cancel_then_status_updates(server):
    base, root = server
    code, body = _post(base, "/v1/tasks/t-in/cancel", {"reason": "diag"})
    assert code == 200, body
    st = _get(base, "/v1/status")
    diag = st["diagnostics"]
    assert diag["incoming"] == 0
    assert diag["deferred"] >= 1
    assert diag["active_tasks"] == 1


def test_client_status_has_diagnostics(server):
    base, root = server
    from protocol.client import ProtocolClient

    c = ProtocolClient(base, timeout=5)
    st = c.status()
    assert "diagnostics" in st
    assert st["diagnostics"]["protocol"] == "v0"
