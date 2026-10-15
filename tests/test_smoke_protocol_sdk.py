# -*- coding: utf-8 -*-
"""Pytest entry for scripts/smoke_protocol_sdk.py."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_smoke_protocol_sdk_script():
    script = ROOT / "scripts" / "smoke_protocol_sdk.py"
    assert script.is_file()
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        pytest.fail(f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    assert "SMOKE PASS" in r.stdout
