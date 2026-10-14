# -*- coding: utf-8 -*-
"""Path / command security validation for AgentBus tasks.

Блокирует path traversal, абсолютные пути, опасные shell-метасимволы
и типичные destructive patterns в verify/run.
"""
from __future__ import annotations
import re
from pathlib import Path, PureWindowsPath
from typing import Iterable

from core.errors import DispatcherError


class SecurityError(DispatcherError):
    """Задача отклонена по security policy."""

    def __init__(self, message: str = "", field: str = "", value: str = ""):
        super().__init__(
            message or "security policy violation",
            field=field,
            value=(value or "")[:200],
        )
        self.field = field
        self.value = value


# Shell / injection patterns (case-insensitive on command text)
_DANGEROUS_CMD = re.compile(
    r"(?:^|[;&|`$()\n])\s*"
    r"(?:"
    r"rm\s+(-[a-zA-Z]*f|/)|"
    r"del\s+/[sqf]|"
    r"format\s+[a-z]:|"
    r"mkfs\.|"
    r"dd\s+if=|"
    r"shutdown|reboot|poweroff|"
    r"curl\s+[^\n]*\|\s*(?:ba)?sh|"
    r"wget\s+[^\n]*\|\s*(?:ba)?sh|"
    r">\s*/dev/sd|"
    r":\(\)\s*\{\s*:\|:&\s*\};:"  # fork bomb
    r")",
    re.IGNORECASE,
)

_META_CHARS = re.compile(r"[;`|$]|\$\(|&&|\|\|")

# Allowlist-ish: relative path segments
_SAFE_REL = re.compile(r"^[A-Za-z0-9_./\\-]+$")

MAX_MESSAGE_LEN = 200_000
MAX_FILES = 200
MAX_COMMANDS = 50
MAX_COMMAND_LEN = 4000
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_path(relative: str, *, field: str = "files") -> None:
    if not relative or not str(relative).strip():
        raise SecurityError("пустой путь файла", field=field, value=str(relative))
    item = str(relative).strip()
    # Normalize Windows separators so ".." is detected on all platforms
    norm = item.replace(chr(92), "/")
    p = Path(norm)
    # On Windows Path("/abs").is_absolute() is False (rooted, no drive),
    # so also check the Windows parser and any leading separator.
    if (
        p.is_absolute()
        or PureWindowsPath(norm).is_absolute()
        or norm.startswith("/")
        or (len(norm) >= 2 and norm[1] == ":")
    ):
        raise SecurityError(f"абсолютный путь запрещён: {item}", field=field, value=item)
    parts = [x for x in norm.split("/") if x not in ("", ".")]
    if ".." in parts or ".." in p.parts:
        raise SecurityError(f"path traversal запрещён: {item}", field=field, value=item)
    # null bytes / control
    if "\x00" in item or any(ord(c) < 32 and c not in "\t" for c in item):
        raise SecurityError(f"недопустимые символы в пути: {item!r}", field=field, value=item)


def validate_paths(files: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for i, item in enumerate(files or []):
        if i >= MAX_FILES:
            raise SecurityError(f"слишком много files (>{MAX_FILES})", field="files")
        validate_path(item)
        out.append(str(item).strip())
    return out


def validate_task_id(task_id: object) -> str:
    """Validate an id before it is interpolated into a queue filename."""
    value = str(task_id or "").strip()
    if not _SAFE_TASK_ID.fullmatch(value):
        raise SecurityError(
            "task id must be a safe filename token",
            field="id",
            value=value,
        )
    return value


def validate_command(cmd: str, *, field: str = "verify") -> None:
    if not cmd or not str(cmd).strip():
        raise SecurityError("пустая команда", field=field, value=str(cmd))
    text = str(cmd).strip()
    if len(text) > MAX_COMMAND_LEN:
        raise SecurityError(
            f"команда слишком длинная (>{MAX_COMMAND_LEN})",
            field=field,
            value=text[:80],
        )
    if "\x00" in text:
        raise SecurityError("null byte в команде", field=field, value=text[:80])
    if _DANGEROUS_CMD.search(text):
        raise SecurityError(
            f"опасный паттерн в команде: {text[:120]}",
            field=field,
            value=text[:120],
        )
    # Soft: multiple chained shell operators without obvious test runner context
    if _META_CHARS.search(text) and not re.search(
        r"\b(pytest|python|py\.test|ruff|mypy|black|flake8|npm|node)\b", text, re.I
    ):
        # allow simple && in test pipelines with known tools only
        raise SecurityError(
            f"shell-метасимволы без известного test-runner: {text[:120]}",
            field=field,
            value=text[:120],
        )


def validate_commands(commands: Iterable[str] | None, *, field: str = "verify") -> list[str]:
    out: list[str] = []
    for i, cmd in enumerate(commands or []):
        if i >= MAX_COMMANDS:
            raise SecurityError(f"слишком много команд (>{MAX_COMMANDS})", field=field)
        validate_command(cmd, field=field)
        out.append(str(cmd).strip())
    return out


def validate_message(message: str | None) -> str:
    text = str(message or "")
    if len(text) > MAX_MESSAGE_LEN:
        raise SecurityError(
            f"message слишком длинный (>{MAX_MESSAGE_LEN})",
            field="message",
            value=text[:80],
        )
    if "\x00" in text:
        raise SecurityError("null byte в message", field="message")
    return text


def validate_task(raw: dict | None) -> dict:
    """Полная проверка payload задачи. Возвращает копию с нормализованными полями.

    Raises SecurityError on violation.
    """
    raw = dict(raw or {})
    if raw.get("id") is not None:
        raw["id"] = validate_task_id(raw["id"])
    validate_message(raw.get("message"))
    files = validate_paths(raw.get("files") or [])
    verify = validate_commands(raw.get("verify") or [], field="verify")
    run = validate_commands(raw.get("run") or [], field="run")
    raw["files"] = files
    raw["verify"] = verify
    raw["run"] = run
    return raw
