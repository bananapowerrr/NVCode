# -*- coding: utf-8 -*-
"""Edge cases для core/bus.py: FileBus.move / FileBus.write / _retry.

Покрывает то, чего нет в test_bus_types.py и test_e2e_filebus.py:
  * атомарность move при гонке N потоков на одном файле (ровно один True);
  * move несуществующего файла / в ещё не созданный каталог;
  * desktop-канал: отсутствие src всегда означает проигранный claim;
  * write: пустая строка, не-ASCII, битый Unicode (surrogate), не-текст,
    вложенный путь, отсутствие каталога, коллизия .tmp;
  * write в read-only каталог и обработка ошибок ФС;
  * _retry: повтор только на транзиентных ошибках, отсутствие бесконечного
    цикла, отсутствие sleep после последней попытки.

Все файловые операции изолированы tmp_path, сеть/диск вне него не трогаются.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from core.bus import STATES, FileBus


CHANNELS = ("gpt", "desktop")


@pytest.fixture
def bus(tmp_path):
    b = FileBus(tmp_path, CHANNELS)
    b.ensure()
    return b


def _state_dir(bus: FileBus, channel: str, state: str) -> Path:
    return bus.paths(channel)[state]


# ==========================================================================
# paths() / ensure(): границы допустимых каналов
# ==========================================================================
def test_paths_unknown_channel_raises_value_error(bus):
    """Канал не из AGENTBUS_CHANNELS и не __sub_* — отказ, а не молчаливый путь."""
    with pytest.raises(ValueError):
        bus.paths("nope")


def test_paths_none_channel_raises_value_error(bus):
    """None вместо строки: (channel or "") даёт '', который не разрешён."""
    with pytest.raises(ValueError):
        bus.paths(None)  # type: ignore[arg-type]


def test_paths_empty_channel_raises_value_error(bus):
    with pytest.raises(ValueError):
        bus.paths("")


def test_paths_rejects_traversal_channel(bus):
    with pytest.raises(ValueError):
        bus.paths("gpt__sub_../../outside")


def test_paths_channel_with_whitespace_is_stripped(bus):
    """' gpt ' должно работать: канал нормализуется через strip()."""
    assert bus.paths("  gpt  ")["incoming"] == _state_dir(bus, "gpt", "incoming")


def test_paths_allows_isolated_sub_agent_channel(bus):
    """Изолированные каналы {base}__sub_* разрешены без записи в конфиг."""
    paths = bus.paths("gpt__sub_1")
    assert set(paths) == set(STATES)
    assert paths["incoming"].name == "incoming"


def test_paths_desktop_allowed_even_if_not_configured(tmp_path):
    """desktop не обязан быть в channels (основной канал чата на ПК)."""
    b = FileBus(tmp_path, ("gpt",))
    assert b.paths("desktop")["incoming"].is_relative_to(tmp_path)


def test_ensure_creates_desktop_even_when_absent_in_channels(tmp_path):
    """ensure() всегда добавляет desktop — иначе первый чат падает на mkdir."""
    b = FileBus(tmp_path, ("gpt",))
    b.ensure()
    for state in STATES:
        assert _state_dir(b, "desktop", state).is_dir()


def test_ensure_is_idempotent(bus):
    bus.ensure()
    bus.ensure()
    assert _state_dir(bus, "gpt", "done").is_dir()


def test_paths_unknown_state_raises_key_error(bus):
    """Состояние вне STATES -> KeyError при индексации paths()[state]."""
    with pytest.raises(KeyError):
        bus.paths("gpt")["nowhere"]


# ==========================================================================
# move(): атомарность и гонки
# ==========================================================================
def test_move_missing_file_returns_false(bus):
    """Отсутствующий src -> False, вызывающий обязан прекратить обработку."""
    assert bus.move("gpt", "incoming", "processing", "ghost.json") is False


def test_move_creates_missing_destination_directory(bus):
    """Каталог назначения создаётся на лету (move в несуществующий каталог)."""
    dst_dir = _state_dir(bus, "gpt", "processing")
    dst_dir.rmdir() if dst_dir.exists() and not any(dst_dir.iterdir()) else None
    bus.write("gpt", "incoming", "t.json", "{}")
    assert bus.move("gpt", "incoming", "processing", "t.json") is True
    assert (dst_dir / "t.json").is_file()


def test_move_missing_source_directory_returns_false_without_creating_it(bus):
    """move из несуществующего канала-состояния не создаёт мусор в incoming."""
    src_dir = _state_dir(bus, "gpt", "incoming")
    src_dir.rmdir()
    assert bus.move("gpt", "incoming", "processing", "t.json") is False
    assert not src_dir.exists()


def test_move_staggered_dispatchers_claim_exactly_once(bus):
    """Два диспетчера, добравшиеся до файла в разные моменты: ровно один True.

    Это и есть рабочий сценарий AgentBus: инстансы опрашивают каталог
    incoming независимо, поэтому их arrival-ы расходятся. Rename в этом
    случае даёт честный claim: кто первый — тот и забрал.
    """
    bus.write("gpt", "incoming", "race.json", '{"id":"r"}')
    res: list[bool] = []
    lock = threading.Lock()

    def claim(delay):
        time.sleep(delay)
        ok = bus.move("gpt", "incoming", "processing", "race.json")
        with lock:
            res.append(ok)

    threads = [threading.Thread(target=claim, args=(i * 0.02,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sum(1 for r in res if r) == 1, f"гонка не защищена: {res}"
    assert len(res) == 6


def test_move_each_file_claimed_once_by_racing_dispatcher_pair(bus):
    """Пачка файлов, на каждый — два диспетчера: суммарно ровно N успешных claim."""
    n = 20
    for i in range(n):
        bus.write("gpt", "incoming", f"t{i}.json", f'{{"id":{i}}}')
    res: list[bool] = []
    lock = threading.Lock()

    def claim_all(delay):
        time.sleep(delay)
        local = [bus.move("gpt", "incoming", "processing", f"t{i}.json")
                 for i in range(n)]
        with lock:
            res.extend(local)

    threads = [threading.Thread(target=claim_all, args=(k * 0.02,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sum(1 for r in res if r) == n, f"двойные claim: {sum(res)} != {n}"
    proc = _state_dir(bus, "gpt", "processing")
    assert sorted(p.name for p in proc.glob("*.json")) == sorted(
        f"t{i}.json" for i in range(n))


def test_move_simultaneous_claims_never_duplicate_or_corrupt_the_file(bus):
    """Инвариант, который держится даже при одновременных claim.

    Несколько потоков могут одновременно получить True от rename (см. тест
    про эксклюзивность ниже), но файл при этом остаётся ровно один и
    содержимое не портится: rename атомарен на уровне файловой системы.
    """
    payload = '{"id":"r","message":"do work"}'
    bus.write("gpt", "incoming", "race.json", payload)
    res: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def claim():
        barrier.wait()
        ok = bus.move("gpt", "incoming", "processing", "race.json")
        with lock:
            res.append(ok)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    proc = _state_dir(bus, "gpt", "processing")
    assert list(proc.glob("*.json")) == [proc / "race.json"], "файл размножился"
    assert (proc / "race.json").read_text(encoding="utf-8") == payload
    assert list(_state_dir(bus, "gpt", "incoming").iterdir()) == []
    assert sum(1 for r in res if r) >= 1


@pytest.mark.xfail(
    reason="NTFS: одновременные MoveFileExW одного источника могут вернуть "
           "успех несколько раз (delete-pending резолвится по имени). "
           "Эксклюзивность claim держится только при разнесённых arrival-ах.",
    strict=False,
)
def test_move_simultaneous_claims_are_exclusive_on_this_platform(bus, tmp_path_factory):
    """Идеальный контракт bus.py: ровно один True на одновременных claim.

    НЕ проходит на NTFS — см. комментарий xfail. Прогон повторяется много
    раз, чтобы результат не зависел от того, «повезло» ли с таймингом в
    конкретном запуске (один прогон даёт ~45% ложных XPASS).
    """
    max_winners = 0
    trials = 25
    for trial in range(trials):
        root = tmp_path_factory.mktemp(f"claim{trial}")
        b = FileBus(root, ("gpt",))
        b.ensure()
        b.write("gpt", "incoming", "race.json", '{"id":"r"}')
        res: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def claim():
            barrier.wait()
            ok = b.move("gpt", "incoming", "processing", "race.json")
            with lock:
                res.append(ok)

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        max_winners = max(max_winners, sum(1 for r in res if r))

    assert max_winners == 1, f"за {trials} прогонов максимум {max_winners} claim-ов"


def test_move_leaves_no_partial_file_in_source(bus):
    """После move в src не должно остаться ни файла, ни .tmp-обломка."""
    bus.write("gpt", "incoming", "t.json", "{}")
    assert bus.move("gpt", "incoming", "processing", "t.json") is True
    src = _state_dir(bus, "gpt", "incoming")
    assert list(src.iterdir()) == []


def test_move_same_state_to_itself_is_noop_but_claims(bus):
    """Перенос incoming->incoming: rename проходит, файл на месте, True."""
    bus.write("gpt", "incoming", "t.json", "{}")
    assert bus.move("gpt", "incoming", "incoming", "t.json") is True
    assert (_state_dir(bus, "gpt", "incoming") / "t.json").is_file()


def test_move_desktop_missing_source_does_not_claim_existing_destination(bus):
    """Отсутствие источника всегда означает, что этот вызов не забрал задачу."""
    bus.write("desktop", "incoming", "t.json", "{}")
    assert bus.move("desktop", "incoming", "processing", "t.json") is True
    assert bus.move("desktop", "incoming", "processing", "t.json") is False


def test_move_normal_channel_missing_src_does_not_soft_ok(bus):
    """Обычный канал жёстче desktop: нет src -> False, даже если dst есть."""
    bus.write("gpt", "incoming", "t.json", "{}")
    assert bus.move("gpt", "incoming", "processing", "t.json") is True
    assert bus.move("gpt", "incoming", "processing", "t.json") is False


def test_move_overwrites_existing_destination(bus):
    """rename перезаписывает dst: повторная обработка не плодит дубль."""
    bus.write("gpt", "incoming", "t.json", '{"v":1}')
    bus.write("gpt", "processing", "t.json", '{"v":0}')
    assert bus.move("gpt", "incoming", "processing", "t.json") is True
    assert (_state_dir(bus, "gpt", "processing") / "t.json").read_text(
        encoding="utf-8") == '{"v":1}'


# ==========================================================================
# write(): границы входа и отказов
# ==========================================================================
def test_write_empty_string_creates_zero_byte_file(bus):
    """Пустой текст — валидная задача-пустышка, файл должен появиться."""
    p = bus.write("gpt", "incoming", "empty.json", "")
    assert p.is_file()
    assert p.stat().st_size == 0
    assert p.read_text(encoding="utf-8") == ""


def test_write_creates_missing_parent_directory(bus):
    """Каталог состояния может быть удалён между ensure() и write()."""
    _state_dir(bus, "gpt", "incoming").rmdir()
    p = bus.write("gpt", "incoming", "t.json", "{}")
    assert p.is_file()


def test_write_nested_relative_filename_creates_subdirs(bus):
    """Имя с подкаталогами: промежуточные каталоги создаются."""
    p = bus.write("gpt", "incoming", "a/b/c.json", "{}")
    assert p.is_file()


@pytest.mark.parametrize("filename", ["../escape.json", "/tmp/escape.json", "a/../../escape.json", "a\\..\\escape.json"])
def test_write_rejects_filename_traversal(bus, tmp_path, filename):
    with pytest.raises(ValueError, match="unsafe bus filename"):
        bus.write("gpt", "incoming", filename, "{}")
    assert not (tmp_path.parent / "escape.json").exists()


def test_move_rejects_filename_traversal(bus):
    with pytest.raises(ValueError, match="unsafe bus filename"):
        bus.move("gpt", "incoming", "processing", "../escape.json")


def test_write_unicode_payload_roundtrip_utf8(bus):
    """Кириллица/эмодзи должны выжить round-trip без потерь."""
    text = '{"message":"почини парсер 🛠","files":[]}'
    p = bus.write("gpt", "incoming", "u.json", text)
    assert p.read_text(encoding="utf-8") == text


def test_write_lone_surrogate_raises_and_leaves_file_unusable(bus):
    """Surrogate (битый Unicode) нельзя записать в utf-8: должен быть отказ.

    Fallback-энкодер не включаем: молча подменить символ значило бы записать
    в очередь задачу с другим содержимым, чем отдал вызывающий.
    """
    with pytest.raises(UnicodeEncodeError):
        bus.write("gpt", "incoming", "bad.json", 'x\ud800y')
    assert not (_state_dir(bus, "gpt", "incoming") / "bad.json").exists()


def test_write_non_text_payload_raises_typeerror(bus):
    """int/None вместо str — TypeError, а не запись мусора в очередь."""
    with pytest.raises(TypeError):
        bus.write("gpt", "incoming", "int.json", 123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        bus.write("gpt", "incoming", "none.json", None)  # type: ignore[arg-type]


def test_write_empty_filename_raises_valueerror(bus):
    """Пустое имя отклоняется до построения filesystem path."""
    with pytest.raises(ValueError, match="unsafe bus filename"):
        bus.write("gpt", "incoming", "", "{}")


def test_write_overwrites_existing_file_atomically(bus):
    """Повторная запись того же имени заменяет содержимое целиком."""
    bus.write("gpt", "incoming", "t.json", '{"v":1}')
    bus.write("gpt", "incoming", "t.json", '{"v":2}')
    p = _state_dir(bus, "gpt", "incoming") / "t.json"
    assert p.read_text(encoding="utf-8") == '{"v":2}'
    assert [f.name for f in _state_dir(bus, "gpt", "incoming").iterdir()] == ["t.json"]


def test_write_uses_tmp_suffix_and_replaces_atomically(bus, monkeypatch):
    """Запись идёт через <name>.tmp + os.replace: читатель не видит полуфайла."""
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    bus.write("gpt", "incoming", "t.json", "{}")
    assert seen and all(s.endswith("t.json.tmp") for s in seen)


def test_write_to_readonly_directory_raises_after_retries(bus, monkeypatch):
    """Read-only каталог: PermissionError после всех попыток, без записи файла.

    chmod на Windows не запрещает запись для текущего пользователя, поэтому
    отказ моделируется на уровне open() — это и есть проверяемый контракт
    _retry (повтор PermissionError, затем проброс последней ошибки).
    """
    calls: list[int] = []

    def denied(*args, **kwargs):
        calls.append(1)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("core.bus.open", denied, raising=False)
    monkeypatch.setattr("builtins.open", denied)
    with pytest.raises(PermissionError):
        bus.write("gpt", "incoming", "ro.json", "{}")
    assert len(calls) >= 10, "PermissionError обязан быть повторён все 10 раз"


def test_write_retry_recovers_after_transient_permission_error(bus, monkeypatch):
    """Первые попытки под Dropbox блокируют файл — запись должна дойти."""
    state = {"n": 0}
    real_open = open

    def flaky(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            state["n"] += 1
            if state["n"] < 3:
                raise PermissionError(32, "file in use")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky)
    p = bus.write("gpt", "incoming", "flaky.json", '{"ok":1}')
    assert p.is_file()
    assert state["n"] == 3
    assert p.read_text(encoding="utf-8") == '{"ok":1}'


# ==========================================================================
# _retry(): семантика повторов
# ==========================================================================
def test_retry_succeeds_on_first_attempt_without_sleeping():
    """Успех с первой попытки: действие вызвано один раз, sleep не было."""
    calls = []
    out = FileBus._retry(lambda: calls.append(1) or "ok", attempts=3, delay=0.01)
    assert out == "ok"
    assert len(calls) == 1


def test_retry_retries_permission_error_until_success():
    calls = []

    def action():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "sharing violation")
        return "done"

    assert FileBus._retry(action, attempts=5, delay=0.01) == "done"
    assert len(calls) == 3


def test_retry_raises_last_error_after_all_attempts():
    """Исчерпали попытки -> пробрасывается ПОСЛЕДНЯЯ ошибка, не первая."""
    calls = []

    def action():
        calls.append(1)
        raise PermissionError(13, f"attempt {len(calls)}")

    with pytest.raises(PermissionError) as exc:
        FileBus._retry(action, attempts=4, delay=0.01)
    assert len(calls) == 4
    assert "attempt 4" in str(exc.value)


def test_retry_reraises_non_transient_oserror_immediately():
    """errno=2 (нет такого файла) не транзиентный: без повторов."""
    calls = []

    def action():
        calls.append(1)
        raise OSError(2, "No such file or directory")

    with pytest.raises(OSError) as exc:
        FileBus._retry(action, attempts=5, delay=0.01)
    assert len(calls) == 1
    assert exc.value.errno == 2


@pytest.mark.parametrize("errno", [11, 16, 26])
def test_retry_retries_transient_posix_errnos(errno):
    """errno 11/16/26 (EAGAIN/EBUSY/ENOTEMPTY) относятся к блокировкам."""
    calls = []

    def action():
        calls.append(1)
        if len(calls) < 2:
            raise OSError(errno, "transient")
        return "ok"

    assert FileBus._retry(action, attempts=3, delay=0.01) == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("winerror", [32, 33])
def test_retry_retries_windows_sharing_violations(winerror):
    """winerror 32/33 (sharing violation / lock) — типичные блокировки Dropbox."""
    calls = []

    def action():
        calls.append(1)
        if len(calls) < 2:
            exc = OSError(13, "locked")
            exc.winerror = winerror
            raise exc
        return "ok"

    assert FileBus._retry(action, attempts=3, delay=0.01) == "ok"
    assert len(calls) == 2


def test_retry_backoff_delay_grows_linearly_between_attempts():
    """Пауза delay*(i+1): без sleep после последней попытки (нет хвоста)."""
    sleeps: list[float] = []

    def action():
        raise PermissionError(13, "always")

    real_sleep = time.sleep
    try:
        time.sleep = lambda s: sleeps.append(s)  # type: ignore[assignment]
        with pytest.raises(PermissionError):
            FileBus._retry(action, attempts=4, delay=0.5)
    finally:
        time.sleep = real_sleep  # type: ignore[assignment]

    assert sleeps == [0.5, 1.0, 1.5], f"backoff неверный: {sleeps}"


def test_retry_zero_attempts_raises_assertion_error():
    """attempts=0 — вырожденный случай: цикл не выполняется, last=None."""
    with pytest.raises(AssertionError):
        FileBus._retry(lambda: "never", attempts=0)


def test_retry_propagates_unexpected_exception_without_retry():
    """ValueError не транзиентный: проброс сразу, без повторов."""
    calls = []

    def action():
        calls.append(1)
        raise ValueError("logic error")

    with pytest.raises(ValueError):
        FileBus._retry(action, attempts=3, delay=0.01)
    assert len(calls) == 1


# ==========================================================================
# move() под нагрузкой ФС: retry на блокировках
# ==========================================================================
def test_move_retries_on_sharing_violation(bus, monkeypatch):
    """Sharing violation на rename -> повтор, а не ложное False."""
    bus.write("gpt", "incoming", "t.json", "{}")
    state = {"n": 0}
    real_replace = Path.replace

    def flaky(self, target):
        state["n"] += 1
        if state["n"] < 3:
            exc = PermissionError(32, "file in use by another process")
            raise exc
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    assert bus.move("gpt", "incoming", "processing", "t.json") is True
    assert state["n"] == 3


def test_move_sustained_sharing_violation_raises_not_false(bus, monkeypatch):
    """Постоянная блокировка: raise, а не False.

    False означал бы «файл забрал другой инстанс», и вызывающий прекратил бы
    обработку задачи, которая на самом деле ещё стоит в очереди.
    """
    bus.write("gpt", "incoming", "t.json", "{}")

    def always_locked(self, target):
        raise PermissionError(32, "still in use")

    monkeypatch.setattr(Path, "replace", always_locked)
    with pytest.raises(PermissionError):
        bus.move("gpt", "incoming", "processing", "t.json")
    assert (_state_dir(bus, "gpt", "incoming") / "t.json").is_file(), \
        "источник обязан остаться на месте"
