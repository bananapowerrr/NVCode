# -*- coding: utf-8 -*-
"""Localhost Protocol v0 HTTP server (stdlib).

  python -m protocol.server
  → http://127.0.0.1:8340/v1/health
  → http://127.0.0.1:8340/  (debug UI)

WebSocket: optional upgrade on GET /v1/events (minimal text frames).
SSE fallback: GET /v1/events/stream
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from protocol import event_bridge
from protocol import rest_handlers as rh
from protocol.envelope import make_envelope

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8340
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class ProtocolHandler(BaseHTTPRequestHandler):
    server_version = "NVCodeProtocol/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send_json(self, code: int, obj: Any) -> None:
        raw = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": str(path.name)}})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        if path == "/v1/events" and self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_websocket()
            return
        if path == "/v1/events/stream":
            self._handle_sse()
            return
        if path in ("/v1/health", "/health"):
            self._send_json(200, rh.health())
            return
        if path in ("/v1/status", "/api/status"):
            self._send_json(200, rh.status())
            return
        if path == "/v1/tasks":
            state = (qs.get("state") or [None])[0]
            channel = (qs.get("channel") or [None])[0]
            try:
                limit = int((qs.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            self._send_json(200, rh.list_tasks(state=state, channel=channel, limit=limit))
            return
        if path.startswith("/v1/tasks/"):
            tid = path.split("/v1/tasks/", 1)[1].strip("/")
            if tid:
                self._send_json(200, rh.get_task(tid))
                return
        if path == "/v1/workers":
            self._send_json(200, rh.list_workers())
            return
        if path == "/v1/leases":
            self._send_json(200, rh.list_leases())
            return
        if path.startswith("/v1/leases/"):
            tid = path.split("/v1/leases/", 1)[1].strip("/")
            self._send_json(200, rh.get_lease(tid))
            return
        if path == "/v1/metrics":
            self._send_json(200, rh.metrics())
            return
        if path == "/v1/logs/tail":
            try:
                n = int((qs.get("n") or ["200"])[0])
            except ValueError:
                n = 200
            self._send_json(200, rh.logs_tail(n=n))
            return
        if path in ("/", "/index.html"):
            index = STATIC_DIR / "index.html"
            if index.is_file():
                self._send_file(index, "text/html; charset=utf-8")
                return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            candidate = (STATIC_DIR / rel).resolve()
            try:
                candidate.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self._send_json(403, {"ok": False, "error": {"code": "FORBIDDEN", "message": "path"}})
                return
            if candidate.is_file():
                ctype = "application/octet-stream"
                if candidate.suffix == ".html":
                    ctype = "text/html; charset=utf-8"
                elif candidate.suffix == ".js":
                    ctype = "application/javascript; charset=utf-8"
                elif candidate.suffix == ".css":
                    ctype = "text/css; charset=utf-8"
                self._send_file(candidate, ctype)
                return
        self._send_json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": path}})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = self._read_json()
        if path in ("/v1/tasks", "/api/task"):
            code, obj = rh.create_task(body)
            self._send_json(code, obj)
            return
        self._send_json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": path}})

    def _handle_sse(self) -> None:
        event_bridge.BRIDGE.attach_bus()
        q = event_bridge.BRIDGE.register_client()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            hello = make_envelope("runtime.health", {"ok": True, "stream": "sse"})
            self.wfile.write(f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    env = q.get(timeout=15.0)
                    self.wfile.write(f"data: {json.dumps(env, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
        finally:
            event_bridge.BRIDGE.unregister_client(q)

    def _handle_websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        event_bridge.BRIDGE.attach_bus()
        q = event_bridge.BRIDGE.register_client()
        sock = self.connection
        try:
            hello = make_envelope("runtime.health", {"ok": True, "stream": "websocket"})
            _ws_send_text(sock, json.dumps(hello, ensure_ascii=False))
            while True:
                try:
                    env = q.get(timeout=20.0)
                    _ws_send_text(sock, json.dumps(env, ensure_ascii=False))
                except Exception:
                    try:
                        _ws_send_text(
                            sock,
                            json.dumps(make_envelope("runtime.health", {"ok": True, "ping": True})),
                        )
                    except Exception:
                        break
        finally:
            event_bridge.BRIDGE.unregister_client(q)


def _ws_send_text(sock: Any, text: str) -> None:
    data = text.encode("utf-8")
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(n.to_bytes(2, "big"))
    else:
        header.append(127)
        header.extend(n.to_bytes(8, "big"))
    sock.sendall(header + data)


class ProtocolServer(ThreadingHTTPServer):
    allow_reuse_address = True


def run_protocol_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"
    event_bridge.BRIDGE.attach_bus()
    httpd = ProtocolServer((host, port), ProtocolHandler)
    print(f"NVCode protocol v0 on http://{host}:{port}/v1/health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NVCode Protocol v0 server")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args(argv)
    return run_protocol_server(host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
