# -*- coding: utf-8 -*-
"""Общая файловая шина AgentBus v2.

Состояния: incoming → processing → done | errors | deferred.
Запись выполняется через fsync + atomic replace; claim — через atomic rename.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

STATES: tuple[str, ...] = ("incoming", "processing", "done", "errors", "deferred", "logs")
_SAFE_CHANNEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_relative_filename(filename: str) -> Path:
    """Allow nested relative names without allowing filesystem escape."""
    value = str(filename or "").replace("\\", "/")
    if not value or "\x00" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("unsafe bus filename")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe bus filename")
    return Path(*parts)

T = TypeVar("T")


class FileBus:
    """Файловая очередь по каналам (gpt/grok/gemini/autopilot)."""

    def __init__(self, root: str | Path, channels: tuple[str, ...]) -> None:
        self.root = Path(root)
        self.channels = channels

    def paths(self, channel: str) -> dict[str, Path]:
        """Каталоги состояний для канала.

        Isolated sub-agent channels (`{base}__sub_*`) are allowed dynamically
        even if not listed in AGENTBUS_CHANNELS.
        """
        ch = (channel or "").strip()
        if not _SAFE_CHANNEL.fullmatch(ch):
            raise ValueError(f"Недопустимое имя канала: {channel}")
        # desktop = primary PC chat queue results (not phone file-bus)
        if ch not in self.channels and "__sub_" not in ch and ch != "desktop":
            raise ValueError(f"Неизвестный канал: {channel}")
        base = self.root / "channels" / ch
        return {state: base / state for state in STATES}

    def ensure(self) -> None:
        """Создать дерево каналов/состояний.

        Always includes ``desktop`` (primary PC chat), even if not in
        AGENTBUS_CHANNELS — otherwise first chat task races on mkdir.
        """
        channels = list(self.channels)
        if "desktop" not in channels:
            channels.append("desktop")
        for channel in channels:
            for path in self.paths(channel).values():
                path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _retry(action: Callable[[], T], attempts: int = 10, delay: float = 0.35) -> T:
        """Retry on transient file locks (Dropbox / Windows Sharing Violation)."""
        last: BaseException | None = None
        for i in range(attempts):
            try:
                return action()
            except PermissionError as exc:
                last = exc
            except OSError as exc:
                win = getattr(exc, "winerror", None)
                err = getattr(exc, "errno", None)
                if win not in (32, 33) and err not in (11, 16, 26):
                    raise
                last = exc
            if i + 1 >= attempts:
                assert last is not None
                raise last
            time.sleep(delay * (i + 1))
        assert last is not None
        raise last

    def write(self, channel: str, state: str, filename: str, text: str) -> Path:
        """Записать текст в channels/<channel>/<state>/<filename> с fsync."""
        path = self.paths(channel)[state] / _safe_relative_filename(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        def write_once() -> Path:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return path

        return self._retry(write_once)

    def move(self, channel: str, state_from: str, state_to: str, filename: str) -> bool:
        """Перенос файла между состояниями.

        Returns:
            True — переход сделал этот вызов;
            False — исходник уже отсутствует (гонка: забрал другой инстанс).
            При False вызывающий НЕ должен продолжать обработку.
        """
        safe_name = _safe_relative_filename(filename)
        src = self.paths(channel)[state_from] / safe_name
        dst = self.paths(channel)[state_to] / safe_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        def move_once() -> bool:
            # Атомарно: rename в пределах одной ФС. Раньше здесь был
            # copy2 + unlink, и два инстанса успевали оба скопировать файл
            # до того, как кто-то его удалил, — задача обрабатывалась дважды.
            try:
                src.replace(dst)
                return True
            except FileNotFoundError:
                # Destination existence proves only that another caller may
                # have won the rename; it is never evidence that this caller
                # owns the claim. Direct desktop processing seeds are handled
                # by Runtime.process and do not use this incoming claim path.
                return False
            # PermissionError и sharing violation НЕ глотаем: их ловит _retry.
            # Глотать нельзя — если rename не прошёл, перехода не было, и
            # True означал бы повторную обработку задачи.

        return self._retry(move_once)
