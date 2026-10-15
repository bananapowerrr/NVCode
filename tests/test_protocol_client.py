# -*- coding: utf-8 -*-
"""Tests for protocol.client.ProtocolClient against live protocol server."""
from __future__ import annotations

import json
import threading
import time
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
    from protocol.client import ProtocolClient

    client = ProtocolClient(base_url=base, timeout=3.0)
    for _ in range(50):
        try:
            h = client.health()
            if h.get("ok"):
                break
        except Exception:
            time.sleep(0.05)
    yield base, client
    httpd.shutdown()
    httpd.server_close()


def test_client_health_status_tasks(protocol_server):
    base, client = protocol_server
    h = client.health()
    assert h.get("ok") is True
    assert h.get("service") == "nvcode-protocol"
    st = client.status()
    assert "queues" in st
    tasks = client.list_tasks(limit=10)
    assert tasks.get("ok") is True
    assert isinstance(tasks.get("tasks"), list)


def test_client_submit_roundtrip_mock(protocol_server):
    base, client = protocol_server
    with mock.patch("core.task_service.submit", return_value=("cli-task-1", None)):
        code, body = client.submit("hello from cli")
    assert code == 200
    assert body["ok"] is True
    assert body["task_id"] == "cli-task-1"


def test_client_submit_empty_fails(protocol_server):
    base, client = protocol_server
    code, body = client.submit("")
    assert code == 400
    assert body.get("ok") is False


def test_client_metrics_logs_workers_leases(protocol_server):
    base, client = protocol_server
    assert client.metrics().get("ok") is True
    assert client.logs_tail(n=5).get("ok") is True
    assert client.workers().get("ok") is True
    assert client.leases().get("ok") is True


def test_client_watch_sse_one_event(protocol_server):
    base, client = protocol_server
    events = []

    def on_event(data: str) -> None:
        events.append(data)

    n = client.watch_sse(max_events=1, on_event=on_event)
    assert n == 1
    assert events
    env = json.loads(events[0])
    assert env.get("v") == 1
    assert "type" in env


def test_cli_main_health(protocol_server, capsys):
    base, client = protocol_server
    from protocol.client import main

    rc = main(["--base", base, "health"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nvcode-protocol" in out
