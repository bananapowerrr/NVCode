# -*- coding: utf-8 -*
from __future__ import annotations
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # src/core/config.py → AgentBus root
ROOT_DIR = BASE_DIR.parent

def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)

for env_path in (BASE_DIR / ".env", ROOT_DIR / ".env"):
    _load_env_file(env_path)

def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

def _flag(name: str, default: bool = True) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def _resolve_config_file(env_name: str, filename: str) -> Path:
    """Prefer config/<filename>, fallback to root; env override wins."""
    override = os.getenv(env_name, "").strip()
    if override:
        return Path(override)
    for candidate in (BASE_DIR / "config" / filename, BASE_DIR / filename):
        if candidate.is_file():
            return candidate
    return BASE_DIR / "config" / filename

AGENTBUS_ROOT = Path(os.getenv("AGENTBUS_ROOT", BASE_DIR))
BUS_ROOT = AGENTBUS_ROOT
CHANNELS_ROOT = AGENTBUS_ROOT / "channels"

_pr = os.getenv("PROJECT_PREDICTION_ANALYZER", "").strip()
PROJECT_ROOT = Path(_pr) if _pr else None


def _load_projects() -> dict[str, Path]:
    """Реестр PROJECT_<NAME>=path. Алиасы: точное имя + lower + с дефисами."""
    projects: dict[str, Path] = {}
    for key, value in os.environ.items():
        if not key.startswith("PROJECT_") or not value.strip():
            continue
        name = key[len("PROJECT_"):]
        path = Path(value.strip())
        projects[name] = path
        projects[name.lower()] = path
        projects[name.replace("_", "-")] = path
        projects[name.replace("-", "_")] = path
        projects[name.replace("_", "-").lower()] = path
        projects[name.replace("-", "_").lower()] = path
    if "Prediction-Analyzer" not in projects and PROJECT_ROOT:
        projects["Prediction-Analyzer"] = PROJECT_ROOT
        projects["prediction-analyzer"] = PROJECT_ROOT
        projects["Prediction_Analyzer"] = PROJECT_ROOT
        projects["PREDICTION_ANALYZER"] = PROJECT_ROOT
    return projects


PROJECTS = _load_projects()


def resolve_project(name: str) -> Path:
    raw = str(name or "").strip()
    p = PROJECTS.get(raw) or PROJECTS.get(raw.lower())
    if not p:
        key = raw.lower().replace("-", "_")
        for k, v in PROJECTS.items():
            if k.lower().replace("-", "_") == key:
                p = v
                break
    if not p and raw:
        # UI/workflow payloads may carry the absolute path rather than the
        # project alias. Accept it only when it resolves to an allowlisted root.
        try:
            candidate = Path(raw).expanduser().resolve()
            for registered in PROJECTS.values():
                if Path(registered).expanduser().resolve() == candidate:
                    p = candidate
                    break
        except (OSError, RuntimeError):
            pass
    if not p:
        known = sorted({k for k in PROJECTS if not k.islower() or "_" in k or "-" in k})[:12]
        raise ValueError(
            f"Проект '{name}' не зарегистрирован. Добавьте PROJECT_<NAME>=<путь> в .env. "
            f"Известные: {known or 'нет'}"
        )
    if not p.exists():
        raise FileNotFoundError(f"Директория проекта не существует: {p}")
    return p


def list_projects() -> dict[str, str]:
    """Уникальные path → canonical name для diagnose."""
    seen: dict[str, str] = {}
    for k, v in PROJECTS.items():
        s = str(v)
        if s not in seen:
            seen[s] = k
    return {name: path for path, name in ((v, k) for k, v in seen.items())}


POLL_SECONDS = max(2, _int("AGENTBUS_POLL_SECONDS", 5))
WORKER_TIMEOUT = max(30, _int("AGENTBUS_WORKER_TIMEOUT", 900))
# Single shared git worktree: never run two root tasks on different projects
# in parallel without isolated worktrees (default 1).
MAX_PARALLEL_PROJECTS = max(1, _int("AGENTBUS_MAX_PARALLEL_PROJECTS", 1))

LEASE_SECONDS = max(WORKER_TIMEOUT + 120, _int("AGENTBUS_LEASE_SECONDS", 1200))
# Adaptive stuck recovery (reclaim): base * complexity/worker factors, capped
STUCK_BASE_SEC = max(60, _int("AGENTBUS_STUCK_BASE_SEC", 300))
STUCK_TIMEOUT_MAX = max(STUCK_BASE_SEC, _int("AGENTBUS_STUCK_TIMEOUT_MAX", 1800))
MAX_ATTEMPTS = max(1, _int("AGENTBUS_MAX_ATTEMPTS", 3))
VERIFY_FAIL_MAX = max(1, _int("AGENTBUS_VERIFY_FAIL_MAX", 3))
# park | stash | branch | allow — dirty worktree before worker
DIRTY_GIT_POLICY = (os.getenv("AGENTBUS_DIRTY_GIT", "park") or "park").strip().lower()
VERIFY_TIMEOUT = max(30, _int("AGENTBUS_VERIFY_TIMEOUT", 300))
# Phone file-bus (gpt/grok/gemini Dropbox folders) retired (TD-021).
# Intake is desktop chat / IDE / Protocol only. Empty default = do not poll
# legacy channel dirs. Opt-in: AGENTBUS_CHANNELS=gpt,grok (unsupported path).
CHANNELS = tuple(
    x.strip()
    for x in os.getenv("AGENTBUS_CHANNELS", "").split(",")
    if x.strip()
)
DEFAULT_CHANNEL = os.getenv("AGENTBUS_DEFAULT_CHANNEL", "desktop").strip() or "desktop"


