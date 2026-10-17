# NVCode roadmap

> Актуализировано 2026-10-17. Завершённые этапы суммированы без исторических таблиц; детали — в тестах и реестре технического долга.

## Завершённые этапы

- **Runtime engineering R1–R6** — terminal path (`finish_task`), ExecutionResult → Evidence, Recovery Controller, worker/fallback contract, context budget, Night Mode v0. Всё в репо, покрыто тестами.
- **Распространение UPDATE-001A–F** — версия + release manifest, UpdateChecker, UI-уведомление, внешний updater, проверка хешей + backup/rollback, GitHub Release + build pipeline.
- **DEV-001–009** — runtime decision, recovery policy, WorkerResult, worker fallback, context budget, continuity задач, Night Mode loop + persist + policy.
- **Pre-UI blockers B1–B3** — atomic claim + terminal contract, cache/skill путь через `finish_task`, post-failure meta decomposition.

## Текущий фокус

| ID | Фокус | Статус |
|----|-------|--------|
| WIRE-003 | worker_result на всех ERROR-выходах | next |
| WIRE-004 | context_audit в metadata (assemble возвращает его) | partial |
| B4 | E2E-доказательство связки B1–B3 | partial |
| LIVE-001 | Приёмка на реальном ПК (Ollama/Aider) — см. [LIVE_ACCEPTANCE](LIVE_ACCEPTANCE.md) | PC |
| NAMING | Миграция legacy-имён AgentBus → NVCode с тестами совместимости | planned |

## Принципы обновления

1. **Приложение не заменяет себя на ходу** — внешний updater.
2. **Пользовательские данные не живут в пути установки** (`%APPDATA%/AgentBus/` — legacy-путь до протестированной миграции; не переименовывать вслепую).
3. **Fail-closed manifest**: нет sha256 / невалидный JSON → нет предложения обновления.
4. **Git — для разработчиков**; Releases + UpdateChecker — для пользователей.

### Целевой UX

```
UpdateChecker → "NVCode update available" → [Update] [Later]
  → download + verify → updater stops app → replace binaries → restart
  → failed start → rollback
```

## Явно не сейчас

MCP, тяжёлый RAG, мульти-агентный параллелизм, разрастание облачных провайдеров.
