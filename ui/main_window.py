# -*- coding: utf-8 -*-
"""Главное окно NVCode."""
from __future__ import annotations

import customtkinter as ctk
import yaml

from ui.chat_panel import ChatPanel
from ui.dispatcher_ctl import is_running as disp_is_running, start as disp_start, stop as disp_stop
from ui.history_panel import HistoryPanel
from ui.logs_panel import LogsPanel
from ui.metrics_panel import MetricsPanel
from ui.notify import notify
from ui.paths import agentbus_root
from ui.projects_panel import ProjectsPanel
from ui.settings_panel import SettingsPanel
from ui.command_palette import CommandPalette
from ui.commands import build_commands
from ui.diff_panel import DiffPanel
from ui.plan_panel import PlanPanel
from ui.problems_panel import ProblemsPanel
from ui.activity_bar import ActivityBar
from ui.terminal_panel import TerminalPanel
from ui.search_panel import SearchPanel
from ui.changes_panel import ChangesPanel
from ui.task_detail_panel import TaskDetailPanel
from ui.pev_panel import PevPanel
from ui.workers_panel import WorkersPanel
from ui.extensions_panel import ExtensionsPanel
from ui.phone_bus_panel import PhoneBusPanel
from ui.skills_panel import SkillsPanel
from ui.recipes_panel import RecipesPanel
from ui.sentinel_panel import SentinelPanel
from ui.project_center_panel import ProjectCenterPanel
from ui.editor_panel import EditorPanel
from ui.explorer_panel import ExplorerPanel

from ui.i18n_ui import t as _t
# Токены темы на верхнем уровне: ниже они используются в __init__ и других
# методах. Локальный импорт внутри одного метода (см. ниже) не покрывает
# остальные — из-за этого был NameError на строке footer_cost.
from ui.theme import (
    ACCENT,
    BG,
    BG_PANEL,
    BG_SIDEBAR,
    BORDER,
    DANGER,
    INFO,
    ON_STATUS,
    SUCCESS,
    TEXT,
    TEXT_DIM,
    WARN,
    apply_appearance,
    apply_frame,
    configure_textbox,
    set_mode,
)


def _ui_defaults_path():
    """Отслеживаемый git шаблон с настройками по умолчанию (READ-ONLY)."""
    from app.ui_config import ui_defaults_path
    return ui_defaults_path()


def _ui_local_path():
    """Локальные переопределения UI — вне git (.agentbus/ в .gitignore)."""
    from app.ui_config import ui_local_path
    return ui_local_path()


def _read_yaml_map(path) -> dict:
    from app.ui_config import read_yaml_map
    return read_yaml_map(path)


def _load_ui_cfg() -> dict:
    """Дефолты из репозитория + локальные переопределения пользователя."""
    from app.ui_config import load_ui_cfg
    return load_ui_cfg()


def _save_ui_cfg(updates: dict) -> None:
    """Пишем ТОЛЬКО в локальный файл.

    Раньше писали прямо в отслеживаемый config/ui.yaml, из-за чего:
      * yaml.safe_dump выжигал все комментарии шаблона;
      * в общий конфиг попадали машинные пути (default_project) и личные настройки;
      * файл постоянно отличался от git и уезжал в Drive.
    Теперь config/ui.yaml — только шаблон-дефолт, его не трогаем.
    Реализация вынесена в app.ui_config, чтобы был один писатель на репозиторий.
    """
    from app.ui_config import update_ui_cfg
    update_ui_cfg(updates)


