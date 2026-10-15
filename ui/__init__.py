# -*- coding: utf-8 -*-
"""NVCode desktop UI (customtkinter)."""
__version__ = "0.1.0"


def _patch_ctk_bind_all() -> None:
    """customtkinter >= 5 blocks bind_all() on every CTk widget.

    tkinter's bind_all is app-level (bindtag "all"), not widget-level, so
    bind_class("all", ...) is the exact equivalent and stays allowed.
    """
    try:
        import customtkinter as ctk
    except Exception:  # pragma: no cover - ctk missing
        return
    base = getattr(ctk, "CTkBaseClass", None)
    if base is None or getattr(base, "_agentbus_bind_all", False):
        return

    def bind_all(self, sequence=None, func=None, add=None):
        return self.bind_class("all", sequence, func, add)

    base.bind_all = bind_all  # type: ignore[method-assign]
    base._agentbus_bind_all = True  # type: ignore[attr-defined]


_patch_ctk_bind_all()
