# -*- coding: utf-8 -*-
"""EventBus → fan-out queue for protocol clients (WS/SSE)."""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from protocol.envelope import make_envelope, map_agent_event


class EventBridge:
    """Thread-safe fan-out. Subscribe once to eventbus.BUS."""

    def __init__(self, max_queue: int = 256) -> None:
        self._max = max_queue
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._subscribed = False
        self._unsub: Callable[[], None] | None = None

    def attach_bus(self) -> None:
        if self._subscribed:
            return
        try:
            from eventbus import BUS
        except Exception:
            return

        def _on(ev: Any) -> None:
            try:
                env = map_agent_event(ev)
                if env is None:
                    return
                self.broadcast(env)
            except Exception:
                pass

        try:
            BUS.subscribe(_on)
            self._subscribed = True
        except Exception:
            try:
                BUS.add_listener(_on)  # type: ignore[attr-defined]
                self._subscribed = True
            except Exception:
                pass

    def register_client(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max)
        with self._lock:
            self._clients.append(q)
        return q

    def unregister_client(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def broadcast(self, envelope: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(envelope)
            except queue.Full:
                try:
                    q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(envelope)
                except Exception:
                    pass

    def emit_metric_tick(self, counters: dict[str, Any]) -> None:
        self.broadcast(make_envelope("metric.tick", {"counters": counters}))


BRIDGE = EventBridge()
