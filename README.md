# NVCode

**NVCode** — локальная AI-среда разработки: desktop-чат, coding workers, инструменты проекта и проверка результатов. Локальные провайдеры Ollama / LM Studio поддерживаются; облачные провайдеры могут подключаться отдельно.

## Быстрый старт

Требуется Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-ui.txt   # desktop UI
# альтернативно: python scripts/bootstrap.py

python dispatcher.py --init
python dispatcher.py --doctor
bash scripts/ci_smoke.sh
```

Запуск приложения в двух терминалах:

```bash
python dispatcher.py
python dispatcher_ui.py
```

Windows: `scripts/start_all.bat`, `scripts/start_ui.bat`, `scripts/start_doctor.bat`.

Для локального coding worker на Ollama можно начать с модели `qwen2.5-coder:7b`:

```bash
ollama pull qwen2.5-coder:7b
```

## CLI

| Команда | Назначение |
|---|---|
| `python dispatcher.py --init` | Инициализация окружения |
| `python dispatcher.py --doctor` | Диагностика |
| `python dispatcher.py --dashboard` | Локальная панель состояния |
| `python dispatcher.py --recipe NAME --target PATH` | Запуск рецепта |
| `python dispatcher.py --version` | Версия |

## Как устроен рабочий цикл

```
Запрос → очередь задач → runtime / worker → проверки → результат и diff
```

Главный принцип: **worker предлагает изменения, runtime управляет выполнением, verification подтверждает результат**. Успешное завершение отдельной задачи не означает автоматически, что весь проект готов к релизу.

## Документация

Начните с **[документационного индекса](docs/INDEX.md)**. Там находятся инструкции по установке, архитектуре, проверках, релизу и текущему техническому долгу.

- [Getting started](docs/GETTING_STARTED.md)
- [Структура репозитория](docs/STRUCTURE.md)
- [Контракты](docs/CONTRACTS.md)
- [Live runbook](docs/LIVE_RUNBOOK.md)
- [Реестр технического долга](docs/TECHNICAL_DEBT_REGISTRY.md)

## Разработка и проверка

```bash
python scripts/project_audit.py
python -m pytest -q
```

Для проверки перед релизом используйте runbook и CI-конфигурацию репозитория. Если проверка не запускалась в текущей среде, не считайте её пройденной.

## Название и совместимость

Текущее название продукта — **NVCode**. Название AgentBus осталось в некоторых внутренних идентификаторах, переменных окружения и путях данных; их следует мигрировать отдельно с проверкой совместимости, а не массовой заменой текста.
