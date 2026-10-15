# NVCode

Локальный AI-помощник для кода (Ollama / LM Studio). Без обязательной облачной подписки.

**Главный канал — чат на ПК.** Очередь `channels/` для телефона — опционально (`phone_filebus`).

## Быстрый старт

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-ui.txt   # GUI
# или: python scripts/bootstrap.py

python dispatcher.py --init
python dispatcher.py --doctor
bash scripts/ci_smoke.sh          # offline CI (mock + tests)

python dispatcher.py          # движок (терминал 1)
python dispatcher_ui.py       # чат (терминал 2)
```

Windows: `scripts/start_ui.bat`, `scripts/start_doctor.bat`  
Модель: `ollama pull qwen2.5-coder:7b`

## CLI

| | |
|--|--|
| `--init` | Ollama / LM Studio, policy |
| `--doctor` | диагностика |
| `--dashboard` | http://127.0.0.1:8337 |
| `--recipe NAME --target PATH` | рецепт |
| `--version` | версия |

## Поток

```
Чат → .agentbus/desktop_queue/ → Runtime → channels/desktop/done → чат
```

Политика: `config/policy.yaml` → `local_only` | `balanced` | `quality` | `cheap`

## Документация

См. **[docs/INDEX.md](docs/INDEX.md)** — онбординг, структура, каналы, API, флаги, UI, troubleshooting.

Пошагово до live: **[docs/LAUNCH.md](docs/LAUNCH.md)**.

## Корень проекта

Только точки входа и метаданные пакета. Код — в `src/`, подробности — в `docs/STRUCTURE.md`.

## Windows package (опционально)

```bash
pip install pyinstaller
python scripts/build_exe.py
# dist/AgentBusUI.exe + dist/AgentBus.exe
```

Или без сборки: `scripts/start_all.bat` (dispatcher + UI).

## Контракты ядра (кратко)

**Очередь:** чат → `.agentbus/desktop_queue/` → Runtime → `channels/desktop/{done,errors}`.

**Состояния задачи:**
```
PENDING → CLAIMED → PROCESSING → VERIFYING → DONE
              ↘ ERROR / DEFERRED → CLAIMED (retry)
DONE = terminal
```

**DONE** только после успешного verify (исключения: cache hit / skill success).
Пустой pytest (`collected 0`) → **false_DONE**, не успех.

**Task Contract:** `core.task_contract` валидирует JSON до runtime.

**VerificationEngine:** единый отчёт checks → `gate_done`.

**Идентичность задачи:** `core.dedupe.task_fingerprint` (без id/attempts/worker).

**Пресет старта:** `AGENTBUS_FEATURE_PRESET=beginner_ru` (local-first, без phone_filebus).

**Аудит перед релизом:**
```bash
python scripts/project_audit.py
python scripts/project_audit.py --pytest
```

### Composer

`Ctrl+K` → **Composer — новая задача** — явная форма (очередь, не прямой worker).
