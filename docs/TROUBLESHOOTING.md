# Типовые проблемы

## Диспетчер «не берёт» задачи из чата

1. Запущен ли `python dispatcher.py`?
2. Есть ли файлы в `.agentbus/desktop_queue/`?
3. `python dispatcher.py --doctor` — смотри `desktop_queue size`.

## Нет ответа в чате

Результаты: `channels/desktop/done/` или `errors/`.  
Чат опрашивает эти папки каждые ~2.5 с.

## Ollama / локальная модель

```bash
ollama list
ollama pull qwen2.5-coder:7b
python dispatcher.py --init
```

Policy `local_only` требует живой local runtime.

## Облако 429 / no_key

Нормально при `local_only`. Иначе: ключи в `.env`, policy `balanced`.

## UI не стартует

```bash
pip install -r requirements-ui.txt
python dispatcher_ui.py
```

## Import errors

Запуск из корня NVCode, `PYTHONPATH=src:.` для pytest.


## Задача в errors: false_DONE

Проверка pytest ничего не собрала (`collected 0 items`).  
NVCode **не** считает это успехом при `AGENTBUS_STRICT_VERIFY=1` (по умолчанию).

- Добавьте тесты или сузьте `task.verify`
- Для тривиальных правок complexity 1 → достаточно L0/L1
