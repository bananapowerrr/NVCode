# -*- coding: utf-8 -*-
"""Admin panel: feature flags on/off, reload, save — separate from operator UI."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pip install customtkinter") from exc

from ui.paths import agentbus_root, ensure_sys_path


# Core modules that must stay on (display-only warning)
PROTECTED = frozenset()  # flags only — bus/runtime are not flags


class AdminWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("NVCode Admin — modules & flags")
        self.geometry("720x640")
        ensure_sys_path()

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(
            top,
            text="Feature flags (optional modules). Core bus/runtime always runs.",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(top, text="Reload", width=90, command=self.reload).pack(side="right", padx=4)
        ctk.CTkButton(top, text="Save YAML", width=100, command=self.save).pack(side="right", padx=4)

        preset_row = ctk.CTkFrame(self, fg_color="transparent")
        preset_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(preset_row, text="Пресеты:").pack(side="left", padx=4)
        for pname in ("minimal", "balanced", "night_autonomous"):
            ctk.CTkButton(
                preset_row, text=pname, width=120,
                command=lambda n=pname: self.apply_preset(n),
            ).pack(side="left", padx=3)

        self.path_lbl = ctk.CTkLabel(self, text="", text_color="gray", anchor="w")
        self.path_lbl.pack(fill="x", padx=12)

        self.status = ctk.CTkLabel(self, text="", anchor="w")
        self.status.pack(fill="x", padx=12, pady=2)

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=12, pady=8)

        self._vars: dict[str, ctk.BooleanVar] = {}
        self.reload()

        tip = ctk.CTkLabel(
            self,
            text=(
                "Выключение → graceful skip в runtime (если модуль обёрнут в is_enabled). "
                "Сохранение пишет config/feature_flags.yaml; диспетчер подхватит после reload_flags "
                "или рестарта."
            ),
            text_color="gray",
            wraplength=680,
            justify="left",
        )
        tip.pack(fill="x", padx=12, pady=8)

    def reload(self) -> None:
        ensure_sys_path()
        from core.feature_flags import list_features, config_path, reload_flags

        reload_flags()
        for w in self.scroll.winfo_children():
            w.destroy()
        self._vars.clear()
        cfg = config_path()
        self.path_lbl.configure(text=f"config: {cfg}" if cfg else "config: (default path on save)")

        for item in list_features():
            name = item["name"]
            row = ctk.CTkFrame(self.scroll)
            row.pack(fill="x", pady=2)
            var = ctk.BooleanVar(value=bool(item["enabled"]))
            self._vars[name] = var
            sw = ctk.CTkSwitch(row, text=name, variable=var, width=280)
            sw.pack(side="left", padx=6, pady=4)
            mark = "known" if item.get("known") else "extra"
            ctk.CTkLabel(row, text=mark, text_color="gray", width=60).pack(side="right", padx=8)
        self.status.configure(text=f"Loaded {len(self._vars)} flags")


    def apply_preset(self, name: str) -> None:
        ensure_sys_path()
        from core.feature_flags import apply_preset
        try:
            flags = apply_preset(name, save=True)
            self.status.configure(
                text=f"Preset {name!r} applied · on={sum(1 for v in flags.values() if v)}"
            )
            self.reload()
        except Exception as exc:
            self.status.configure(text=f"Preset error: {exc}")

    def save(self) -> None:
        ensure_sys_path()
        from core.feature_flags import set_flag, save_flags, get_flags

        for name, var in self._vars.items():
            set_flag(name, bool(var.get()))
        try:
            path = save_flags()
            self.status.configure(text=f"Saved → {path} · enabled={sum(1 for v in get_flags().values() if v)}")
        except Exception as exc:
            self.status.configure(text=f"Save error: {exc}")


def main() -> None:
    root = agentbus_root()
    src = root / "src"
    for p in (str(src), str(root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = AdminWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
