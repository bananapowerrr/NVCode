# -*- coding: utf-8 -*-
"""Logs tail filter query params."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

import pytest


@pytest.fixture
def log_server(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBUS_ROOT", str(tmp_path))
    monkeypatch.setenv("NVCODE_ROOT", str(tmp_path))
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "runtime.log").write_text(
        "\n".join(
            [
                "INFO boot start",
                "ERROR task-abc failed verification",
                "DEBUG task-abc detail",
                "INFO task-xyz ok",
                "WARNING something else",
                "ERROR task-xyz crash",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
    yield base
    httpd.shutdown()
    httpd.server_close()


def _get(base, path, **q):
    qs = urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})
    url = base + path + (("?" + qs) if qs else "")
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_logs_filter_by_task_id(log_server):
    body = _get(log_server, "/v1/logs/tail", n=50, task_id="task-abc")
    assert body["ok"] is True
    assert body["count"] >= 1
    assert all("task-abc" in ln for ln in body["lines"])
    assert body.get("filters", {}).get("task_id") == "task-abc"


def test_logs_filter_by_level_error(log_server):
    body = _get(log_server, "/v1/logs/tail", n=50, level="ERROR")
    assert body["ok"] is True
    assert body["count"] >= 1
    for ln in body["lines"]:
        assert "ERROR" in ln.upper()


def test_logs_filter_task_and_level(log_server):
    body = _get(log_server, "/v1/logs/tail", n=50, task_id="task-xyz", level="ERROR")
    assert body["ok"] is True
    assert body["count"] == 1
    assert "task-xyz" in body["lines"][0]
    assert "ERROR" in body["lines"][0]


def test_client_logs_tail_filters(log_server):
    from protocol.client import ProtocolClient

    c = ProtocolClient(log_server, timeout=5)
    body = c.logs_tail(n=20, task_id="task-abc", level="DEBUG")
    assert body["ok"] is True
    assert any("DEBUG" in ln and "task-abc" in ln for ln in body["lines"])
