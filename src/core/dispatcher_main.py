# -*- coding: utf-8 -*-
"""AgentBus Dispatcher — логика запуска (лежит в src/)."""
from __future__ import annotations
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

# AgentBus root = parent of src/ (this file lives in src/core/)
ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parents[1]
for p in (SRC, ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)



def _ver_label(name: str, path: str, args=("--version",), cwd=None, tmo=20) -> str:
    if not path:
        return f"{name} = не найден"
    line = "n/a"
    try:
        r = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=tmo,
            cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or r.stderr or "").strip().splitlines()
        line = out[0] if out else f"exit={r.returncode}"
    except FileNotFoundError:
        line = "не найден"
    except Exception as exp:
        line = f"ошибка: {type(exp).__name__}"
    return f"{name} = {path}\n    {line}"


def _diag_header() -> None:
    print(f"AgentBus root    = {ROOT}")
    print(f"src/             = {SRC}")
    print(f"Python           = {sys.executable} v{sys.version.split()[0]}")
    print(f"Чтобы проверить   : {sys.executable} -m pytest -q tests")


def _diag_config() -> None:
    from core.config import (
        AIDER_PATH, OPENCODE_PATH, AIDER_PYTHON, OLLAMA_PATH,
        list_projects, GIT_ENABLED, WORKERS_FILE, PROVIDERS_FILE,
    )
    from providers.registry import load_providers
    from providers.capacity import FreeCapacityManager
    print("\n-- конфиг --")
    print(f"WORKERS_FILE   = {WORKERS_FILE} exists={Path(WORKERS_FILE).is_file()}")
    print(f"PROVIDERS_FILE = {PROVIDERS_FILE} exists={Path(PROVIDERS_FILE).is_file()}")
    print("\n-- инструменты --")
    print(_ver_label("Git", shutil.which("git") or ""))
    print(_ver_label("Ollama", OLLAMA_PATH, args=("-v",)))
    print(_ver_label("Aider", AIDER_PATH, cwd=str(ROOT)))
    print(_ver_label("OpenCode", OPENCODE_PATH, cwd=str(ROOT)))
    print(f"AiderPython = {AIDER_PYTHON or 'не задан'}")
    print(f"GIT_ENABLED = {GIT_ENABLED}")
    print(f"TASK_CHANNEL = file-bus (Dropbox/local)")


def _diag_projects() -> None:
    print("\n-- проекты --")
    try:
        projs = list_projects()
        for name, path in projs.items():
            print(f"  {name}: {path}")
        if not projs:
            print("  (нет PROJECT_* в .env)")
    except Exception as exp:
        print(f"  err: {exp}")


def _diag_modules() -> None:
    print("\n-- python-модули --")
    # A bare __import__ is not proof: PyPI `aider` is an unrelated package that
    # imports fine but ships no CLI. Ask the *distribution* what is installed.
    for dist, mod in (("gitpython", "git"), ("pytest", "pytest"), ("aider-chat", "aider")):
        try:
            from importlib.metadata import version

            ver = version(dist)
        except Exception:
            print(f"  {mod:12s} НЕ найден")
            continue
        try:
            __import__(mod)
            print(f"  {mod:12s} OK v{ver}")
        except Exception:
            print(f"  {mod:12s} v{ver} (дистрибутив есть, импорт падает)")


def _diag_providers() -> None:
    print("\n-- провайдеры / воркеры --")
    try:
        ps = load_providers()
        cm = FreeCapacityManager(ps)
        for p in ps:
            state = "usable" if p.is_usable() else "disabled/недоступен"
            extra = f" (env-ворота {p.env_gate})" if p.env_gate else ""
            print(f"  {p.id:10s} {p.type:18s} {p.billing:6s} {state}{extra}")
        try:
            from core.workers import load_workers
            for w in load_workers():
                usable = "run" if cm.worker_usable(w) else "blocked"
                print(f"  worker {w.name:20s} [{w.harness}/{w.provider}] enabled={w.enabled} -> {usable}")
        except Exception as exp:
            print(f"  воркеры: {type(exp).__name__}: {exp}")
    except Exception as exp:
        print(f"  провайдеры: {type(exp).__name__}: {exp}")


def _diag_budget_metrics_env() -> None:
    try:
        from utils.budget import GLOBAL_BUDGET
        print("\n-- бюджет --")
        for name, d in GLOBAL_BUDGET.snapshot().items():
            parts = []
            if d.get("day_limit") is not None:
                parts.append(f"{d['day_calls']}/{d['day_limit']} day")
            if d.get("month_token_limit") is not None:
                parts.append(f"{d['month_tokens']}/{d['month_token_limit']} tok/mo")
            print(f"  {name}: " + (" · ".join(parts) if parts else "unlimited"))
    except Exception as exp:
        print(f"  budget: {exp}")
    try:
        from utils.metrics import GLOBAL_METRICS
        print("\n-- metrics --")
        print(f"  {GLOBAL_METRICS.get_summary()}")
    except Exception as exp:
        print(f"  metrics: {exp}")
    try:
        from utils.diagnose import diagnose_environment
        diagnose_environment()
    except Exception as exp:
        print(f"  environment: {exp}")
    


def _diag_task_channel() -> None:
    print("\n-- канал задач (продукт) --")
    print("  primary = desktop chat (UI) → .agentbus/desktop_queue/")
    print("  phone file-bus = optional (feature phone_filebus)")
    try:
        from core.config import BASE_DIR, CHANNELS, CHANNELS_ROOT
        from core.bus import FileBus
        FileBus(Path(BASE_DIR), tuple(CHANNELS)).ensure()
        desk = Path(CHANNELS_ROOT) / "desktop"
        ok = all((desk / s).is_dir() for s in ("processing", "done", "errors"))
        status = "OK" if ok else "MISSING"
        print(f"  desktop channel = {status} {desk}")
    except Exception as exp:
        print(f"  desktop channel ERR: {exp}")
    try:
        from core.local_queue import get_local_queue
        from core.config import BASE_DIR
        n = get_local_queue(Path(BASE_DIR)).size()
        print(f"  desktop_queue size = {n}")
    except Exception as exp:
        print(f"  desktop_queue ERR: {exp}")