class MainWindow(ctk.CTk):

    def _recipe_project(self) -> str:
        try:
            if hasattr(self, "projects") and hasattr(self.projects, "selected_project"):
                return str(self.projects.selected_project() or "")
        except Exception:
            pass
        return ""

    def __init__(self):
        apply_appearance()
        super().__init__()
        self.title("NVCode")
        self.geometry("1440x900")
        cfg = _load_ui_cfg()
        mode = str(cfg.get("theme", "dark") or "dark")
        if mode not in ("dark", "light", "system"):
            mode = "dark"
        # 'system' CustomTkinter не понимает — резолвим через theme engine.
        # Раньше здесь стояло ctk.set_appearance_mode(mode), и при mode=system
        # (а он есть в списке значений OptionMenu) тема не применялась.
        self._theme_resolved = set_mode(mode)
        self._theme = mode
        self._toast = bool(cfg.get("toast_notifications", True))

        self.grid_columnconfigure(0, weight=0)  # activity bar
        self.grid_columnconfigure(1, weight=0)  # sidebar
        self.grid_columnconfigure(2, weight=3)  # center
        self.grid_columnconfigure(3, weight=2)  # right panels
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self.configure(fg_color=BG)

        # P4 Activity Bar (column 0)
        try:
            self.activity_bar = ActivityBar(self, on_select=self._on_activity_select)
            self.activity_bar.grid(row=0, column=0, sticky="ns", padx=0, pady=0)
        except Exception:
            self.activity_bar = None

        left = ctk.CTkFrame(self, width=240, corner_radius=0)
        left.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        left.grid_propagate(False)
        self._left_frame = left
        apply_frame(left, role="sidebar")

        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.pack(fill="x", padx=10, pady=(12, 4))
        ctk.CTkLabel(
            brand,
            text="AgentBus",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            brand,
            text=_t("brand_tagline", default="локальный AI для кода"),
            text_color="gray",
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).pack(fill="x")
        led_row = ctk.CTkFrame(brand, fg_color="transparent")
        led_row.pack(fill="x", pady=(6, 0))
        self.disp_led = ctk.CTkLabel(led_row, text="●", text_color="gray", width=16)
        self.disp_led.pack(side="left")
        self.disp_led_text = ctk.CTkLabel(
            led_row,
            text=_t("disp_off", default="диспетчер выключен"),
            text_color="gray",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.disp_led_text.pack(side="left", padx=4)

        center = ctk.CTkFrame(self, corner_radius=0)
        center.grid(row=0, column=2, sticky="nsew", padx=0, pady=0)
        self._center_frame = center
        try:
            apply_frame(center, role="shell")
        except Exception:
            pass

        right = ctk.CTkFrame(self, width=340, corner_radius=0)
        right.grid(row=0, column=3, sticky="nsew", padx=0, pady=0)
        self._right_frame = right
        apply_frame(right, role="panel")


        # Bottom status bar
        footer = ctk.CTkFrame(self, height=28, corner_radius=0)
        footer.grid(row=1, column=0, columnspan=4, sticky="ew")
        apply_frame(footer, role="sidebar")
        self.footer_status = ctk.CTkLabel(
            footer,
            text=_t("footer_idle", default="диспетчер: —  ·  очередь: —"),
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        )
        self.footer_status.pack(side="left", padx=12, pady=2)
        self.footer_hint = ctk.CTkLabel(
            footer,
            text="Ctrl+Enter · Ctrl+K · Ctrl+F/H · F5",
            anchor="e",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.footer_hint.pack(side="right", padx=12, pady=2)
        self.footer_cost = ctk.CTkLabel(
            footer,
            text="local: 0 ₽",
            anchor="e",
            font=ctk.CTkFont(size=11),
            text_color=SUCCESS,
        )
        self.footer_cost.pack(side="right", padx=8, pady=2)


        self.projects = ProjectsPanel(left, on_select=self._on_project)
        self.projects.pack(fill="x", padx=0, pady=0)
        try:
            self.explorer = ExplorerPanel(
                left,
                get_project=lambda: self.projects.selected_project() if callable(getattr(self.projects, "selected_project", None)) else self.projects.selected_project,
                on_open_file=self._open_in_editor,
            )
            self.explorer.pack(fill="both", expand=True, padx=4, pady=4)
        except Exception:
            self.explorer = None

        ctl = ctk.CTkFrame(left, fg_color="transparent")
        ctl.pack(fill="x", padx=8, pady=4)
        # Подписи различаем явно: раньше «■ Stop» (стоп приложения) и
        # «■ Stop» (стоп диспетчера) выглядели одинаково, и кнопку остановки
        # диспетчера невозможно было опознать в панели.
        ctk.CTkButton(ctl, text=_t("dispatcher_start", default="▶ Dispatcher"), command=self._start_dispatcher, height=28).pack(fill="x", pady=2)
        ctk.CTkButton(ctl, text=_t("btn_diagnose", default="Диагностика"), command=self._run_diagnose, height=28, fg_color="gray30").pack(fill="x", pady=2)
        ctk.CTkButton(ctl, text=_t("btn_help", default="Справка"), command=self._show_help, height=28, fg_color="gray30").pack(fill="x", pady=2)
        ctk.CTkButton(ctl, text=_t("app_run", default="▶ Run (приложение)"), command=self._run_project_app, height=28, fg_color="gray30").pack(fill="x", pady=2)
        ctk.CTkButton(ctl, text=_t("app_stop", default="■ Stop (приложение)"), command=self._stop_project_app, height=28, fg_color="gray30").pack(fill="x", pady=2)
        ctk.CTkButton(ctl, text=_t("dispatcher_stop", default="■ Stop (диспетчер)"), command=self._stop_dispatcher, height=28, fg_color="gray40").pack(
            fill="x", pady=2
        )

        theme_row = ctk.CTkFrame(left, fg_color="transparent")
        theme_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(theme_row, text=_t("theme_label", default="Тема:")).pack(side="left")
        self.theme_var = ctk.StringVar(value=mode)
        ctk.CTkOptionMenu(
            theme_row,
            variable=self.theme_var,
            values=["dark", "light", "system"],
            command=self._set_theme,
            width=100,
        ).pack(side="right")

        # FC-45: workspace mode (Code / Agent / Project / Full)
        mode_row = ctk.CTkFrame(left, fg_color="transparent")
        mode_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(mode_row, text="Режим:").pack(side="left")
        self._workspace_mode = ctk.StringVar(value="agent")
        ctk.CTkOptionMenu(
            mode_row,
            variable=self._workspace_mode,
            values=["agent", "code", "project", "full"],
            command=self._set_workspace_mode,
            width=110,
        ).pack(side="right")
        try:
            from app.agent_behavior import behavior_summary, load_agent_behavior
            self._agent_label = ctk.CTkLabel(
                mode_row, text=behavior_summary(load_agent_behavior()), text_color="gray"
            )
            self._agent_label.pack(side="right", padx=8)
            self._agent_label.bind("<Button-1>", lambda e: self._open_agent_popover())
            try:
                self._agent_label.configure(cursor="hand2")
            except Exception:
                pass
        except Exception:
            self._agent_label = None
        nav_row = ctk.CTkFrame(left, fg_color="transparent")
        nav_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(nav_row, text="←", width=36, command=self._nav_back).pack(side="left", padx=2)
        ctk.CTkButton(nav_row, text="→", width=36, command=self._nav_forward).pack(side="left", padx=2)
        self._nav_label = ctk.CTkLabel(nav_row, text="", text_color="gray", anchor="w")
        self._nav_label.pack(side="left", padx=6)

        ctk.CTkButton(left, text=_t("settings", default="⚙ Настройки"), command=self._open_settings).pack(fill="x", padx=8, pady=8)
        self.status = ctk.CTkLabel(left, text="Проект: —", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 4))
        self.lock_lbl = ctk.CTkLabel(left, text="dispatcher: ?", anchor="w", text_color="gray")
        self.lock_lbl.pack(fill="x", padx=10, pady=(0, 8))

        # FC-39: Editor (top) + Chat (bottom)
        try:
            self.editor = EditorPanel(
                center,
                get_project=lambda: self.projects.selected_project() if callable(getattr(self.projects, "selected_project", None)) else self.projects.selected_project,
                on_active_change=self._on_editor_active,
            )
            self.editor.pack(fill="both", expand=True, padx=2, pady=(2, 0))
        except Exception:
            self.editor = None
        self.chat = ChatPanel(
            center,
            get_project=lambda: self.projects.selected_project,
            get_channel=lambda: self.projects.selected_channel,
            on_command=self._on_chat_command,
            get_editor_context=self._editor_context_for_chat,
                # on_continue set below

            on_sent=self._on_task_sent,
        )
        self.chat.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        try:
            self.terminal_panel = TerminalPanel(center)
            self.terminal_panel.pack(fill="x", padx=2, pady=(0, 2))
        except Exception:
            self.terminal_panel = None

        # --- shortcuts that need the widgets to exist ---------------------
        try:
            self.bind_all("<F8>", lambda e: (self._focus_terminal(), "break")[1])
            self.bind_all("<Alt-1>", lambda e: (self._focus_pane("projects"), "break")[1])
            self.bind_all("<Alt-2>", lambda e: (self._focus_pane("chat"), "break")[1])
        except Exception:
            pass

        tabs = ctk.CTkTabview(right)
        self._right_tabs = tabs
        tabs.pack(fill="both", expand=True, padx=4, pady=4)
        log_tab = tabs.add(_t("tab_logs", default="Логи"))
        met_tab = tabs.add(_t("tab_metrics", default="Метрики"))
        hist_tab = tabs.add(_t("tab_history", default="История"))
        self.logs = LogsPanel(log_tab, on_done=self._on_done, on_error=self._on_error)
        self.logs.pack(fill="both", expand=True)
        self.metrics = MetricsPanel(met_tab)
        self.metrics.pack(fill="both", expand=True)
        self.history = HistoryPanel(hist_tab, on_resend=self._resend_task, on_select=self._on_history_select)
        self.history.pack(fill="both", expand=True)
        try:
            q_tab = tabs.add(_t("tab_queue", default="Очередь"))
            from ui.queue_panel import QueuePanel
            self.queue_panel = QueuePanel(q_tab, on_select=self._on_queue_select)
            self.queue_panel.pack(fill="both", expand=True)
        except Exception:
            self.queue_panel = None
        self.workers_panel = None
        self.skills_panel = None
        self.recipes_panel = None
        self.extensions_panel = None
        self.phone_bus_panel = None
        self.pev_panel = None
        self.sentinel_panel = None
        if self._should_show_advanced_tabs():
            wrk_tab = tabs.add(_t("tab_workers", default="Воркеры"))
            self.workers_panel = WorkersPanel(wrk_tab)
            self.workers_panel.pack(fill="both", expand=True)
            sk_tab = tabs.add(_t("tab_skills", default="Навыки"))
            rec_tab = tabs.add(_t("tab_recipes", default="Рецепты"))
            ext_tab = tabs.add(_t("tab_extensions", default="Расширения"))
            phone_tab = tabs.add(_t("tab_phone", default="Телефон"))
            self.phone_bus_panel = PhoneBusPanel(phone_tab)
            self.phone_bus_panel.pack(fill="both", expand=True)
            self.extensions_panel = ExtensionsPanel(ext_tab)
            self.extensions_panel.pack(fill="both", expand=True)
            self.skills_panel = SkillsPanel(sk_tab)
            self.skills_panel.pack(fill="both", expand=True)
            self.recipes_panel = RecipesPanel(
                rec_tab,
                on_enqueue=lambda d: self.chat.append(
                    "System", f"Рецепт «{d.get('recipe')}» в очереди", kind="info"
                )
                if hasattr(self, "chat")
                else None,
                get_project=self._recipe_project,
            )
            self.recipes_panel.pack(fill="both", expand=True)
            pev_tab = tabs.add(_t("tab_pev", default="PEV"))
            self.pev_panel = PevPanel(pev_tab)
            self.pev_panel.pack(fill="both", expand=True)
            sent_tab = tabs.add(_t("tab_sentinel", default="Санитар"))
            self.sentinel_panel = SentinelPanel(
                sent_tab,
                get_project=self._current_project_root,
            )
            self.sentinel_panel.pack(fill="both", expand=True)
        diff_tab = tabs.add(_t("tab_diff", default="Diff"))
        self._tab_diff_name = _t("tab_diff", default="Diff")
        self.diff_panel = DiffPanel(
            diff_tab,
            get_project_root=lambda: (
                self.projects.selected_project()
                if callable(getattr(self.projects, "selected_project", None))
                else getattr(self.projects, "selected_project", "") or ""
            ),
            on_applied=lambda tid: self._after_diff_action("applied", tid),
            on_rejected=lambda tid: self._after_diff_action("rejected", tid),
        )
        self.diff_panel.pack(fill="both", expand=True)
        try:
            plan_tab = tabs.add(_t("tab_plan", default="Plan"))
            self.plan_panel = PlanPanel(
                plan_tab,
                get_project=lambda: self.projects.selected_project()
                if callable(getattr(self.projects, "selected_project", None))
                else getattr(self.projects, "selected_project", ""),
            )
            self.plan_panel.pack(fill="both", expand=True)
        except Exception:
            self.plan_panel = None
        try:
            prob_tab = tabs.add("Problems")
            self.problems_panel = ProblemsPanel(
                prob_tab,
                get_project=lambda: self.projects.selected_project()
                if callable(getattr(self.projects, "selected_project", None))
                else getattr(self.projects, "selected_project", ""),
                on_open_file=self._open_in_editor if hasattr(self, "_open_in_editor") else None,
                on_open_task=self._open_task_from_problem,
            )
            self.problems_panel.pack(fill="both", expand=True)
        except Exception:
            self.problems_panel = None
        try:
            search_tab = tabs.add("Search")
            self.search_panel = SearchPanel(
                search_tab,
                get_project=lambda: self.projects.selected_project()
                if callable(getattr(self.projects, "selected_project", None))
                else getattr(self.projects, "selected_project", ""),
                on_open_file=self._open_in_editor if hasattr(self, "_open_in_editor") else None,
            )
            self.search_panel.pack(fill="both", expand=True)
        except Exception:
            self.search_panel = None
        try:
            ch_tab = tabs.add("Changes")
            self.changes_panel = ChangesPanel(
                ch_tab,
                get_project=lambda: (
                    self.projects.selected_project()
                    if callable(getattr(self.projects, "selected_project", None))
                    else getattr(self.projects, "selected_project", "") or ""
                ),
                on_review_file=self._review_change,
                on_open_file=self._open_in_editor,
            )
            self.changes_panel.pack(fill="both", expand=True)
        except Exception:
            self.changes_panel = None

        # Project Center (FC-43) — создавался как модуль, но не был инстанцирован:
        # все getattr(self, "project_center") ниже были вечно мёртвой веткой.
        try:
            pc_name = _t("tab_project_center", default="Проект")
            pc_tab = tabs.add(pc_name)
            self._tab_project_center = pc_name
            self.project_center = ProjectCenterPanel(
                pc_tab,
                get_project=lambda: (
                    self.projects.selected_project()
                    if callable(getattr(self.projects, "selected_project", None))
                    else getattr(self.projects, "selected_project", "") or ""
                ),
                on_action=self._on_project_center_action,
            )
            self.project_center.pack(fill="both", expand=True)
        except Exception:
            self.project_center = None

        # Task Detail (FC-42) — та же история: show_task() звали 8 раз, панели не было.
        try:
            td_name = _t("tab_task_detail", default="Задача")
            td_tab = tabs.add(td_name)
            self._tab_task_detail = td_name
            self.task_detail_panel = TaskDetailPanel(
                td_tab,
                get_project=lambda: (
                    self.projects.selected_project()
                    if callable(getattr(self.projects, "selected_project", None))
                    else getattr(self.projects, "selected_project", "") or ""
                ),
                on_open_diff=self._open_task_diff,
                on_open_file=self._open_in_editor if hasattr(self, "_open_in_editor") else None,
            )
            self.task_detail_panel.pack(fill="both", expand=True)
        except Exception:
            self.task_detail_panel = None

        try:
            import os
            if os.getenv("AGENTBUS_FEATURE_PRESET", "").startswith("beginner"):
                # Beginner: focus Chat + Diff + Logs only
                for tab_name in ("PEV", "Sentinel", "Skills", "Workers"):
                    try:
                        tabs.delete(tab_name)
                    except Exception:
                        pass
        except Exception:
            pass

        self._settings_win = None
        self.bind_all("<Control-comma>", lambda e: self._open_settings())
        self.bind_all("<F7>", lambda e: self._refresh_scm())
        try:
            self.bind_all("<F5>", lambda e: self._refresh_all())
        except Exception:
            pass
        try:
            from app.nav_history import NavHistory
            self._nav = NavHistory()
        except Exception:
            self._nav = None
        try:
            self._apply_workspace_mode(self._workspace_mode.get() if hasattr(self, "_workspace_mode") else "agent")
        except Exception:
            pass

        self.bind_all("<F1>", lambda e: self._show_help())
        self.bind_all("<Control-t>", lambda e: self._toggle_theme())
        self.palette = CommandPalette(self, build_commands(self))
        try:
            from ui.setup_wizard import maybe_run_wizard
            self.after(400, lambda: maybe_run_wizard(self))
        except Exception:
            pass
        self.after(600, self._apply_onboarding_cfg)
        self.after(900, self._apply_editor_prefs)
        self.after(800, self._poll_app_loop)
        self._file_watcher = None
        try:
            self.chat.on_continue = self._on_continue
        except Exception:
            pass
        self.bind_all("<Control-k>", lambda e: self.palette.open())
        self.bind_all("<Control-p>", lambda e: self._quick_open())
        self.bind_all("<Control-Shift-O>", lambda e: self._goto_symbol())
        self.bind_all("<Alt-z>", lambda e: self._toggle_wrap())
        self.bind_all("<Control-Shift-F>", lambda e: self._open_search())
        self.bind_all("<Control-Alt-1>", lambda e: self._apply_layout_preset("agent"))
        self.bind_all("<Control-Alt-2>", lambda e: self._apply_layout_preset("code"))
        self.bind_all("<Control-Alt-3>", lambda e: self._apply_layout_preset("focus"))
        self.bind_all("<Control-Alt-4>", lambda e: self._apply_layout_preset("full"))
        self.bind_all("<Control-Alt-s>", lambda e: self._save_current_layout())
        self.bind_all("<Control-Shift-f>", lambda e: self._open_search())
        self.bind_all("<Control-P>", lambda e: self._quick_open())
        self.bind_all("<Control-K>", lambda e: self.palette.open())
        self.bind_all("<Control-1>", lambda e: self.projects.channel_var.set("gpt"))
        self.bind_all("<Control-2>", lambda e: self.projects.channel_var.set("grok"))
        self.bind_all("<Control-3>", lambda e: self.projects.channel_var.set("gemini"))
        self.bind_all("<Control-4>", lambda e: self.projects.channel_var.set("autopilot"))
        self.bind_all("<Control-q>", lambda e: self._request_close())
        try:
            self.protocol("WM_DELETE_WINDOW", self._request_close)
        except Exception:
            pass
        self.after(800, self._poll_dispatcher_lock)
        self.after(15000, self._poll_skill_proposals)

        if cfg.get("auto_start_dispatcher"):
            self.after(500, self._start_dispatcher)
        self.after(800, self._welcome_desktop)
        self.after(1500, self._footer_loop)



    def _term(self, source: str, message: str) -> None:
        try:
            if getattr(self, "terminal_panel", None):
                self.terminal_panel.append(source, str(message)[:500])
        except Exception:
            pass

    def _on_activity_select(self, view_id: str) -> None:
        """Activity Bar → focus primary UI region / tab."""
        try:
            if view_id == "explorer" and getattr(self, "explorer", None):
                self.explorer.refresh()
            elif view_id == "search":
                try:
                    if getattr(self, "_right_tabs", None):
                        self._right_tabs.set("Search")
                    if getattr(self, "search_panel", None):
                        self.search_panel.focus_search()
                except Exception:
                    pass
            elif view_id == "agent" and getattr(self, "chat", None):
                try:
                    self.chat.focus_set()
                except Exception:
                    pass
            elif view_id == "settings":
                self._open_settings()
            elif view_id in ("plan", "problems", "git", "extensions") and getattr(self, "_right_tabs", None):
                tab_map = {
                    "plan": "Plan",
                    "problems": "Problems",
                    "git": "Changes",
                    "extensions": "Extensions",
                }
                name = tab_map.get(view_id, "")
                for cand in (name, view_id.capitalize()):
                    try:
                        self._right_tabs.set(cand)
                        break
                    except Exception:
                        continue
            if getattr(self, "terminal_panel", None):
                self.terminal_panel.append("ui", f"view → {view_id}")
        except Exception:
            pass

    def _request_close(self) -> None:
        """Exit guard: confirm discard of dirty editor tabs."""
        try:
            ed = getattr(self, "editor", None) or getattr(self, "editor_panel", None)
            dirty = []
            if ed is not None and hasattr(ed, "dirty_paths"):
                dirty = list(ed.dirty_paths() or [])
            if dirty:
                from tkinter import messagebox
                msg = "Несохранённые файлы:\n" + "\n".join(dirty[:12])
                if len(dirty) > 12:
                    msg += f"\n…и ещё {len(dirty)-12}"
                msg += "\n\nВыйти без сохранения?"
                if not messagebox.askyesno("Выход", msg, parent=self):
                    return
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _open_search(self) -> None:
        try:
            if getattr(self, "_right_tabs", None):
                self._right_tabs.set("Search")
            if getattr(self, "search_panel", None):
                self.search_panel.focus_search()
            self._term("ui", "search")
        except Exception:
            pass


    def _apply_editor_prefs(self) -> None:
        try:
            ed = getattr(self, "editor", None)
            if ed is None or not hasattr(ed, "set_autosave"):
                return
            cfg = {}
            try:
                import yaml
                from pathlib import Path as P
                p = P(__file__).resolve().parents[1] / "config" / "ui.yaml"
                if p.is_file():
                    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                cfg = {}
            on = bool(cfg.get("autosave", False))
            ms = int(cfg.get("autosave_interval_ms") or 8000)
            ed.set_autosave(on, ms)
            if on:
                self._term("editor", f"autosave on ({ms}ms)")
        except Exception:
            pass

    def _toggle_autosave(self) -> None:
        try:
            ed = getattr(self, "editor", None)
            if ed is None:
                return
            cur = not getattr(ed, "_autosave_on", False)
            ed.set_autosave(cur)
            self.chat.append("System", f"Autosave: {'ON' if cur else 'OFF'}", kind="info")
        except Exception as exp:
            self.chat.append("System", f"autosave: {exp}")

    def _toggle_wrap(self) -> None:
        try:
            ed = getattr(self, "editor", None)
            if ed and hasattr(ed, "toggle_word_wrap"):
                on = ed.toggle_word_wrap()
                self.chat.append("System", f"Word wrap: {'ON' if on else 'OFF'}", kind="info")
        except Exception as exp:
            self.chat.append("System", f"wrap: {exp}")

    def _goto_symbol(self) -> None:

        try:
            ed = getattr(self, "editor", None)
            if ed and hasattr(ed, "goto_symbol"):
                ed.goto_symbol()
        except Exception as exp:
            self.chat.append("System", f"symbol: {exp}")

    def _quick_open(self) -> None:

        """Ctrl+P — filter project files and open in editor."""
        try:
            root = self._current_project_root()
        except Exception:
            root = ""
        if not root:
            try:
                if getattr(self, "palette", None):
                    self.palette.open()
            except Exception:
                pass
            return
        try:
            import customtkinter as ctk
        except Exception:
            return
        win = ctk.CTkToplevel(self)
        win.title("Quick Open")
        win.geometry("480x360")
        win.attributes("-topmost", True)
        entry = ctk.CTkEntry(win, placeholder_text="filter files…")
        entry.pack(fill="x", padx=8, pady=8)
        box = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=12))
        box.pack(fill="both", expand=True, padx=8, pady=4)
        files: list[str] = []
        try:
            from app.files_service import FilesService
            files = FilesService(root).list_files_flat(max_files=1500)
        except Exception as exp:
            box.insert("1.0", f"error: {exp}")
        recent: list[str] = []
        try:
            ed = getattr(self, "editor", None)
            if ed is not None and hasattr(ed, "recent_files"):
                recent = [p for p in ed.recent_files() if p in files or True][:8]
        except Exception:
            recent = []
        # recent first, then rest without dups
        rest = [f for f in files if f not in recent]
        ordered = recent + rest
        shown: list[str] = list(ordered[:80])
        self._qo_recent = set(recent)

        def render(items: list[str]) -> None:
            box.delete("1.0", "end")
            rec = getattr(self, "_qo_recent", set()) or set()
            lines = []
            for it in items:
                lines.append(("⏱ " if it in rec else "   ") + it)
            box.insert("1.0", "\n".join(lines) if lines else "(no matches)")

        render(shown)

        def on_filter(_e=None) -> None:
            q = (entry.get() or "").strip().lower().replace("\\", "/")
            if not q:
                shown[:] = files[:80]
            else:
                shown[:] = [f for f in files if q in f.lower()][:80]
            render(shown)

        def open_sel(_e=None) -> None:
            try:
                line = box.get("insert linestart", "insert lineend").strip()
            except Exception:
                line = shown[0] if shown else ""
            if not line or line.startswith("("):
                return
            if line.startswith("⏱ "):
                line = line[2:].strip()
            else:
                line = line.strip()
            try:
                self._open_in_editor(line)
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

        entry.bind("<KeyRelease>", on_filter)
        entry.bind("<Return>", open_sel)
        box.bind("<Double-Button-1>", open_sel)
        box.bind("<Return>", open_sel)
        try:
            entry.focus_set()
        except Exception:
            pass
        self._term("ui", "quick open")


    def _resend_task(self, row: dict) -> None:
        """FC-09: resend через TaskService → desktop_queue (не channels/incoming)."""
        ensure_sys_path = None
        try:
            from ui.paths import ensure_sys_path as _esp
            ensure_sys_path = _esp
            _esp()
        except Exception:
            pass
        try:
            from core.task_service import resubmit_from_row
        except Exception as exc:
            self.chat.append("System", f"Resend: TaskService недоступен ({exc})")
            return
        # enrich system_prompt if present
        row = dict(row or {})
        if hasattr(self.chat, "_load_system_prompt"):
            try:
                meta = dict(row.get("metadata") or {})
                meta.setdefault("system_prompt", self.chat._load_system_prompt())
                row["metadata"] = meta
            except Exception:
                pass
        if not str(row.get("project") or "").strip():
            row["project"] = str(getattr(self.projects, "selected_project", "") or "")
        tid, err = resubmit_from_row(row, source="ui-resend", root=agentbus_root())
        if err or not tid:
            self.chat.append("System", f"Resend отклонён: {err or 'unknown'}")
            return
        try:
            if hasattr(self.chat, "_track_pending"):
                self.chat._track_pending(tid)
            else:
                self.chat._pending_ids.add(tid)
        except Exception:
            pass
        self.chat.append("System", f"Resend → desktop_queue id={tid}")
        try:
            from ui.dispatcher_ctl import is_running as _disp_run
            if not _disp_run():
                self.chat.append(
                    "System",
                    "Диспетчер не запущен — задача в очереди. Нажмите ▶.",
                    kind="info",
                )
        except Exception:
            pass
        try:
            self.history.refresh()
        except Exception:
            pass

    def _refresh_all(self) -> None:
        """FC-44: refresh all workspace panels (F5)."""
        try:
            self.projects.reload()
        except Exception:
            pass
        for name in (
            "explorer",
            "queue_panel",
            "task_detail_panel",
            "changes_panel",
            "project_center",
            "metrics",
            "history",
            "workers_panel",
            "sentinel_panel",
            "problems_panel",
            "plan_panel",
            "terminal_panel",
            "search_panel",
        ):
            panel = getattr(self, name, None)
            if panel is None:
                continue
            try:
                if hasattr(panel, "refresh"):
                    panel.refresh()
            except Exception:
                pass
        try:
            if getattr(self, "diff_panel", None) and hasattr(self.diff_panel, "refresh_from_store"):
                self.diff_panel.refresh_from_store()
        except Exception:
            pass


    def _apply_layout_preset(self, name: str) -> None:
        """P4: apply saved layout preset (agent/code/focus/full)."""
        try:
            from app.layout_prefs import apply_layout_preset, get_layout
            from ui.paths import agentbus_root
            layout = apply_layout_preset(name, root=agentbus_root())
            mode = str(layout.get("mode") or name)
            if hasattr(self, "_workspace_mode"):
                self._workspace_mode.set(mode)
                self._apply_workspace_mode(mode)
            # column widths
            try:
                left_w = int(layout.get("left_width") or 260)
                right_w = int(layout.get("right_width") or 360)
                # sidebar is grid col 1
                for child in self.winfo_children():
                    pass
            except Exception:
                pass
            show_right = layout.get("show_right", True)
            try:
                # right frame is column 3 — hide by zero weight/forget is hard; use grid_remove if we stored ref
                if getattr(self, "_right_frame", None) is not None:
                    if show_right:
                        self._right_frame.grid()
                    else:
                        self._right_frame.grid_remove()
            except Exception:
                pass
            self._term("ui", f"layout → {name}")
            try:
                self.chat.append("System", f"Layout: {name}", kind="info")
            except Exception:
                pass
        except Exception as exp:
            try:
                self._term("ui", f"layout error: {exp}")
            except Exception:
                pass

    def _save_current_layout(self) -> None:
        try:
            from app.layout_prefs import save_layout
            from ui.paths import agentbus_root
            mode = "agent"
            try:
                mode = self._workspace_mode.get()
            except Exception:
                pass
            save_layout(preset=mode, mode=mode, root=agentbus_root())
            self._term("ui", f"layout saved ({mode})")
        except Exception as exp:
            self._term("ui", f"save layout: {exp}")

    def _should_show_advanced_tabs(self) -> bool:
        """Create the optional right-side tabs only if the mode asks for them.

        Beginner/agent keeps the panel set small; `full` shows everything.
        Panels stay registered-but-optional, so a False just means "not built".
        """
        try:
            mode_id = self._workspace_mode.get() if hasattr(self, "_workspace_mode") else "agent"
        except Exception:
            mode_id = "agent"
        try:
            from app.workspace_mode import panel_visibility
            vis = panel_visibility(mode_id)
        except Exception:
            return True
        keys = (
            "workers", "skills", "recipes", "pev",
            "sentinel", "phone", "extensions",
        )
        return any(bool(vis.get(k)) for k in keys)

    def _set_workspace_mode(self, mode_id: str) -> None:

        try:
            self._apply_workspace_mode(mode_id)
            self._nav_push(kind="mode", label=f"mode:{mode_id}", workspace_mode=mode_id)
        except Exception:
            pass

    def _apply_workspace_mode(self, mode_id: str) -> None:
        """FC-45 progressive disclosure: show/hide center panels + prefer tabs."""
        try:
            from app.workspace_mode import get_mode, should_show_panel
            m = get_mode(mode_id)
        except Exception:
            return
        # Editor / Chat visibility
        try:
            if getattr(self, "editor", None):
                if should_show_panel(mode_id, "editor"):
                    if not self.editor.winfo_ismapped():
                        self.editor.pack(fill="both", expand=True, padx=2, pady=(2, 0))
                else:
                    self.editor.pack_forget()
        except Exception:
            pass
        try:
            if getattr(self, "chat", None):
                if should_show_panel(mode_id, "chat"):
                    if not self.chat.winfo_ismapped():
                        self.chat.pack(fill="both", expand=True, padx=2, pady=(0, 2))
                else:
                    # keep chat accessible in code mode via small strip — still show for agent/project
                    if mode_id == "code":
                        # minimal: keep chat but user focused on editor; still pack
                        pass
        except Exception:
            pass
        try:
            if hasattr(self, "chat"):
                self.chat.append("System", f"Режим: {m.label} — {m.description}", kind="info")
        except Exception:
            pass
        try:
            _save_ui_cfg({"workspace_mode": mode_id})
        except Exception:
            pass

    def _nav_push(self, **kwargs) -> None:
        if not getattr(self, "_nav", None):
            return
        try:
            from app.nav_history import NavContext
            root = ""
            try:
                sp = self.projects.selected_project
                root = sp() if callable(sp) else (sp or "")
            except Exception:
                root = ""
            mode = "agent"
            try:
                mode = self._workspace_mode.get()
            except Exception:
                pass
            ctx = NavContext(
                kind=str(kwargs.get("kind") or "generic"),
                project=str(kwargs.get("project") or root or ""),
                file=str(kwargs.get("file") or ""),
                selection=str(kwargs.get("selection") or "")[:500],
                task_id=str(kwargs.get("task_id") or ""),
                workspace_mode=str(kwargs.get("workspace_mode") or mode),
                label=str(kwargs.get("label") or ""),
            )
            self._nav.push(ctx)
            self._update_nav_label()
        except Exception:
            pass

    def _update_nav_label(self) -> None:
        try:
            if not getattr(self, "_nav", None) or not getattr(self, "_nav_label", None):
                return
            cur = self._nav.current()
            if not cur:
                self._nav_label.configure(text="")
                return
            bits = [cur.kind]
            if cur.file:
                bits.append(cur.file)
            elif cur.task_id:
                bits.append(cur.task_id[:12])
            elif cur.label:
                bits.append(cur.label)
            self._nav_label.configure(text=" · ".join(bits)[:40])
        except Exception:
            pass

    def _nav_back(self) -> None:
        if not getattr(self, "_nav", None):
            return
        ctx = self._nav.back()
        self._restore_nav_context(ctx)
        self._update_nav_label()

    def _nav_forward(self) -> None:
        if not getattr(self, "_nav", None):
            return
        ctx = self._nav.forward()
        self._restore_nav_context(ctx)
        self._update_nav_label()

    def _restore_nav_context(self, ctx) -> None:
        if not ctx:
            return
        try:
            if ctx.file and getattr(self, "editor", None):
                self.editor.open_file(ctx.file)
            if ctx.task_id and getattr(self, "task_detail_panel", None):
                self.task_detail_panel.show_task(ctx.task_id)
            if ctx.workspace_mode and hasattr(self, "_workspace_mode"):
                if self._workspace_mode.get() != ctx.workspace_mode:
                    self._workspace_mode.set(ctx.workspace_mode)
                    self._apply_workspace_mode(ctx.workspace_mode)
        except Exception:
            pass

    def _set_theme(self, mode: str) -> None:
        """Переключить тему и переоформить уже построенные панели.

        Раньше здесь был только ctk.set_appearance_mode(mode): виджеты
        CustomTkinter перекрашивались, а semantic tokens в ui.theme — нет.
        Из-за этого переключатель в UI давал half-тему. Теперь set_mode()
        обновляет палитру, а _retheme_widgets() прогоняет apply_frame по
        уже созданным панелям — переключение работает без перезапуска.
        """
        resolved = set_mode(mode)
        self._theme = mode
        self._theme_resolved = resolved
        try:
            self._retheme_widgets()
        except Exception:
            pass
        try:
            _save_ui_cfg({"theme": mode})
        except Exception:
            pass
        try:
            self._update_footer()
        except Exception:
            pass

    def _retheme_widgets(self) -> None:
        """Переоформить панели, уже получившие цвета от theme."""
        for attr, role in (("_left_frame", "sidebar"), ("_center_frame", "shell"),
                           ("_right_frame", "panel")):
            w = getattr(self, attr, None)
            if w is not None and hasattr(w, "configure"):
                apply_frame(w, role=role)
        for name in (
            "projects", "explorer", "chat", "terminal_panel", "logs", "metrics",
            "history", "queue_panel", "workers_panel", "phone_bus_panel",
            "extensions_panel", "skills_panel", "recipes_panel", "pev_panel",
            "sentinel_panel", "diff_panel", "plan_panel", "problems_panel",
            "search_panel", "changes_panel", "project_center", "task_detail_panel",
        ):
            w = getattr(self, name, None)
            if w is None:
                continue
            # Панель должна быть виджетом: у некоторых (queue_panel.history)
            # одноимённый атрибут — метод, и apply_frame на нём падает.
            if not hasattr(w, "configure"):
                continue
            apply_frame(w, role="panel")
            for attr, role in (("_history", "history"), ("_text", "history"),
                               ("_body", "history"), ("history", "history")):
                box = getattr(w, attr, None)
                if box is not None and hasattr(box, "configure"):
                    configure_textbox(box, role=role)

    def _toggle_theme(self) -> None:
        nxt = "light" if self._theme == "dark" else "dark"
        self.theme_var.set(nxt)
        self._set_theme(nxt)


    def _submit_composer_task(self, task: dict) -> None:
        """Enqueue composer task into local/desktop intake — not execute worker."""
        try:
            from pathlib import Path
            import json
            from ui.paths import agentbus_root
            root = Path(agentbus_root())
            incoming = root / "channels" / "desktop" / "incoming"
            incoming.mkdir(parents=True, exist_ok=True)
            tid = task.get("id") or "task"
            path = incoming / f"{tid}.json"
            path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                self.chat.append("System", f"Composer → queue: {tid}")
            except Exception:
                pass
        except Exception as exp:
            try:
                self.chat.append("System", f"Composer error: {exp}")
            except Exception:
                pass

    def _open_composer(self) -> None:
        import customtkinter as ctk
        win = ctk.CTkToplevel(self)
        win.title("Composer")
        win.geometry("520x480")
        try:
            win.transient(self)
        except Exception:
            pass
        from ui.composer_panel import ComposerPanel
        def _proj():
            try:
                return self.projects.selected_project()
            except Exception:
                try:
                    return getattr(self.projects, "_selected", None) or None
                except Exception:
                    return None
        ComposerPanel(win, get_project=_proj, on_submit=self._submit_composer_task).pack(
            fill="both", expand=True
        )

    def _show_help(self) -> None:
        try:
            from ui.help_dialog import show_help
            show_help(self)
        except Exception as exp:
            try:
                self.chat.append("System", f"help: {exp}")
            except Exception:
                pass

    def _update_footer(self, *, busy: bool = False) -> None:
        """FC-21: unified footer — dispatcher · queue · plan."""
        try:
            from ui.status_labels import format_footer
            from ui.dispatcher_ctl import is_running
            on = bool(is_running())
            n = 0
            try:
                from ui.metrics_panel import _queue_counts
                q = _queue_counts() or {}
                n = int(q.get("desktop") or q.get("queued") or q.get("pending") or 0)
            except Exception:
                n = 0
            plan_active = None
            plan_pending = None
            try:
                root = self._current_project_root()
                from app.plan_service import PlanService
                data = PlanService(root).list_plan()
                steps = data.get("steps") or []
                plan_active = int(data.get("active") or 0)
                plan_pending = sum(
                    1
                    for s in steps
                    if isinstance(s, dict)
                    and str(s.get("status") or "").upper() in ("PENDING", "READY", "TODO", "")
                )
            except Exception:
                pass
            problems_n = 0
            try:
                if getattr(self, "problems_panel", None) and getattr(self.problems_panel, "_rows", None):
                    problems_n = len(self.problems_panel._rows)
            except Exception:
                problems_n = 0
            dirty_n = 0
            try:
                ed = getattr(self, "editor", None)
                if ed is not None and hasattr(ed, "dirty_count"):
                    dirty_n = int(ed.dirty_count())
            except Exception:
                dirty_n = 0
            text = format_footer(
                dispatcher_on=on,
                queue_n=n,
                busy=busy,
                plan_active=plan_active,
                plan_pending=plan_pending,
                problems_n=problems_n,
                dirty_n=dirty_n,
            )
            if hasattr(self, "footer_status"):
                self.footer_status.configure(text=text)
            if hasattr(self, "disp_badge"):
                from ui.i18n_ui import t as _t
                self.disp_badge.configure(
                    text=_t("disp_on", default="диспетчер активен") if on else _t("disp_off", default="диспетчер выключен")
                )
        except Exception:
            pass

    def _run_diagnose(self) -> None:
        """FC-18: core.doctor product report → chat."""
        try:
            from ui.paths import ensure_sys_path
            ensure_sys_path()
            try:
                from core.doctor import doctor_full_text as doctor_text
                out = doctor_text()
            except Exception:
                import io
                import contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    try:
                        from core.doctor import print_doctor
                        print_doctor()
                    except Exception as exp:
                        print(f"doctor unavailable: {exp}")
                out = buf.getvalue().strip() or "diagnose empty"
            self.chat.append("System", out[:5000], kind="system")
        except Exception as exc:
            try:
                self.chat.append("System", f"diagnose error: {exc}", kind="error")
            except Exception:
                pass


    def _apply_onboarding_cfg(self) -> None:
        """Подхватить default_project и beginner-режим после wizard / из ui.yaml."""
        try:
            cfg = _load_ui_cfg()
        except Exception:
            cfg = {}
        project = str(cfg.get("default_project") or "").strip()
        if project:
            try:
                from pathlib import Path as _P
                if _P(project).is_dir() and hasattr(self.projects, "select_or_add"):
                    self.projects.select_or_add(project)
                elif hasattr(self.projects, "set_selected"):
                    self.projects.set_selected(project)
                else:
                    # best-effort: status label + env
                    import os
                    os.environ["AGENTBUS_PROJECT"] = project
                    self.status.configure(text=f"Проект: {project}")
            except Exception:
                pass
        if cfg.get("setup_complete") and cfg.get("auto_start_dispatcher", True):
            try:
                if not disp_is_running():
                    # не автозапускаем при каждом открытии если юзер стопил —
                    # только если флаг явно true и ещё не running
                    if cfg.get("auto_start_once"):
                        cfg.pop("auto_start_once", None)
                        try:
                            _save_ui_cfg({"auto_start_once": False})
                        except Exception:
                            pass
                        self._start_dispatcher()
            except Exception:
                pass
        # Баннер если диспетчер выключен
        try:
            if not disp_is_running():
                self.chat.append(
                    "System",
                    "Диспетчер не запущен — нажмите «▶ Запустить диспетчер», "
                    "иначе задачи из чата не обработаются.",
                )
        except Exception:
            pass




    def _safe_refresh(self, name: str) -> None:
        """Refresh optional panel if present (beginner may hide advanced tabs)."""
        try:
            p = getattr(self, name, None)
            if p is not None and hasattr(p, "refresh"):
                p.refresh()
        except Exception:
            pass

    def _footer_loop(self) -> None:
        try:
            self._update_footer()
            self._notify_open_decisions()
        except Exception:
            pass
        try:
            self.after(2500, self._footer_loop)
        except Exception:
            pass

    def _welcome_desktop(self) -> None:
        """One-shot product tip into chat (not a second onboarding)."""
        try:
            if getattr(self, "_welcomed", False):
                return
            self._welcomed = True
            from ui.i18n_ui import t as _t
            tips = [
                _t("welcome_1", default="NVCode: чат — основной ввод. Ctrl+K — Composer и команды."),
                _t("welcome_2", default="После задачи: Report → Review / Continue / Undo."),
                _t("welcome_3", default="Agent · … — профиль. ? у Health — почему."),
            ]
            try:
                from app.agent_behavior import behavior_summary, effective_behavior, PROFILE_BEGINNER
                tips.append(behavior_summary())
                if effective_behavior().profile == PROFILE_BEGINNER:
                    tips.append(
                        _t(
                            "welcome_beginner_tabs",
                            default="Профиль Новичок: технические вкладки скрыты. Ctrl+K → Show all tabs.",
                        )
                    )
            except Exception:
                pass
            self.chat.append("System", "\n".join(tips))
            self._update_footer()
        except Exception:
            pass

    def _start_dispatcher(self) -> None:

        ok, msg = disp_start()
        self.chat.append("System", f"▶ Диспетчер: {msg}", kind="info")
        self._term("dispatcher", f"start: {msg}")
        self._poll_dispatcher_lock()
        try:
            self._update_footer()
        except Exception:
            pass


    def _stop_dispatcher(self) -> None:
        ok, msg = disp_stop()
        self.chat.append("System", f"■ Диспетчер: {msg}", kind="info")
        self._term("dispatcher", f"stop: {msg}")
        self._poll_dispatcher_lock()
        try:
            self._update_footer()
        except Exception:
            pass


    def _poll_dispatcher_lock(self) -> None:
        root = agentbus_root()
        lock = root / "dispatcher.lock"
        alt = root / ".agentbus" / "dispatcher.lock"
        running = lock.is_file() or alt.is_file() or disp_is_running()
        if not running:
            ch = root / "channels"
            try:
                for d in ch.glob("*/processing"):
                    if any(d.glob("*.json")):
                        running = True
                        break
            except OSError:
                pass
        desk = 0
        try:
            from ui.metrics_panel import _queue_counts
            desk = int(_queue_counts().get("desktop") or 0)
        except Exception:
            pass
        base = "диспетчер: работает" if running else "диспетчер: не запущен"
        if desk:
            base = f"{base} · очередь ПК: {desk}"
        self.lock_lbl.configure(
            text=base,
            text_color=("green" if running else "orange"),
        )
        try:
            running = disp_is_running()
            qn = 0
            try:
                from core.local_queue import get_local_queue
                qn = get_local_queue(agentbus_root()).size()
            except Exception:
                pass
            st = "активен" if running else "остановлен"
            if hasattr(self, "footer_status"):
                self.footer_status.configure(
                    text=f"диспетчер: {st}  ·  очередь: {qn}  ·  проект: {(self.projects.selected_project() if callable(getattr(self.projects, 'selected_project', None)) else getattr(self.projects, 'selected_project', None)) or '—'}"
                )
            if hasattr(self, "disp_led"):
                # Раньше здесь стоял локальный фолбэк
                # SUCCESS, DANGER = ("#4ec9b0", "#f14c4c") на случай
                # ImportError — он маскировал бы неработающий theme engine.
                self.disp_led.configure(text_color=SUCCESS if running else DANGER)
                if hasattr(self, "disp_led_text"):
                    self.disp_led_text.configure(
                        text=("диспетчер активен" if running else "диспетчер выключен"),
                        text_color=SUCCESS if running else "gray",
                    )
        except Exception:
            pass
        self.after(3000, self._poll_dispatcher_lock)

    def _start_project_watcher(self, project: str) -> None:
        try:
            if self._file_watcher is not None:
                try:
                    self._file_watcher.stop()
                except Exception:
                    pass
                self._file_watcher = None
            from ui.paths import ensure_sys_path
            ensure_sys_path()
            from core.config import resolve_project
            from safety.file_watcher import FileWatcher
            root = resolve_project(project)
            if not root:
                return
            def _cb(path, event):
                try:
                    self.chat.append("System", f"file {event}: {path}")
                except Exception:
                    pass
            fw = FileWatcher(root, _cb)
            mode = fw.start()
            self._file_watcher = fw
            self.chat.append("System", f"watcher:{mode} on {root}")
        except Exception as exc:
            try:
                self.chat.append("System", f"watcher: {exc}")
            except Exception:
                pass

    def _current_project_root(self) -> str:
        try:
            name = getattr(self.projects, "selected_project", None) or ""
            if not name:
                return str(agentbus_root())
            from ui.paths import ensure_sys_path
            ensure_sys_path()
            from core.config import resolve_project
            return str(resolve_project(name) or agentbus_root())
        except Exception:
            return str(agentbus_root())



    def _review_change(self, path: str, diff_text: str) -> None:
        try:
            if getattr(self, "diff_panel", None):
                self.diff_panel.show_diff_text(path or "workspace", diff_text or "")
            if hasattr(self, "chat") and path:
                self.chat.append("System", f"Review: {path}", kind="info")
        except Exception:
            pass




    def _on_project_center_action(self, action_id: str) -> None:
        try:
            if hasattr(self, "chat") and action_id:
                self.chat.append("System", f"Project: {action_id}", kind="info")
        except Exception:
            pass
        # Keep Plan / Queue in sync after workflow actions
        try:
            if action_id in ("plan_from_advice", "show_plan"):
                if getattr(self, "plan_panel", None) and hasattr(self.plan_panel, "refresh"):
                    self.plan_panel.refresh()
            if action_id in ("step_enqueued",):
                if getattr(self, "plan_panel", None) and hasattr(self.plan_panel, "refresh"):
                    self.plan_panel.refresh()
                if getattr(self, "queue_panel", None) and hasattr(self.queue_panel, "refresh"):
                    self.queue_panel.refresh()
                try:
                    self._term("queue", "plan step enqueued")
                except Exception:
                    pass
        except Exception:
            pass

    def _notify_open_decisions(self) -> None:
        """Surface WAITING_DECISION in chat (once per id)."""
        try:
            root = self._current_project_root()
            from app.plan_service import get_decision_queue
            items = list(get_decision_queue(root).open_items() or [])
            if not items:
                return
            seen = getattr(self, "_notified_decisions", set())
            item = items[0]
            did = str(getattr(item, "id", "") or "")
            if not did or did in seen:
                return
            seen.add(did)
            self._notified_decisions = seen
            title = str(getattr(item, "title", None) or "decision")
            opts = []
            for o in list(getattr(item, "options", None) or [])[:3]:
                opts.append(f"{getattr(o, 'id', '?')}: {getattr(o, 'label', '')}")
            lines = ["Decision needed: " + title] + opts + ["→ Plan panel: buttons A / B / C"]
            self.chat.append("System", "\n".join(lines), kind="info")
        except Exception:
            pass

    def _focus_terminal(self) -> None:
        """F8: перевести фокус в панель терминала."""
        panel = getattr(self, "terminal_panel", None)
        if panel is None:
            return
        try:
            box = getattr(panel, "box", None) or getattr(panel, "text", None)
            if box is not None and hasattr(box, "focus_set"):
                box.focus_set()
        except Exception:
            pass

    def _focus_pane(self, which: str) -> None:
        """Alt+1/Alt+2: фокус в сайдбар (projects) или в чат."""
        target = None
        if which == "projects":
            target = getattr(self, "projects", None)
        elif which == "chat":
            target = getattr(self, "chat", None)
        if target is None:
            return
        try:
            for attr in ("entry", "text", "box", "_entry", "_text"):
                widget = getattr(target, attr, None)
                if widget is not None and hasattr(widget, "focus_set"):
                    widget.focus_set()
                    return
            if hasattr(target, "focus_set"):
                target.focus_set()
        except Exception:
            pass

    def _refresh_scm(self) -> None:
        try:
            if getattr(self, "changes_panel", None):
                self.changes_panel.refresh()
                self._term("scm", "refreshed")
        except Exception:
            pass

    def _on_history_select(self, task_id: str, row: dict | None = None) -> None:

        """History card → Task Detail + Diff + optional Problems context."""
        try:
            if task_id and getattr(self, "task_detail_panel", None):
                self.task_detail_panel.show_task(task_id)
            if task_id and getattr(self, "diff_panel", None):
                try:
                    self.diff_panel.show_for_task(task_id)
                except Exception:
                    pass
            try:
                self._nav_push(kind="task", task_id=task_id, label=task_id)
            except Exception:
                pass
            try:
                self._term("history", f"open {task_id}")
            except Exception:
                pass
            # surface error text briefly in chat for errors
            try:
                if row and str(row.get("_state") or "") in ("errors", "error"):
                    err = str(row.get("error") or row.get("message") or "")[:200]
                    if err and hasattr(self, "chat"):
                        self.chat.append("System", f"History ERROR {task_id}: {err}", kind="error")
            except Exception:
                pass
        except Exception as exp:
            try:
                self.chat.append("System", f"history select: {exp}")
            except Exception:
                pass

    def _on_queue_select(self, task_id: str) -> None:

        try:
            if getattr(self, "task_detail_panel", None):
                self.task_detail_panel.show_task(task_id)
            if getattr(self, "diff_panel", None) and task_id:
                try:
                    self.diff_panel.show_for_task(task_id)
                except Exception:
                    pass
            self._nav_push(kind="task", task_id=task_id, label=task_id)
        except Exception:
            pass

    def _editor_context_for_chat(self) -> dict:
        try:
            if getattr(self, "editor", None):
                return self.editor.get_context()
        except Exception:
            pass
        return {}

    def _open_task_from_problem(self, task_id: str) -> None:
        try:
            if task_id and getattr(self, "task_detail_panel", None):
                self.task_detail_panel.show_task(task_id)
        except Exception:
            pass

    def _open_task_diff(self, task_id: str) -> None:
        """Открыть diff конкретной задачи (кнопка «Diff» в панели задачи)."""
        try:
            if not task_id:
                return
            try:
                name = getattr(self, "_tab_diff_name", None)
                if name and getattr(self, "_right_tabs", None) is not None:
                    self._right_tabs.set(name)
            except Exception:
                pass
            dp = getattr(self, "diff_panel", None)
            if dp is not None and hasattr(dp, "show_for_task"):
                dp.show_for_task(task_id)
        except Exception:
            pass

    def _open_in_editor(self, rel_path: str, line: int | None = None) -> None:
        try:
            ed = getattr(self, "editor", None) or getattr(self, "editor_panel", None)
            if ed is not None:
                try:
                    ed.open_file(rel_path, line=line)
                except TypeError:
                    ed.open_file(rel_path)
            try:
                self._nav_push(kind="file", file=rel_path, label=rel_path)
            except Exception:
                pass
            try:
                self._term("editor", f"open {rel_path}" + (f":{line}" if line else ""))
            except Exception:
                pass
        except Exception:
            pass

    def _on_editor_active(self, path: str, selection: str) -> None:
        try:
            import sys
            from pathlib import Path as P
            base = P(__file__).resolve().parents[1]
            if str(base / "src") not in sys.path:
                sys.path.insert(0, str(base / "src"))
            from app.agent_service import AgentService
            if not hasattr(self, "_agent_svc"):
                self._agent_svc = AgentService()
            root = ""
            try:
                sp = self.projects.selected_project
                root = sp() if callable(sp) else (sp if isinstance(sp, str) else "")
            except Exception:
                root = ""
            if root:
                self._agent_svc.set_root(root)
            self._agent_svc.set_editor_context(active_file=path or "", selection=selection or "")
        except Exception:
            pass

    def _on_project(self, name: str) -> None:
        try:
            if getattr(self, "project_center", None):
                self.project_center.refresh()
        except Exception:
            pass
        try:
            if getattr(self, "explorer", None):
                self.explorer.refresh()
        except Exception:
            pass
        self.status.configure(text=_t("project_label", default="Проект: {name}", name=name))
        self._start_project_watcher(name)
        try:
            self._safe_refresh("sentinel_panel")
        except Exception:
            pass
        # New project → new conversation session (avoid cross-project context leak)
        try:
            sid = self.chat.switch_project(name)
            self.chat.append("System", f"Активный проект: {name} · session={sid}")
        except Exception:
            self.chat.append("System", f"Активный проект: {name}")


    def _load_task_explanation(self, task_id: str) -> dict | None:
        if not task_id:
            return None
        root = agentbus_root() / "channels"
        if not root.is_dir():
            return None
        for p in root.glob(f"*/done/{task_id}.json"):
            try:
                import json
                data = json.loads(p.read_text(encoding="utf-8"))
                meta = data.get("metadata") if isinstance(data, dict) else None
                if isinstance(meta, dict) and isinstance(meta.get("last_explanation"), dict):
                    return meta["last_explanation"]
            except Exception:
                continue
        # stem match
        for p in root.glob("*/done/*.json"):
            if task_id in p.stem:
                try:
                    import json
                    data = json.loads(p.read_text(encoding="utf-8"))
                    meta = data.get("metadata") if isinstance(data, dict) else None
                    if isinstance(meta, dict) and isinstance(meta.get("last_explanation"), dict):
                        return meta["last_explanation"]
                except Exception:
                    continue
        return None

    def _poll_skill_proposals(self) -> None:
        try:
            from ui.paths import ensure_sys_path
            ensure_sys_path()
            from skills.skill_learner import GLOBAL_SKILL_LEARNER
            cands = GLOBAL_SKILL_LEARNER.find_candidates()
            if cands:
                prop = GLOBAL_SKILL_LEARNER.propose_to_user(cands[0])
                self.chat.show_skill_proposal(prop)
        except Exception:
            pass
        self.after(60000, self._poll_skill_proposals)



    def _after_diff_action(self, action: str, task_id: str) -> None:
        try:
            self.chat.append("System", f"Diff {action}: {task_id}")
        except Exception:
            pass
        self._sync_after_task(task_id or "")

    def _on_task_sent(self, task_id: str) -> None:
        """FC-44: after chat enqueue — open Task Detail + refresh queue."""
        try:
            if getattr(self, "task_detail_panel", None) and task_id:
                self.task_detail_panel.show_task(task_id)
        except Exception:
            pass
        try:
            if getattr(self, "queue_panel", None) and hasattr(self.queue_panel, "refresh"):
                self.queue_panel.refresh()
        except Exception:
            pass
        try:
            self._update_footer(busy=True)
        except Exception:
            pass

    def _sync_after_task(self, task_id: str = "") -> None:
        """FC-44: keep Queue / Changes / Project / History in sync after DONE/ERROR."""
        for name in ("queue_panel", "changes_panel", "project_center", "history", "metrics", "explorer", "problems_panel", "plan_panel"):
            panel = getattr(self, name, None)
            if panel is None:
                continue
            try:
                if hasattr(panel, "refresh"):
                    panel.refresh()
            except Exception:
                pass
        try:
            if task_id and getattr(self, "task_detail_panel", None):
                self.task_detail_panel.show_task(task_id)
        except Exception:
            pass
        try:
            self._update_footer(busy=False)
        except Exception:
            pass

    def _on_continue(self, action: str, task_id: str = "") -> None:
        """P5: Continue / Changes / Trace from chat after DONE."""
        try:
            if action == "continue":
                # User clicked Continue = explicit consent to enqueue next plan step
                try:
                    root = self._current_project_root()
                    from app.project_workflow import ProjectWorkflow
                    r = ProjectWorkflow(root).enqueue_first_pending()
                    if r.get("ok"):
                        tid = r.get("task_id") or ""
                        step = r.get("step_action") or ""
                        self.chat.append(
                            "System",
                            f"Continue → queued {tid}\n{step}",
                            kind="info",
                        )
                        try:
                            if getattr(self, "queue_panel", None):
                                self.queue_panel.refresh()
                            if getattr(self, "plan_panel", None):
                                self.plan_panel.refresh()
                            self._term("queue", f"continue enqueued {tid}")
                        except Exception:
                            pass
                    else:
                        err = r.get("error") or "no pending step"
                        self.chat.append(
                            "System",
                            f"Continue: {err}\nОпишите следующую задачу в чате или /compose",
                            kind="info",
                        )
                        try:
                            self.chat.input.focus_force()
                        except Exception:
                            pass
                except Exception as exp:
                    self.chat.append("System", f"Continue: {exp}")
            elif action == "changes":
                try:
                    if hasattr(self, "_on_activity_select"):
                        self._on_activity_select("git")
                    if getattr(self, "changes_panel", None):
                        self.changes_panel.refresh()
                    if task_id and getattr(self, "diff_panel", None):
                        self.diff_panel.show_for_task(task_id)
                except Exception as exp:
                    self.chat.append("System", f"changes: {exp}")
            elif action == "trace":
                try:
                    if task_id and getattr(self, "task_detail_panel", None):
                        self.task_detail_panel.show_task(task_id)
                        if hasattr(self, "_on_activity_select"):
                            self._on_activity_select("plan")
                except Exception:
                    pass
        except Exception as exp:
            try:
                self.chat.append("System", f"on_continue: {exp}")
            except Exception:
                pass


    def _sync_plan_step_for_task(self, task_id: str, *, status: str = "DONE") -> None:
        """If LivingPlan step meta.task_id matches, update step status."""
        if not task_id:
            return
        try:
            root = self._current_project_root()
            from app.plan_service import PlanService
            data = PlanService(root).list_plan()
            for s in data.get("steps") or []:
                meta = s.get("meta") or {}
                if str(meta.get("task_id") or "") == str(task_id):
                    PlanService(root).set_step_status(str(s.get("id")), status, note=f"task {status}")
                    if getattr(self, "plan_panel", None):
                        self.plan_panel.refresh()
                    break
        except Exception:
            pass

    def _on_done(self, task_id: str, detail: str) -> None:

        exp = self._load_task_explanation(task_id)
        self.chat.notify_done(task_id, detail, explanation=exp)
        try:
            if getattr(self, "task_detail_panel", None) and task_id:
                self.task_detail_panel.show_task(task_id)
        except Exception:
            pass
        try:
            if hasattr(self, "diff_panel") and task_id:
                self.diff_panel.show_for_task(task_id)
        except Exception:
            pass
        try:
            self.bell()
        except Exception:
            pass
        if self._toast:
            notify(_t("toast_done", default="AgentBus · DONE"), detail or task_id or "задача выполнена", dedupe_key=f"done:{task_id}")
        self._sync_after_task(task_id)
        try:
            self._sync_plan_step_for_task(task_id, status="DONE")
        except Exception:
            pass
        try:
            self._term("done", f"{task_id}: {detail}")
        except Exception:
            pass

    def _on_error(self, task_id: str, detail: str) -> None:
        self.chat.notify_error(task_id, detail)
        try:
            self._term("error", f"{task_id}: {detail}")
        except Exception:
            pass
        try:
            if getattr(self, "problems_panel", None):
                self.problems_panel.refresh()
        except Exception:
            pass
        self._sync_after_task(task_id)
        try:
            self._sync_plan_step_for_task(task_id, status="ERROR")
        except Exception:
            pass
        if self._toast:
            notify(_t("toast_error", default="AgentBus · ERROR"), detail or task_id or "ошибка", dedupe_key=f"err:{task_id}")
        try:
            self.history.refresh()
        except Exception:
            pass

    def _on_chat_command(self, cmd: str) -> bool:
        c = (cmd or "").strip().lower()
        if c in ("/help", "/?"):
            self.chat.append(
                "System",
                "Язык UI: ? почему · ⚠ проблема · → действие · ↶ Undo\n"
                "Agent · … — профиль · Ctrl+K — палитра\n"
                "После DONE: Review / Continue / Undo\n"
                "\n"
                "Команды:\n"
                "/help — справка\n"
                "/status — dispatcher + очередь\n"
                "/compose — форма новой задачи\n"
                "/audit — аудит проекта\n"
                "/health — project health\n"
                "/plan — текущий LivingPlan\n"
                "/workflow — Audit→Advisor→Plan\n"
                "/workers — workers.yaml\n"
                "/metrics — обновить метрики\n"
                "/history — обновить историю\n"
                "/clear — очистить чат",
            )
            return True
        if c == "/clear":
            self.chat._clear_history()
            return True

        if c in ("/compose", "/composer"):
            try:
                self._open_composer()
            except Exception as exp:
                self.chat.append("System", f"compose: {exp}")
            return True
        if c == "/audit":
            try:
                root = self._current_project_root()
                from app.project_service import ProjectService
                text = ProjectService(root).run_audit_text()
                self.chat.append("System", (text or "empty audit")[:4000], kind="system")
            except Exception as exp:
                self.chat.append("System", f"audit: {exp}")
            return True
        if c == "/health":
            try:
                root = self._current_project_root()
                from app.project_service import ProjectService
                text = ProjectService(root).get_health_text()
                self.chat.append("System", (text or "no health")[:4000], kind="system")
            except Exception as exp:
                self.chat.append("System", f"health: {exp}")
            return True
        if c == "/plan":
            try:
                root = self._current_project_root()
                from app.plan_service import PlanService
                data = PlanService(root).list_plan()
                steps = data.get("steps") if isinstance(data, dict) else data
                if not isinstance(steps, list):
                    steps = []
                lines_out = ["LivingPlan:"]
                for s in steps[:20]:
                    if isinstance(s, dict):
                        lines_out.append(
                            f"  [{s.get('status')}] {s.get('title') or s.get('action') or s.get('id')}"
                        )
                    else:
                        lines_out.append(f"  {s}")
                if len(lines_out) == 1:
                    lines_out.append("  (empty)")
                self.chat.append("System", "\n".join(lines_out), kind="system")
            except Exception as exp:
                self.chat.append("System", f"plan: {exp}")
            return True
        if c.startswith("/accept"):
            try:
                parts = c.split()
                idx = int(parts[1]) - 1 if len(parts) > 1 else 0
                root = self._current_project_root()
                from app.project_workflow import ProjectWorkflow
                r = ProjectWorkflow(root).accept_finding(max(0, idx))
                if r.get("ok"):
                    self.chat.append("System", f"Plan +{r.get('step_id')}: {r.get('action')}", kind="info")
                    try:
                        if getattr(self, "plan_panel", None):
                            self.plan_panel.refresh()
                    except Exception:
                        pass
                else:
                    self.chat.append("System", f"accept: {r.get('error')}", kind="error")
            except Exception as exp:
                self.chat.append("System", f"accept: {exp}")
            return True
        if c == "/run":
            try:
                ed = getattr(self, "editor", None)
                path = getattr(ed, "_current", None) if ed else None
                if not path:
                    self.chat.append("System", "Нет файла в editor", kind="error")
                else:
                    msg = f"run python {path}"
                    if hasattr(self.chat, "send_message"):
                        self.chat.send_message(msg)
                    else:
                        self.chat.append("System", f"Run: {msg}", kind="info")
                    self._term("run", path)
            except Exception as exp:
                self.chat.append("System", f"run: {exp}")
            return True
        if c in ("/wrap", "/wordwrap"):
            self._toggle_wrap()
            return True
        if c == "/autosave":

            self._toggle_autosave()
            return True
        if c in ("/symbol", "/outline"):
            self._goto_symbol()
            return True
        if c == "/search":

            try:
                self._open_search()
            except Exception as exp:
                self.chat.append("System", f"search: {exp}")
            return True
        if c == "/problems":

            try:
                if getattr(self, "problems_panel", None):
                    self.problems_panel.refresh()
                self.chat.append("System", "Problems refreshed", kind="info")
                self._term("problems", "slash refresh")
            except Exception as exp:
                self.chat.append("System", f"problems: {exp}")
            return True
        if c in ("/term", "/terminal"):
            try:
                if "clear" in c or (hasattr(self, "chat") and False):
                    pass
                parts = c.split()
                if len(parts) > 1 and parts[1] == "clear":
                    if getattr(self, "terminal_panel", None):
                        self.terminal_panel.clear()
                    self.chat.append("System", "Terminal cleared")
                else:
                    tp = getattr(self, "terminal_panel", None)
                    if tp and hasattr(tp, "focus"):
                        tp.focus()
                    self._term("ui", "terminal focus")
            except Exception as exp:
                self.chat.append("System", f"term: {exp}")
            return True
        if c in ("/scm", "/changes"):


            self._refresh_scm()
            self.chat.append("System", "Source Control refreshed", kind="info")
            return True
        if c == "/enqueue":


            try:
                root = self._current_project_root()
                from app.project_workflow import ProjectWorkflow
                r = ProjectWorkflow(root).enqueue_first_pending()
                if r.get("ok"):
                    self.chat.append(
                        "System",
                        f"Enqueued {r.get('task_id')}: {r.get('step_action')}",
                        kind="info",
                    )
                    try:
                        if getattr(self, "queue_panel", None):
                            self.queue_panel.refresh()
                    except Exception:
                        pass
                else:
                    self.chat.append("System", f"enqueue: {r.get('error')}", kind="error")
            except Exception as exp:
                self.chat.append("System", f"enqueue: {exp}")
            return True
        if c == "/workflow":

            try:
                root = self._current_project_root()
                from app.project_workflow import ProjectWorkflow
                text = ProjectWorkflow(root).format_preview()
                self.chat.append("System", (text or "empty")[:4000], kind="system")
            except Exception as exp:
                self.chat.append("System", f"workflow: {exp}")
            return True
        if c == "/metrics":
            try:
                self.metrics.refresh()
                self.chat.append("System", "Метрики обновлены.")
            except Exception as exc:
                self.chat.append("System", f"metrics: {exc}")
            return True
        if c == "/history":
            try:
                self.history.refresh()
                self.chat.append("System", "История обновлена.")
            except Exception as exc:
                self.chat.append("System", f"history: {exc}")
            return True
        if c == "/status":
            from ui.metrics_panel import _queue_counts
            q = _queue_counts()
            running = disp_is_running()
            pol = ""
            try:
                from ui.paths import ensure_sys_path
                ensure_sys_path()
                from core.policy import load_policy
                p = load_policy()
                pol = f"policy={p.name} cloud={p.allow_cloud}"
            except Exception:
                pol = "policy=?"
            self.chat.append(
                "System",
                f"dispatcher={'active' if running else 'idle'}\n"
                f"desktop_queue={q.get('desktop', 0)}\n"
                f"filebus in={q['incoming']} run={q['processing']} done={q['done']} "
                f"err={q['errors']} def={q['deferred']}\n"
                f"{pol}\n"
                f"primary=desktop chat",
            )
            return True
        if c == "/workers":
            import yaml
            path = agentbus_root() / "config" / "workers.yaml"
            try:
                workers = yaml.safe_load(path.read_text(encoding="utf-8")) or []
                lines = []
                for w in workers:
                    if not isinstance(w, dict):
                        continue
                    lines.append(
                        f"{w.get('name')}: en={w.get('enabled', True)} "
                        f"prio={w.get('priority')} {w.get('provider')}/{w.get('model')}"
                    )
                self.chat.append("System", "\n".join(lines) or "workers.yaml пуст")
            except Exception as exc:
                self.chat.append("System", str(exc))
            return True
        return False

    def _run_project_app(self) -> None:
        try:
            from app.app_runner import start_app, run_status
            # self.project_root не существует -> getattr всегда None.
            # Канонический источник — _current_project_root().
            root = self._current_project_root()
            if not root:
                try:
                    root = self.projects.selected_project() if callable(getattr(self.projects, "selected_project", None)) else getattr(self.projects, "selected_project", "")
                except Exception:
                    root = ""
            if not root:
                self.chat.append("System", "Сначала откройте проект")
                self._term("run", "no project")
                return
            r = start_app(root)
            self.chat.append("System", f"Run: {r}")
            self._term("run", str(r)[:400])
        except Exception as exp:
            self.chat.append("System", f"Run error: {exp}")
            self._term("run", f"error: {exp}")

    def _stop_project_app(self) -> None:
        try:
            from app.app_runner import stop_app
            r = stop_app()
            tail = (r.get("tail") or "").strip()
            msg = f"Stop: running={r.get('running')} code={r.get('returncode')}"
            if tail:
                msg += "\n--- output ---\n" + tail[-1500:]
            self.chat.append("System", msg)
            self._term("run", msg[:500])
            if tail:
                self._term("stdout", tail[-800:])
        except Exception as exp:
            self.chat.append("System", f"Stop error: {exp}")
            self._term("run", f"stop error: {exp}")

    def _poll_app_loop(self) -> None:
        try:
            self._stream_app_output()
        except Exception:
            pass
        try:
            self._poll_eventbus()
        except Exception:
            pass
        try:
            self._poll_app_exit()
        except Exception:
            pass
        try:
            self.after(800, self._poll_app_loop)
        except Exception:
            pass

    def _poll_eventbus(self) -> None:
        """Stream important EventBus lines into Terminal."""
        try:
            from app.event_tail import drain_events, filter_important, format_event_line
            root = ""
            try:
                root = self._recipe_project() or ""
            except Exception:
                root = ""
            events = filter_important(drain_events(root or None, max_lines=30))
            for ev in events:
                line = format_event_line(ev)
                et = str(ev.get("type") or "")
                kind = "done" if "DONE" in et or "PASSED" in et else (
                    "error" if "ERROR" in et or "FAIL" in et or "TIMEOUT" in et else "bus"
                )
                self._term(kind, line)
        except Exception:
            pass

    def _stream_app_output(self) -> None:
        try:
            from app.app_runner import drain_new_output
            for line in drain_new_output():
                self._term("stdout", line)
        except Exception:
            pass

    def _poll_app_exit(self) -> None:
        try:
            from app.app_runner import poll_exit
            r = poll_exit()
            if r.get("exited") and r.get("failed"):
                tail = (r.get("tail") or "")[-1200:]
                self.chat.append(
                    "System",
                    f"Application exited with code {r.get('returncode')}\n{tail}\n"
                    "Можно: «исправь эту ошибку» в чате.",
                )
                self._term("run", f"exit {r.get('returncode')}")
                if tail:
                    self._term("stdout", tail[-600:])
        except Exception:
            pass

    def _open_agent_popover(self) -> None:
        try:
            from ui.agent_popover import AgentPopover
            from app.agent_behavior import behavior_summary, load_agent_behavior
            def _refresh():
                if getattr(self, "_agent_label", None) is not None:
                    try:
                        self._agent_label.configure(text=behavior_summary(load_agent_behavior()))
                    except Exception:
                        pass
            AgentPopover(
                self,
                on_change=_refresh,
                project_root=lambda: self._current_project_root(),
            ).open()
        except Exception as exp:
            try:
                self.chat.append("System", f"Agent settings: {exp}")
            except Exception:
                pass

    def _open_settings(self) -> None:
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.focus()
            return
        win = ctk.CTkToplevel(self)
        win.title("Настройки NVCode")
        win.geometry("780x620")
        SettingsPanel(win).pack(fill="both", expand=True)
        self._settings_win = win
