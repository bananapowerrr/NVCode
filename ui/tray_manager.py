# -*- coding: utf-8 -*-
"""System tray (optional). Requires pystray + pillow."""
from __future__ import annotations

from typing import Callable


def start_tray(*, on_show: Callable[[], None], on_quit: Callable[[], None]) -> bool:
    """Return False if tray deps missing."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    def _icon_image():
        img = Image.new("RGB", (64, 64), color=(20, 40, 80))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(60, 140, 255))
        d.rectangle((22, 22, 42, 42), fill=(240, 248, 255))
        return img

    state = {"icon": None}

    def show(icon=None, item=None):
        on_show()

    def quit_app(icon=None, item=None):
        try:
            if state["icon"] is not None:
                state["icon"].stop()
        except Exception:
            pass
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("Показать NVCode", show, default=True),
        pystray.MenuItem("Выход", quit_app),
    )
    icon = pystray.Icon("NVCode", _icon_image(), "NVCode", menu)
    state["icon"] = icon
    try:
        icon.run_detached()
    except Exception:
        return False
    return True
