# -*- coding: utf-8 -*-
"""Release manifest contract (UPDATE-001A).

Published on GitHub Releases as `release.json` (or similar).
NVCode UpdateChecker only *reads* this — install is external updater.
"""
from __future__ import annotations

import json
from typing import Any

REQUIRED_KEYS = (
    "app",
    "version",
    "channel",
    "published_at",
    "package",
)

PACKAGE_KEYS = ("url", "sha256", "size_bytes")


def validate_manifest(data: dict[str, Any] | None) -> tuple[bool, str]:
    """Return (ok, error). Fail-closed for missing critical fields."""
    if not isinstance(data, dict):
        return False, "manifest_not_object"
    for k in REQUIRED_KEYS:
        if k not in data or data[k] in (None, ""):
            return False, f"missing:{k}"
    pkg = data.get("package")
    if not isinstance(pkg, dict):
        return False, "package_not_object"
    for k in ("url", "sha256"):
        if not str(pkg.get(k) or "").strip():
            return False, f"package_missing:{k}"
    sha = str(pkg.get("sha256") or "").strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return False, "sha256_invalid"
    return True, ""


def parse_manifest(raw: str | bytes | dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Parse JSON or dict → validated manifest or (None, error)."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            data = json.loads(text)
        except Exception as exc:
            return None, f"json:{type(exc).__name__}"
    ok, err = validate_manifest(data if isinstance(data, dict) else None)
    if not ok:
        return None, err
    assert isinstance(data, dict)
    return data, ""


def example_manifest(
    *,
    version: str = "0.10.1",
    channel: str = "stable",
    url: str = "https://github.com/bananapowerrr/NVCode/releases/download/v0.10.1/NVCode-0.10.1-src.zip",
    sha256: str = "0" * 64,
) -> dict[str, Any]:
    """Template for Release pipeline (not used at runtime for install)."""
    return {
        "app": "NVCode",
        "version": version,
        "channel": channel,
        "published_at": "2026-09-21T00:00:00Z",
        "notes": "Bugfixes and recovery improvements.",
        "min_updater": "1.0.0",
        "package": {
            "url": url,
            "sha256": sha256,
            "size_bytes": 0,
            "format": "zip",
        },
        "user_data_paths": [
            "%APPDATA%/AgentBus",  # legacy data path; preserve existing installations
            "%APPDATA%/NVCode",
        ],
        "install_paths_hint": [
            "binaries",
            "runtime",
            "ui",
            "skills",
        ],
    }
