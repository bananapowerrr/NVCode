# -*- coding: utf-8 -*-
"""NVCode Protocol v0 CLI client (stdlib only).

Examples:
  python -m protocol.client health
  python -m protocol.client submit "Fix the bug"
  python -m protocol.client cancel <task_id>
  python -m protocol.client watch --max-events 5
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE = "http://127.0.0.1:8340"


class ProtocolClient:
    """Thin HTTP (+ optional SSE/WS) client for Protocol v0."""

    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = self.base + path
        if query:
            q = {k: v for k, v in query.items() if v is not None}
            if q:
                url += "?" + urllib.parse.urlencode(q)
        return url

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        req = urllib.request.Request(self._url(path, query), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post_json(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._url(path),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {"ok": False, "error": {"code": "HTTP", "message": str(e)}}
            return e.code, payload

    def health(self) -> dict[str, Any]:
        return self.get_json("/v1/health")

    def status(self) -> dict[str, Any]:
        return self.get_json("/v1/status")

    def list_tasks(
        self,
        state: str | None = None,
        channel: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.get_json(
            "/v1/tasks",
            {"state": state, "channel": channel, "limit": limit},
        )

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.get_json(f"/v1/tasks/{urllib.parse.quote(task_id)}")

    def submit(
        self,
        message: str,
        *,
        project: str = "",
        channel: str = "desktop",
        recipe: str = "",
    ) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {"message": message, "channel": channel}
        if project:
            body["project"] = project
        if recipe:
            body["recipe"] = recipe
        return self.post_json("/v1/tasks", body)

    def cancel(self, task_id: str, *, reason: str = "") -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {}
        if reason:
            body["reason"] = reason
        return self.post_json(f"/v1/tasks/{urllib.parse.quote(task_id)}/cancel", body)

    def metrics(self) -> dict[str, Any]:
        return self.get_json("/v1/metrics")

    def logs_tail(self, n: int = 100) -> dict[str, Any]:
        return self.get_json("/v1/logs/tail", {"n": n})

    def workers(self) -> dict[str, Any]:
        return self.get_json("/v1/workers")

    def leases(self) -> dict[str, Any]:
        return self.get_json("/v1/leases")

    def watch_sse(self, *, max_events: int = 0, on_event=None) -> int:
        """Listen to GET /v1/events/stream until max_events (0 = forever) or Ctrl+C."""
        from urllib.parse import urlparse

        u = urlparse(self.base)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        sock = socket.create_connection((host, port), timeout=self.timeout)
        path = "/v1/events/stream"
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Accept: text/event-stream\r\n"
            f"Connection: keep-alive\r\n\r\n"
        ).encode("utf-8")
        sock.sendall(req)
        sock.settimeout(30.0)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        _, _, body = buf.partition(b"\r\n\r\n")
        leftover = body
        count = 0
        try:
            while True:
                while b"\n" in leftover:
                    line, _, leftover = leftover.partition(b"\n")
                    line_s = line.decode("utf-8", errors="replace").strip()
                    if line_s.startswith("data:"):
                        data = line_s[5:].strip()
                        if on_event:
                            on_event(data)
                        else:
                            print(data, flush=True)
                        count += 1
                        if max_events and count >= max_events:
                            return count
                chunk = sock.recv(4096)
                if not chunk:
                    break
                leftover += chunk
        except KeyboardInterrupt:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return count


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nvcode-protocol", description="NVCode Protocol v0 CLI")
    p.add_argument("--base", default=DEFAULT_BASE, help="Server base URL")
    p.add_argument("--timeout", type=float, default=10.0)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="GET /v1/health")
    sub.add_parser("status", help="GET /v1/status")
    sub.add_parser("metrics", help="GET /v1/metrics")
    sub.add_parser("workers", help="GET /v1/workers")
    sub.add_parser("leases", help="GET /v1/leases")

    t = sub.add_parser("tasks", help="GET /v1/tasks")
    t.add_argument("--state", default=None)
    t.add_argument("--channel", default=None)
    t.add_argument("--limit", type=int, default=50)

    g = sub.add_parser("task", help="GET /v1/tasks/{id}")
    g.add_argument("task_id")

    s = sub.add_parser("submit", help="POST /v1/tasks")
    s.add_argument("message")
    s.add_argument("--project", default="")
    s.add_argument("--channel", default="desktop")
    s.add_argument("--recipe", default="")

    c = sub.add_parser("cancel", help="POST /v1/tasks/{id}/cancel")
    c.add_argument("task_id")
    c.add_argument("--reason", default="")

    lg = sub.add_parser("logs", help="GET /v1/logs/tail")
    lg.add_argument("--n", type=int, default=100)

    w = sub.add_parser("watch", help="SSE event stream")
    w.add_argument("--max-events", type=int, default=0, help="0 = until Ctrl+C")

    args = p.parse_args(argv)
    client = ProtocolClient(base_url=args.base, timeout=args.timeout)

    try:
        if args.cmd == "health":
            _print_json(client.health())
        elif args.cmd == "status":
            _print_json(client.status())
        elif args.cmd == "metrics":
            _print_json(client.metrics())
        elif args.cmd == "workers":
            _print_json(client.workers())
        elif args.cmd == "leases":
            _print_json(client.leases())
        elif args.cmd == "tasks":
            _print_json(client.list_tasks(state=args.state, channel=args.channel, limit=args.limit))
        elif args.cmd == "task":
            _print_json(client.get_task(args.task_id))
        elif args.cmd == "cancel":
            code, body = client.cancel(args.task_id, reason=args.reason)
            _print_json(body)
            return 0 if code == 200 and body.get("ok") else 1
        elif args.cmd == "submit":
            code, body = client.submit(
                args.message, project=args.project, channel=args.channel, recipe=args.recipe
            )
            _print_json(body)
            return 0 if code == 200 and body.get("ok") else 1
        elif args.cmd == "logs":
            body = client.logs_tail(n=args.n)
            for line in body.get("lines") or []:
                print(line)
            return 0
        elif args.cmd == "watch":
            client.watch_sse(max_events=args.max_events)
        else:
            p.error(f"unknown command {args.cmd}")
            return 2
    except urllib.error.URLError as e:
        print(f"connection error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
