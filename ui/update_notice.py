# -*- coding: utf-8 -*-
"""UPDATE-001C: Chat/Settings update notice helpers.

Does not download packages or run updater — only surfaces check_for_updates.
"""
from __future__ import annotations

from typing import Any, Callable


def run_update_check(*, offline: bool | None = None, url: str | None = None) -> dict[str, Any]:
    try:
        from app.update_checker import check_for_updates, format_update_notice

        result = check_for_updates(offline=offline, url=url)
        result["notice"] = format_update_notice(result)
        return result
    except Exception as exc:
        return {
            "current": "?",
            "update_available": False,
            "manifest_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "notice": "",
            "source": "error",
        }


def status_line(check: dict[str, Any] | None) -> str:
    """One-line status for Settings label."""
    c = dict(check or {})
    cur = c.get("current") or "?"
    if c.get("error") == "offline":
        return f"NVCode {cur} · offline (проверка отключена)"
    if c.get("error") and not c.get("manifest_ok"):
        return f"NVCode {cur} · проверка: {c.get('error')}"
    if c.get("update_available"):
        return f"NVCode {cur} → доступна {c.get('latest')}"
    if c.get("manifest_ok"):
        return f"NVCode {cur} · актуально"
    return f"NVCode {cur}"


def should_show_chat_banner(check: dict[str, Any] | None) -> bool:
    c = dict(check or {})
    return bool(c.get("update_available") and c.get("notice"))
