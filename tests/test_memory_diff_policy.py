# -*- coding: utf-8 -*-
from __future__ import annotations

from safety.diff_policy import assess_risk, should_queue_for_ui


def test_assess_low_risk():
    d = assess_risk(files=["a.py"], complexity=1, diff_lines=5, message="format code")
    assert d.risk == "LOW"
    assert d.action == "auto"


def test_assess_high_sensitive():
    d = assess_risk(files=[".env", "src/auth/login.py"], complexity=4, diff_lines=200, message="delete users")
    assert d.risk in ("HIGH", "MEDIUM")
    assert d.action in ("require_approval", "queue")


def test_should_queue_high():
    d = assess_risk(files=["secrets/key.pem"], complexity=5, diff_lines=500)
    assert should_queue_for_ui(d) is True