def _diag_flags_policy() -> None:
    try:
        from core.feature_flags import is_enabled
        print(f"  phone_filebus = {is_enabled('phone_filebus', default=False)}")
        print(f"  remote_filebus = {is_enabled('remote_filebus', default=False)}")
    except Exception as exp:
        print(f"  flags ERR: {exp}")
    try:
        from core.policy import load_policy
        pol = load_policy()
        print(f"  policy = {pol.name} cloud={pol.allow_cloud} privacy={pol.privacy}")
    except Exception as exp:
        print(f"  policy ERR: {exp}")


def _diag_runtimes_backends() -> None:
    try:
        from cli.init_wizard import discover_local_stack
        disc = discover_local_stack()
        for r in disc.get("runtimes") or []:
            mark = "OK" if r.get("ok") else "--"
            print(f"  runtime[{mark}] {r.get('id')} models={len(r.get('models') or [])}")
    except Exception as exp:
        print(f"  runtimes ERR: {exp}")
    try:
        from core.backend import build_default_registry
        reg = build_default_registry()
        snap = reg.snapshot() if hasattr(reg, "snapshot") else {}
        items = snap.get("backends") if isinstance(snap, dict) else None
        if items is None and hasattr(reg, "list"):
            try:
                items = reg.list()
            except Exception:
                items = []
        n = len(items) if items is not None else 0
        print(f"  backends registered = {n}")
        if isinstance(items, list):
            for it in items[:12]:
                if isinstance(it, dict):
                    print(f"    · {it.get('id') or it}")
                else:
                    print(f"    · {getattr(it, 'id', it)}")
        elif isinstance(items, dict):
            for k in list(items.keys())[:12]:
                print(f"    · {k}")
    except Exception as exp:
        print(f"  backends ERR: {exp}")


def _diagnose() -> int:
    _diag_header()
    _diag_config()
    _diag_projects()
    _diag_modules()
    _diag_providers()
    _diag_budget_metrics_env()
    _diag_task_channel()
    _diag_flags_policy()
    _diag_runtimes_backends()
    # P0.4 go/no-go
    try:
        from core.doctor import print_doctor
        code = print_doctor()
    except Exception as exp:
        print(f"\ndoctor ERR: {exp}")
        code = 1

    print("\nREADY" if code == 0 else "\nNOT READY")
    return code




# Public aliases: scripts/bootstrap.py (and other entrypoints) import these.
run_diagnose = _diagnose
diagnose = _diagnose


def _main_init() -> None:
    try:
        from cli.init_wizard import run_init_wizard
        raise SystemExit(run_init_wizard(verbose=True))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


def _main_recipe(argv: list[str]) -> None:
    try:
        from cli.recipes import emit_recipe, list_recipes
        # parse: --recipe NAME [--target PATH] [--project P] [--channel C]
        name = ""
        target = ""
        project = ""
        channel = "gpt"
        if "--recipe" in argv:
            i = argv.index("--recipe")
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                name = argv[i + 1]
        if name in ("list", "ls", ""):
            for r in list_recipes():
                rid = r.get("metadata", {}).get("recipe") or r.get("id") or "?"
                print(f"  {rid}: {(r.get('message') or '')[:60]}...")
            raise SystemExit(0)
        if "--target" in argv:
            j = argv.index("--target")
            if j + 1 < len(argv):
                target = argv[j + 1]
        if "--project" in argv:
            j = argv.index("--project")
            if j + 1 < len(argv):
                project = argv[j + 1]
        if "--channel" in argv:
            j = argv.index("--channel")
            if j + 1 < len(argv):
                channel = argv[j + 1]
        path = emit_recipe(name, target=target or None, project=project, channel=channel)
        print(f"recipe → {path}")
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


def _main_dashboard(argv: list[str]) -> None:
    try:
        host, port = "127.0.0.1", 8337
        if "--port" in argv:
            i = argv.index("--port")
            if i + 1 < len(argv):
                port = int(argv[i + 1])
        from cli.dashboard_server import run_dashboard
        raise SystemExit(run_dashboard(host=host, port=port))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


def _main_doctor() -> None:
    try:
        raise SystemExit(_diagnose())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv or "-V" in argv:
        print("AgentBus 0.9.1")
        raise SystemExit(0)
    if "--init" in argv or "init" in argv:
        _main_init()
    if "--recipe" in argv:
        _main_recipe(argv)
    if "--dashboard" in argv or "dashboard" in argv:
        _main_dashboard(argv)
    if "--diagnose" in argv or "--doctor" in argv or "doctor" in argv:
        _main_doctor()
    try:
        from core.runtime import Runtime, main as runtime_main
        # Apply feature preset early (AGENTBUS_FEATURE_PRESET / beginner_ru)
        try:
            import os
            preset = (os.getenv("AGENTBUS_FEATURE_PRESET") or "").strip()
            if preset:
                from core.feature_flags import apply_preset
                apply_preset(preset, save=False)
        except Exception:
            pass
        runtime_main()
    except KeyboardInterrupt:
        print("\nостановлено вручную")
    except Exception:
        txt = traceback.format_exc()
        print(txt)
        try:
            (ROOT / "crash.log").write_text(txt, encoding="utf-8")
        except OSError:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
