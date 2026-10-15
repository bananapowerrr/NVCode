# Live acceptance — короткий путь (после возвращения к ПК)

Цель: **не** прогнать всё. Доказать один цикл.

```
doctor → dispatcher + UI → 1 задача → diff/verify → DONE
```

Если шаг красный — **стоп**, чинить только его.

---

## 0. Подготовка (один раз)

```bash
cd NVCode
pip install -r requirements.txt
pip install -r requirements-ui.txt

ollama pull qwen2.5-coder:7b
# опционально meta:
ollama pull qwen2.5:1.5b-instruct

# .env: AGENTBUS_MAX_PARALLEL_PROJECTS=1
# local_only если без облака
```

Создай **отдельный** git-репозиторий-песочницу (не боевой dirty tree):

```bash
mkdir -p ~/nvcode_sandbox && cd ~/nvcode_sandbox
git init && echo "print('hi')" > app.py && git add . && git commit -m init
```

В UI выбери этот project.

---

## 1. Doctor

```bash
python dispatcher.py --doctor
# или: python -m core.doctor
```

Ожидание: **VERDICT: МОЖНО СТАРТОВАТЬ**, critical PASS.  
Если NO — починить конфиг/воркеры, не запускать UI.

---

## 2. Два процесса

```bash
# терминал 1
python dispatcher.py

# терминал 2
python dispatcher_ui.py
```

---

## 3. Матрица из 5 задач (минимум)

| # | Задача в чате | Ожидание |
|---|---------------|----------|
| 1 | `format code` / простой skill | DONE skill, без LLM (или быстрый skill) |
| 2 | `переименуй hi_fn в hello_fn` + файл с `def hi_fn` | файл на диске изменён, DONE |
| 3 | «добавь комментарий в app.py» (через aider) | git diff / file change, verify, DONE |
| 4 | намеренно сломать тест / verify | **не** DONE; ERROR или RETRY |
| 5 | повторить простую задачу | стабильный DONE |

Опционально позже: timeout, reclaim.

---

## 4. Что смотреть при сбое

| Симптом | Куда смотреть |
|---------|----------------|
| Задача не взялась | desktop_queue, dispatcher log, intake reject в чате |
| Skill matched но ничего не сделал | skill fail в логе; `message=` для rename |
| Worker крутится вечно | executor timeout, ollama alive |
| DONE без изменений | false-DONE → verify policy |
| ERROR без текста | chat poll / done json summary |

---

## 5. Offline smoke (перед live, по желанию)

```bash
python scripts/validate_config.py
python -m core.doctor
pytest -q tests/test_skill_dispatcher_contract.py tests/test_intake_pipeline.py tests/test_local_queue_intake.py
python scripts/live_smoke.py --mock
```

---

## Критерий «можно идти дальше»

- 5 строк матрицы выше без ручного ковыряния JSON  
- Ни одного false-DONE на задаче 4  
- Пользователь видит DONE/ERROR в чате  

Только после этого — router/UI polish/autopilot.

## Offline UI readiness (до live)

Уже в коде (не требует Ollama):

- Chat: phase «○ в очереди» → processing → DONE/ERROR с человекочитаемым result
- History: строка итога + детали с summary сверху
- Logs: chat:N = размер .agentbus/desktop_queue
- Doctor: desktop_queue + desktop_channel
- FileBus.ensure() всегда создаёт channels/desktop/*

При live достаточно смотреть чат и history — не только сырой JSON.

---

## Offline product hardening (PC-25…34) — уже в коде

Перед live можно не трогать; это уже закрыто offline:

- deferred/stuck **desktop** reclaim + claim
- DEDUPED / pre_hook / decompose → terminal JSON (UI pending очищается)
- ProjectContext ERROR полный terminal path
- pending stale + hint «запустите диспетчер»
- `is_running()` видит CLI-диспетчер (DispatcherLock PID)
- entry points: `dispatcher.py` / `dispatcher_ui.py` / `admin_ui.py`

Проверка без Ollama:

```bash
PYTHONPATH=src python scripts/product_path_check.py
# RESULT: GREEN
```

## Progressive UX (offline-ready, verify on PC)

- [ ] Wizard: step profile (beginner/developer/advanced/auto)
- [ ] Click **Agent · …** → change autonomy / suggestions
- [ ] Task DONE → Report with checkboxes → Continue enqueues only checked
- [ ] Health **?** opens HelpPopover
- [ ] Ctrl+K palette: Layout Agent / Code / Focus
- [ ] ▶ Run / ■ Stop on project with main.py (optional)
- [ ] After 5 Continues — one soft habit message (no silent profile change)
- [ ] `/help` shows UI language (? ⚠ → ↶)


---

## 5. Product path (после базовой матрицы)

Проверить связку Plan ↔ Queue ↔ History (уже в UI offline):

| # | Действие | Ожидание |
|---|----------|----------|
| 6 | Project Center → **Аудит** → **В план** | шаги в Plan panel, footer `plan: N pending` |
| 7 | **В очередь** или chat `/enqueue` | task в Queue, шаг `IN_PROGRESS` |
| 8 | После DONE — **Continue** | следующий PENDING уходит в очередь |
| 9 | History: фильтр Done / Errors | группировка Сегодня/Вчера |
| 10 | Клик по карточке History | Task Detail + Diff |

Критерий: **нет false-DONE**, Continue не создаёт задачу без PENDING-шага, footer отражает queue+plan.

