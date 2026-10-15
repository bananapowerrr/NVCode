# -*- coding: utf-8 -*-
"""NVCode Protocol v0 — UI-independent REST + event stream.

Does not import ui.*, does not modify core reclaim/executor/FSM.
"""
from __future__ import annotations

__version__ = "0.1.0"
PROTOCOL_VERSION = 1

from protocol.server import run_protocol_server

__all__ = ["__version__", "PROTOCOL_VERSION", "run_protocol_server"]