# --- Meta classifier & decompose (direct Ollama HTTP) ---
# AGENTBUS_META=1 enables. Pre-claim: skills/meta_classifier.py
# Post-failure decomposition: skills/meta_decompose.py
# Default model: qwen2.5:1.5b-instruct
WORKERS_FILE = _resolve_config_file("AGENTBUS_WORKERS_FILE", "workers.yaml")
PROVIDERS_FILE = _resolve_config_file("AGENTBUS_PROVIDERS_FILE", "providers.yaml")
PROVIDERS_STATE_FILE = (
    os.getenv("AGENTBUS_PROVIDERS_STATE", "").strip()
    or str(BUS_ROOT / "providers_state.json")
)
ALLOW_PAID = os.getenv("AGENTBUS_ALLOW_PAID", "").strip().lower() in {"1", "true", "yes", "on"}


def _tool_path(env_name: str, exe_names: tuple[str, ...], prefs: tuple[str, ...] = ()) -> str:
    v = os.getenv(env_name, "").strip()
    if v:
        return v
    from shutil import which
    for name in exe_names:
        hit = which(name)
        if hit:
            return hit
    for p in prefs:
        if p and Path(p).is_file():
            return p
    return ""


AIDER_PATH = _tool_path("AIDER_PATH", ("aider.exe", "aider"))
OPENCODE_PATH = _tool_path("OPENCODE_PATH", ("opencode.exe", "opencode"))
AIDER_PYTHON = _tool_path("AIDER_PYTHON", ("python.exe", "python")) or sys.executable
OLLAMA_PATH = _tool_path("OLLAMA_PATH", ("ollama.exe", "ollama"))
AIDER_MODEL = os.getenv("AIDER_MODEL", "ollama_chat/qwen2.5-coder:7b")
OPENCODE_ENABLED = os.getenv("OPENCODE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
OPENCODE_TIMEOUT = max(60, _int("OPENCODE_TIMEOUT", 600))
VERIFY_PYTHON_FALLBACK = os.getenv("AGENTBUS_PYTHON_FALLBACK", "").strip()

HEALTH_BASE_COOLDOWN = max(10, _int("AGENTBUS_HEALTH_BASE_COOLDOWN", 120))
HEALTH_CIRCUIT_LIMIT = max(1, _int("AGENTBUS_HEALTH_CIRCUIT_LIMIT", 3))
HEALTH_SUCCESS_WEIGHT = _float("AGENTBUS_HEALTH_SUCCESS_WEIGHT", 1.0)
HEALTH_SPEED_WEIGHT = _float("AGENTBUS_HEALTH_SPEED_WEIGHT", 1.0)
HEALTH_AVAIL_WEIGHT = _float("AGENTBUS_HEALTH_AVAIL_WEIGHT", 2.0)
HEALTH_FIT_WEIGHT = _float("AGENTBUS_HEALTH_FIT_WEIGHT", 1.0)
HEALTH_FAIL_PENALTY = _float("AGENTBUS_HEALTH_FAIL_PENALTY", 0.5)
WORKERS_STATE_FILE = (
    os.getenv("AGENTBUS_WORKERS_STATE", "").strip() or str(BUS_ROOT / "workers_state.json")
)

RANKER_FEEDBACK = _flag("AGENTBUS_RANKER_FEEDBACK", True)
RANKER_BIAS_LIMIT = max(0.0, _float("AGENTBUS_RANKER_BIAS_LIMIT", 0.5))
RANKER_STATE_FILE = (
    os.getenv("AGENTBUS_RANKER_STATE", "").strip() or str(BUS_ROOT / "ranker_state.json")
)

USE_DYNAMIC = _flag("AGENTBUS_USE_DYNAMIC", False)

RETRY_DELAY_SECONDS = max(5, _int("AGENTBUS_RETRY_DELAY_SECONDS", 60))
FALLBACK_MIN_DELAY = max(0, _int("AGENTBUS_FALLBACK_MIN_DELAY", 20))
COMPLEXITY_LOCAL_MAX = _int("AGENTBUS_COMPLEXITY_LOCAL_MAX", 2)

# По умолчанию git-commit выключен: пользователь коммитит сам после возврата.
# AGENTBUS_GIT=1 — включить автокоммиты диспетчера.
GIT_ENABLED = _flag("AGENTBUS_GIT", False)
REPAIR_ENABLED = _flag("AGENTBUS_REPAIR", True)
REPAIR_MAX_ATTEMPTS = _int("AGENTBUS_REPAIR_MAX_ATTEMPTS", 3)
GIT_IGNORE_EXTRA = tuple(
    x.strip()
    for x in os.getenv(
        "AGENTBUS_GIT_IGNORE",
        "__pycache__/,*.pyc,.pytest_cache/,*.aider*,*.aider.*-a,*.orig,*.rej,.tmp/,*.tmp",
    ).split(",")
    if x.strip()
)

AUTOPILOT = os.getenv("AGENTBUS_AUTOPILOT", "off").strip().lower()
NIGHT_START = os.getenv("AGENTBUS_NIGHT_START", "02:00")
NIGHT_END = os.getenv("AGENTBUS_NIGHT_END", "02:00")
REPORT_DIR = CHANNELS_ROOT / DEFAULT_CHANNEL / "logs"
LOG_ROOT = REPORT_DIR
