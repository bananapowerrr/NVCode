#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Developer daemon: Protocol v0 server for UI work.

  PYTHONPATH=.:src python scripts/run_dev_server.py
  PYTHONPATH=.:src python scripts/run_dev_server.py --port 8340 --root /path/to/project --debug

Graceful shutdown on Ctrl+C. Does not touch core reclaim/executor/Tkinter.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _log(level: str, msg: str) -> None:
    colors = {"INFO": "36", "OK": "32", "WARN": "33", "ERR": "31", "DBG": "35"}
    tag = _c(colors.get(level, "0"), f"[{level}]")
    print(f"{tag} {msg}", flush=True)


def _emit_debug_events(stop: threading.Event, interval: float = 3.0) -> None:
    """Push synthetic AgentEvents so WS/SSE clients see traffic."""
    try:
        from eventbus import BUS, AgentEvent
    except Exception as e:
        _log("WARN", f"EventBus unavailable for debug inject: {e}")
        return
    n = 0
    while not stop.wait(interval):
        n += 1
        try:
            BUS.emit(
                AgentEvent(
                    type="HEARTBEAT" if n % 3 else "MESSAGE",
                    task_id=f"dev-{n}",
                    worker="dev-daemon",
                    message=f"debug pulse #{n}",
                    payload={"source": "run_dev_server", "n": n},
                )
            )
            _log("DBG", f"injected event #{n}")
        except Exception as e:
            _log("WARN", f"emit failed: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NVCode Protocol v0 developer daemon")
    p.add_argument("--host", default="127.0.0.1", help="Bind host (loopback only)")
    p.add_argument("--port", type=int, default=8340, help="Bind port (default 8340)")
    p.add_argument(
        "--root",
        default="",
        help="Project / bus root (sets AGENTBUS_ROOT and NVCODE_ROOT)",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Inject synthetic EventBus events for SSE/WS testing",
    )
    p.add_argument(
        "--debug-interval",
        type=float,
        default=3.0,
        help="Seconds between debug events (default 3)",
    )
    args = p.parse_args(argv)

    if args.root:
        root = str(Path(args.root).resolve())
        os.environ["AGENTBUS_ROOT"] = root
        os.environ["NVCODE_ROOT"] = root
        _log("INFO", f"root={root}")

    from protocol.server import ProtocolServer, ProtocolHandler

    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        _log("WARN", f"non-loopback host {host!r} forced to 127.0.0.1")
        host = "127.0.0.1"

    stop_dbg = threading.Event()
    if args.debug:
        threading.Thread(
            target=_emit_debug_events,
            args=(stop_dbg, max(0.5, args.debug_interval)),
            daemon=True,
            name="dev-debug-events",
        ).start()
        _log("INFO", "debug event injector started")

    httpd = ProtocolServer((host, args.port), ProtocolHandler)
    _log("OK", f"protocol v0 listening on http://{host}:{args.port}/v1/health")
    _log("INFO", "Ctrl+C to stop")

    def _shutdown(*_a) -> None:
        _log("INFO", "shutting down…")
        stop_dbg.set()
        try:
            httpd.shutdown()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _shutdown()
    finally:
        stop_dbg.set()
        try:
            httpd.server_close()
        except Exception:
            pass
        _log("OK", "stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
