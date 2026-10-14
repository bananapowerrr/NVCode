# -*- coding: utf-8 -*-
"""Task Contract — normalize + validate task JSON before runtime.

Official shape (minimal required fields):

{
  "id": str,
  "project": str,
  "message": str,
  "files": [str],
  "channel": str,
  "status": PENDING|CLAIMED|PROCESSING|VERIFYING|DONE|ERROR|DEFERRED,
  "attempts": int,
  "worker": str,
  "verify": [str],
  "run": [str],
  "executor": str,
  "metadata": { ... },
  "verification": { optional structured verify prefs }
}

Does not depend on jsonschema — pure Python, offline-safe.
"""
from __future__ import annotations

from typing import Any
import re

from core.tasks import STATES

REQUIRED_KEYS = ("id", "message")
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OPTIONAL_DEFAULTS: dict[str, Any] = {
    "project": "",
    "files": [],
    "channel": "desktop",
    "status": "PENDING",
    "attempts": 0,
    "worker": "",
    "verify": [],
    "run": [],
    "executor": "",
    "metadata": {},
    "verification": {},
}

# Volatile keys allowed but stripped from "identity" elsewhere
KNOWN_TOP = frozenset(
    list(REQUIRED_KEYS)
    + list(OPTIONAL_DEFAULTS)
    + ["id", "status", "attempts", "worker", "metadata", "verification"]
)


class TaskContractError(ValueError):
    """Invalid task payload."""


def _as_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        raise TaskContractError(f"{field} must be a list of strings")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, (str, int, float)):
            raise TaskContractError(f"{field}[{i}] must be string-like")
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def _as_str(value: Any, field: str, *, allow_empty: bool = True) -> str:
    if value is None:
        s = ""
    elif not isinstance(value, (str, int, float)):
        raise TaskContractError(f"{field} must be a string")
    else:
        s = str(value).strip()
    if not allow_empty and not s:
        raise TaskContractError(f"{field} must be non-empty")
    return s


def _as_int(value: Any, field: str, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise TaskContractError(f"{field} must be int") from e


def normalize_task(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return a clean task dict; raises TaskContractError if unusable."""
    if not isinstance(raw, dict):
        raise TaskContractError("task must be a JSON object")

    tid = _as_str(raw.get("id"), "id", allow_empty=False)
    if not _SAFE_TASK_ID.fullmatch(tid):
        raise TaskContractError("id must be a safe filename token (letters, digits, dot, underscore, hyphen; max 128)")
    message = _as_str(raw.get("message"), "message", allow_empty=False)
    if len(message) > 100_000:
        raise TaskContractError("message too long (>100k)")

    status = _as_str(raw.get("status") or "PENDING", "status").upper() or "PENDING"
    if status not in STATES:
        raise TaskContractError(f"status must be one of {sorted(STATES)}, got {status!r}")

    files = _as_str_list(raw.get("files"), "files")
    verify = _as_str_list(raw.get("verify"), "verify")
    run = _as_str_list(raw.get("run"), "run")

    meta = raw.get("metadata")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise TaskContractError("metadata must be an object")

    verification = raw.get("verification")
    if verification is None:
        verification = {}
    if not isinstance(verification, dict):
        raise TaskContractError("verification must be an object")

    # Optional nested verification flags
    for key in ("syntax", "tests", "lint", "typecheck", "diff", "security"):
        if key in verification and not isinstance(verification[key], (bool, str, int)):
            raise TaskContractError(f"verification.{key} invalid type")

    # Fail-closed security intake (path traversal / absolute paths / dangerous cmds)
    try:
        from safety.security import SecurityError, validate_commands, validate_message, validate_paths

        validate_message(message)
        files = validate_paths(files)
        verify = validate_commands(verify, field="verify")
        run = validate_commands(run, field="run")
    except ImportError:
        pass  # offline/tests without safety package
    except Exception as sec_err:
        # Re-raise as TaskContractError so callers share one error type
        name = type(sec_err).__name__
        if name == "SecurityError" or "security" in name.lower():
            raise TaskContractError(str(sec_err)) from sec_err
        raise TaskContractError(f"security check failed: {sec_err}") from sec_err

    out: dict[str, Any] = {
        "id": tid,
        "project": _as_str(raw.get("project"), "project"),
        "message": message,
        "files": files,
        "channel": _as_str(raw.get("channel") or "desktop", "channel") or "desktop",
        "status": status,
        "attempts": max(0, _as_int(raw.get("attempts"), "attempts", 0)),
        "worker": _as_str(raw.get("worker"), "worker"),
        "verify": verify,
        "run": run,
        "executor": _as_str(raw.get("executor"), "executor"),
        "metadata": dict(meta),
        "verification": dict(verification),
    }
    return out


def validate_task(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Alias: normalize + validate. Same return."""
    return normalize_task(raw)


def is_valid_task(raw: dict[str, Any] | None) -> bool:
    try:
        normalize_task(raw)
        return True
    except TaskContractError:
        return False
