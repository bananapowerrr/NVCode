"""REST synchronization, WebSocket transport and reconnect policy for Protocol v0."""
from __future__ import annotations
from dataclasses import dataclass
import base64, hashlib, json, os, random, socket, threading, time
from collections.abc import Callable, Mapping
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from typing import Any
from .store import StateStore

@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 1.0
    cap_seconds: float = 30.0
    stable_reset_seconds: float = 30.0
    def delay(self, attempt: int, random_value: float | None = None) -> float:
        ceiling = min(max(0, self.cap_seconds), max(0, self.base_seconds) * 2 ** max(0, min(attempt, 30)))
        return ceiling * (random.random() if random_value is None else min(1, max(0, random_value)))

class HttpRestAdapter:
    def __init__(self, base_url="http://127.0.0.1:8340", timeout=3.0):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
    def get(self, path: str) -> Mapping[str, Any]:
        with urlopen(Request(self.base_url + "/" + path.lstrip("/"),
                             headers={"Accept": "application/json"}), timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Protocol v0 REST response must be a JSON object")
        return data

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + "/" + path.lstrip("/"), data=payload, method="POST",
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Protocol v0 REST response must be a JSON object")
        return data

class _SocketHandle:
    def __init__(self, sock, on_event, on_close):
        self.sock, self.on_close, self.closed = sock, on_close, False
        self.thread = threading.Thread(target=self._read, args=(on_event,), daemon=True, name="nvcode-protocol-ws")
        self.thread.start()
    def _exact(self, n):
        out = bytearray()
        while len(out) < n:
            part = self.sock.recv(n-len(out))
            if not part: raise ConnectionError("WebSocket peer closed")
            out.extend(part)
        return bytes(out)
    def _read(self, on_event):
        error = None
        try:
            while not self.closed:
                first, second = self._exact(2)
                opcode, length, masked = first & 15, second & 127, bool(second & 128)
                if length == 126: length = int.from_bytes(self._exact(2), "big")
                elif length == 127: length = int.from_bytes(self._exact(8), "big")
                mask = self._exact(4) if masked else b""
                payload = self._exact(length)
                if masked: payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 8: break
                if opcode not in (1, 0): continue
                try: event = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError): continue
                if isinstance(event, Mapping): on_event(event)
        except Exception as exc:
            if not self.closed: error = exc
        finally:
            self.closed = True
            try: self.sock.close()
            except OSError: pass
            self.on_close(error)
    def close(self):
        if self.closed: return
        self.closed = True
        try:
            mask, payload = os.urandom(4), b"\x03\xe8"
            self.sock.sendall(bytes((0x88, 0x82)) + mask + bytes(b ^ mask[i] for i,b in enumerate(payload)))
        except OSError: pass
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: self.sock.close()
        except OSError: pass

class WebSocketConnector:
    """Receive-only stdlib WebSocket connector, restricted to localhost."""
    def __init__(self, url="ws://127.0.0.1:8340/v1/events", timeout=3.0):
        self.url, self.timeout = url, timeout
    def __call__(self, on_event, on_close):
        parsed = urlsplit(self.url)
        if parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Protocol v0 WebSocket must use ws:// localhost")
        port, path = parsed.port or 80, parsed.path or "/"
        if parsed.query: path += "?" + parsed.query
        sock = socket.create_connection((parsed.hostname, port), timeout=self.timeout)
        sock.settimeout(None)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        try:
            sock.sendall(request.encode("ascii")); response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(1)
                if not chunk: raise ConnectionError("WebSocket handshake closed")
                response.extend(chunk)
                if len(response) > 16384: raise ConnectionError("WebSocket headers too large")
            lines = bytes(response).decode("latin-1").split("\r\n")
            headers = {line.split(":",1)[0].strip().lower(): line.split(":",1)[1].strip()
                       for line in lines[1:] if ":" in line}
            expected = base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if " 101 " not in lines[0] or headers.get("sec-websocket-accept") != expected:
                raise ConnectionError("WebSocket upgrade/accept validation failed")
        except Exception:
            sock.close(); raise
        return _SocketHandle(sock, on_event, on_close)

