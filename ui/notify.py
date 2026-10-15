# -*- coding: utf-8 -*-
"""Desktop notifications (plyer → Windows toast; fallback no-op)."""
from __future__ import annotations

import threading
from typing import Optional

_last: dict[str, float] = {}
_lock = threading.Lock()


def notify(title: str, message: str, *, timeout: int = 5, dedupe_key: Optional[str] = None) -> bool:
    """Show toast. Returns True if backend accepted the call."""
    import time
    key = dedupe_key or f"{title}:{message[:80]}"
    now = time.time()
    with _lock:
        if key in _last and (now - _last[key]) < 3.0:
            return False
        _last[key] = now
    try:
        from plyer import notification
        notification.notify(
            title=title or "NVCode",
            message=(message or "")[:200],
            app_name="NVCode",
            timeout=timeout,
        )
        return True
    except Exception:
        return False
