# -*- coding: utf-8 -*-
"""Smoke: run_dev_server starts and answers ProtocolClient."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_run_dev_server_health_and_stop():
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_dev_server.py"),
            "--port",
            "18341",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        from protocol.client import ProtocolClient

        client = ProtocolClient("http://127.0.0.1:18341", timeout=3)
        ok = False
        for _ in range(40):
            try:
                if client.health().get("ok"):
                    ok = True
                    break
            except Exception:
                time.sleep(0.05)
        assert ok
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
