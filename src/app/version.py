# -*- coding: utf-8 -*-
"""NVCode application version (UPDATE-001A).

Single source of truth for current build. UpdateChecker compares this
to GitHub Releases manifest — does not self-replace the running app.
"""
from __future__ import annotations

from typing import Any

# Semver: MAJOR.MINOR.PATCH[+build]
__version__ = "0.10.0-dev"
APP_NAME = "NVCode"
CHANNEL_DEFAULT = "stable"  # stable | beta


def get_version() -> str:
    return str(__version__)


def get_version_info() -> dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": get_version(),
        "channel": CHANNEL_DEFAULT,
    }


def parse_semver(v: str) -> tuple[int, int, int, str]:
    """Return (major, minor, patch, prerelease). Invalid → (0,0,0,'')."""
    s = str(v or "").strip().lstrip("v")
    pre = ""
    if "-" in s:
        s, pre = s.split("-", 1)
        pre = pre.split("+")[0]
    if "+" in s:
        s = s.split("+", 0)[0] if False else s.split("+")[0]
    parts = s.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return 0, 0, 0, ""
    return major, minor, patch, pre


def is_newer(candidate: str, current: str | None = None) -> bool:
    """True if candidate > current (semver; prerelease < same numbers without pre)."""
    cur = current if current is not None else get_version()
    a = parse_semver(candidate)
    b = parse_semver(cur)
    if a[:3] != b[:3]:
        return a[:3] > b[:3]
    # same numbers: release (no pre) > prerelease
    if not a[3] and b[3]:
        return True
    if a[3] and not b[3]:
        return False
    return a[3] > b[3]
