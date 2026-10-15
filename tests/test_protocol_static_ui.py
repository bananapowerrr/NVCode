# -*- coding: utf-8 -*-
"""Static debug UI is served at GET / without breaking REST."""
from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest


@pytest.fixture
def protocol_server():
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


def test_root_serves_html_dashboard(protocol_server):
    with urllib.request.urlopen(protocol_server + "/", timeout=3) as r:
        assert r.status == 200
        ctype = r.headers.get("Content-Type", "")
        assert "text/html" in ctype
        body = r.read().decode("utf-8")
    assert "NVCode Protocol" in body
    assert "EventSource" in body
    assert "/v1/events/stream" in body


def test_static_index_and_rest_still_ok(protocol_server):
    with urllib.request.urlopen(protocol_server + "/static/index.html", timeout=3) as r:
        assert r.status == 200
    with urllib.request.urlopen(protocol_server + "/v1/health", timeout=3) as r:
        data = json.loads(r.read().decode("utf-8"))
    assert data.get("ok") is True
