# -*- coding: utf-8 -*-
"""UpdateChecker (UPDATE-001A/B).

Compares local version to remote release manifest.
Does NOT download the application package or replace the running process.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.release_manifest import parse_manifest
from app.version import get_version, is_newer


DEFAULT_MANIFEST_URL = (
    "https://github.com/bananapowerrr/NVCode/releases/latest/download/release.json"
)

# Fail-closed timeouts
DEFAULT_TIMEOUT_SEC = 8.0
MAX_MANIFEST_BYTES = 256_000


def compare_to_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Pure compare. No network."""
    current = get_version()
    out: dict[str, Any] = {
        "current": current,
        "latest": None,
        "update_available": False,
        "manifest_ok": False,
        "error": "",
        "notes": "",
        "package_url": "",
        "sha256": "",
        "channel": "",
        "source": "local",
    }
    if not manifest:
        out["error"] = "no_manifest"
        return out
    parsed, err = parse_manifest(manifest)
    if not parsed:
        out["error"] = err or "invalid_manifest"
        return out
    out["manifest_ok"] = True
    latest = str(parsed.get("version") or "")
    out["latest"] = latest
    out["notes"] = str(parsed.get("notes") or "")
    out["channel"] = str(parsed.get("channel") or "")
    pkg = parsed.get("package") if isinstance(parsed.get("package"), dict) else {}
    out["package_url"] = str(pkg.get("url") or "")
    out["sha256"] = str(pkg.get("sha256") or "")
    out["update_available"] = is_newer(latest, current)
    return out


def check_update_from_text(manifest_json: str) -> dict[str, Any]:
    """Parse JSON text and compare (offline / test path)."""
    data, err = parse_manifest(manifest_json)
    if not data:
        return {
            "current": get_version(),
            "latest": None,
            "update_available": False,
            "manifest_ok": False,
            "error": err,
            "source": "text",
        }
    out = compare_to_manifest(data)
    out["source"] = "text"
    return out


def fetch_manifest(
    url: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> tuple[dict[str, Any] | None, str]:
    """HTTP GET release manifest. Returns (data, error). Never raises."""
    target = (url or os.getenv("NVCODE_RELEASE_MANIFEST_URL") or os.getenv("AGENTBUS_RELEASE_MANIFEST_URL") or DEFAULT_MANIFEST_URL).strip()
    if not target.startswith(("https://", "http://")):
        return None, "url_scheme_invalid"
    try:
        req = Request(
            target,
            headers={
                "User-Agent": "NVCode-UpdateChecker/0.10",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            return None, "manifest_too_large"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return None, f"json:{type(exc).__name__}"
        if not isinstance(data, dict):
            return None, "manifest_not_object"
        parsed, err = parse_manifest(data)
        if not parsed:
            return None, err or "invalid_manifest"
        return parsed, ""
    except HTTPError as exc:
        return None, f"http:{exc.code}"
    except URLError as exc:
        return None, f"network:{type(exc.reason).__name__ if exc.reason else 'URLError'}"
    except TimeoutError:
        return None, "timeout"
    except Exception as exc:
        return None, f"{type(exc).__name__}:{exc}"[:200]


def check_for_updates(
    *,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Fetch remote manifest and compare to local version.

    offline=True or AGENTBUS_OFFLINE → skip network, return error offline.
    Does not download packages or launch updater.
    """
    force_offline = offline
    if force_offline is None:
        env = (os.getenv("AGENTBUS_OFFLINE") or "").strip().lower()
        force_offline = env in ("1", "true", "yes", "on")

    if force_offline:
        return {
            "current": get_version(),
            "latest": None,
            "update_available": False,
            "manifest_ok": False,
            "error": "offline",
            "source": "offline",
            "notes": "",
            "package_url": "",
            "sha256": "",
        }

    data, err = fetch_manifest(url, timeout=timeout)
    if not data:
        return {
            "current": get_version(),
            "latest": None,
            "update_available": False,
            "manifest_ok": False,
            "error": err or "fetch_failed",
            "source": "network",
            "notes": "",
            "package_url": "",
            "sha256": "",
        }
    out = compare_to_manifest(data)
    out["source"] = "network"
    out["error"] = ""
    return out


def format_update_notice(check: dict[str, Any] | None) -> str:
    """Short Chat/UI string; empty if no update or error-only."""
    c = dict(check or {})
    if c.get("error") and not c.get("manifest_ok"):
        return ""
    if not c.get("update_available"):
        return ""
    latest = c.get("latest") or "?"
    notes = str(c.get("notes") or "").strip()
    lines = [f"Доступна новая версия NVCode {latest}"]
    if notes:
        lines.append(notes[:300])
    lines.append("[Обновить] [Позже]  — установка через внешний updater (UPDATE-001D)")
    return "\n".join(lines)