class ProtocolClient:
    STATES = {"STOPPED", "CONNECTING", "SYNCING", "LIVE", "DEGRADED", "RECONNECT_WAIT", "OFFLINE", "STOPPING"}
    def __init__(self, *, rest_get: Callable[[str], Mapping[str, Any]], ws_connect,
                 rest_post: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
                 store: StateStore | None = None, retry_policy: RetryPolicy | None = None,
                 random_fn: Callable[[], float] = random.random, clock: Callable[[], float] = time.monotonic,
                 schedule_fn=None):
        self.rest_get, self.ws_connect, self.rest_post = rest_get, ws_connect, rest_post
        self.store, self.retry_policy = store or StateStore(), retry_policy or RetryPolicy()
        self.random_fn, self.clock = random_fn, clock
        self.schedule_fn = schedule_fn or self._schedule
        self._lock = threading.RLock(); self._ws = self._timer = None
        self._attempt, self._connected_at = 0, None
        self._stopping = self._buffering = False
        self._buffer: list[Mapping[str, Any]] = []
    def submit_task(self, message: str, *, project: str = "", channel: str = "desktop",
                    metadata: Mapping[str, Any] | None = None, recipe: str | None = None) -> Mapping[str, Any]:
        """Submit new work using the public POST /v1/tasks intake endpoint."""
        if self.rest_post is None:
            raise RuntimeError("No REST POST adapter configured; pass HttpRestAdapter.post as rest_post")
        text = str(message).strip()
        if not text:
            raise ValueError("message must not be empty")
        body: dict[str, Any] = {"message": text, "project": project, "channel": channel}
        if metadata: body["metadata"] = dict(metadata)
        if recipe: body["recipe"] = recipe
        result = self.rest_post("/v1/tasks", body)
        if result.get("ok") is False:
            error = result.get("error", {})
            raise RuntimeError(str(error.get("message") if isinstance(error, Mapping) else error or "task submission rejected"))
        return result

    @staticmethod
    def _schedule(delay, callback):
        timer = threading.Timer(delay, callback); timer.daemon = True; timer.start(); return timer
    @property
    def state(self): return self.store.state.connection
    def start(self) -> bool:
        with self._lock:
            self._stopping = False; self._buffering = True; self._buffer = []
        self.store.set_connection("CONNECTING")
        try:
            ws = self.ws_connect(self.on_event, self.on_ws_closed)
            with self._lock: self._ws, self._connected_at = ws, self.clock()
            ws_ok = True
        except Exception as exc:
            self.store.state.last_error = str(exc); ws_ok = False
        self.store.set_connection("SYNCING")
        rest_ok = self.reconcile()
        with self._lock: buffered, self._buffer, self._buffering = self._buffer, [], False
        for event in buffered: self.store.apply_event(event)
        self.store.set_connection("LIVE" if ws_ok and rest_ok else "DEGRADED" if ws_ok or rest_ok else "OFFLINE")
        return ws_ok and rest_ok
    def reconcile(self) -> bool:
        ticket, data = self.store.begin_sync(), {}
        try:
            data["status"] = self.rest_get("/v1/status")
            data["tasks"] = self.rest_get("/v1/tasks")
            for key, path in (("workers","/v1/workers"),("leases","/v1/leases"),
                              ("logs","/v1/logs/tail?n=200"),("metrics","/v1/metrics")):
                try: data[key] = self.rest_get(path)
                except Exception: pass
            ok = self.store.apply_snapshot(ticket, **data)
            self.store.state.last_error = None if ok else "stale REST snapshot ignored"
            return ok
        except Exception as exc:
            self.store.state.last_error = str(exc); return False
    def on_event(self, event):
        if not isinstance(event, Mapping): return
        with self._lock:
            if self._buffering: self._buffer.append(dict(event)); return
        self.store.apply_event(event)
    def on_ws_closed(self, error=None):
        with self._lock:
            self._ws = None
            if self._stopping:
                self.store.set_connection("STOPPED"); return None
            attempt = self._attempt; self._attempt += 1
        self.store.set_connection("RECONNECT_WAIT", str(error) if error else None)
        rest_ok = self.reconcile()
        delay = self.retry_policy.delay(attempt, self.random_fn())
        self.store.set_connection("DEGRADED" if rest_ok else "OFFLINE", str(error) if error else None)
        with self._lock:
            if not self._stopping: self._timer = self.schedule_fn(delay, self.reconnect)
        return delay
    def reconnect(self):
        with self._lock:
            if self._stopping: return False
            if self._connected_at is not None and self.clock()-self._connected_at >= self.retry_policy.stable_reset_seconds:
                self._attempt = 0
            self._buffering = True; self._buffer = []
        self.store.set_connection("CONNECTING")
        try: ws = self.ws_connect(self.on_event, self.on_ws_closed)
        except Exception as exc:
            with self._lock: self._buffering = False; self._buffer = []
            self.on_ws_closed(exc); return False
        with self._lock: self._ws, self._connected_at = ws, self.clock()
        self.store.set_connection("SYNCING"); rest_ok = self.reconcile()
        with self._lock: buffered, self._buffer, self._buffering = self._buffer, [], False
        for event in buffered: self.store.apply_event(event)
        self.store.set_connection("LIVE" if rest_ok else "DEGRADED")
        return rest_ok
    def stop(self):
        with self._lock:
            self._stopping = True; self.store.set_connection("STOPPING")
            ws, timer, self._ws, self._timer = self._ws, self._timer, None, None
        if timer is not None and hasattr(timer, "cancel"): timer.cancel()
        if ws is not None:
            try: ws.close()
            except Exception: pass
        self.store.set_connection("STOPPED")
