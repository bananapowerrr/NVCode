# CONTRACTS_AUDIT.md — исторический аудит контрактов NVCode

> Исторический снимок от 2026-10-08; все выводы и счётчики требуют повторной проверки по текущему коду. База: `docs/CONTRACTS.md`, код `src/core|safety|intelligence`.
> Статусы: ✔ OK (контракт зафиксирован и принуждён), ⚠ GAP (отклонение/риск), ❗ БАГ.
> Счётчик тестов: задача заявляла **1723**, фактически pytest собирает **1823** (коллекция 2026-10-08) — см. TD-022.

## 1. Сводная таблица контрактов

| # | Контракт | Где зафиксирован | Принуждение в runtime | Тесты (файлы) | Статус |
|---|----------|------------------|-----------------------|---------------|--------|
| C-01 | Путь к терминалу (runtime решает `done/errors/deferred`, воркер не решает) | `src/core/terminal_path.py` (FOLDER) | `_phase_verify_and_commit` пишет через `terminal_path` | `test_terminal_path_r1.py`, `test_terminal_contract_regressions.py` | ✔ |
| C-02 | FSM задач: `PENDING→CLAIMED→PROCESSING→VERIFYING`; терминалы `DONE/ERROR/`; `DEFERRED→CLAIMED` только retry; DONE необратим | `src/core/tasks.py` (STATES/TRANSITIONS) | `safe_transition` / ранние возвраты | `test_task_state_machine.py`, `test_fsm_invariants.py` | ✔ |
| C-03 | Intake fail-closed: невалидный payload/путь ⇒ `IntakeError`, не soft-pass | `src/core/intake_pipeline.py:21-62`, `local_queue.py:17-28` | `strict=True` на всех входах | `test_intake*.py`, `test_bus_edge_cases.py`, `test_compat_fc20.py` | ✔ |
| C-04 | DONE-gate: нет DONE без verify + evidence; `verification_allows_done` | `src/core/terminal_path.py`, `rp_verify.py:24-236` | Лeстница верификации, анти-false-done | `test_anti_false_done.py`, `test_verification_fc11.py`, `test_execution_evidence.py` | ✔ |
| C-05 | План — терминальная власть: план может заморозить DONE от ERROR | `src/core/tasks.py`, `src/intelligence/living_plan.py` | `finish_task`/`_phase_verify` консультируются с планом | `test_plan_replan_day2.py` | ✔ |
| C-06 | Paid-gate: бесплатны только локальные провайдеры + `opencode`; неизвестный = платный (консервативно) | `src/core/paid_gate.py` (`LOCAL_PROVIDER_IDS`, `LOCAL_CLI_HARNESSES`) | `_check_paid_gate` → `DEFERRED` с причиной | `test_billing_protection.py`, `test_providers.py`, `test_http_error_fail_closed.py` | ✔ |
| C-07 | Worker-контракт: структура `WorkerResult`, timeout/пусто => error-строка с evidence | `src/core/task_result.py`, worker-хелперы | `synthesize_worker_result_on_error_text` и др. | `test_worker_contract_fc14.py`, `test_worker_result*`, `test_worker_diagnostics_day1.py` | ✔ |
| C-08 | Health-контракт: тиры ошибок, 24h cooldown при billing, loop-тиры | `src/safety/health.py` | `health.pre_check/end_task` до повторного полёта | `test_health_edge_cases.py`, `test_graceful.py` | ✔ |
| C-09 | LoopGuard: новизна, повторы (ngram 3/4, window 40, min_novelty=0.15) | `src/safety/loopguard.py` | фильтр до LLM-этапа | `test_loopguard*.py` | ✔ |
| C-10 | Reclaim/аренда: stale `processing` возвращается по lease | `src/core/reclaim.py:193-249` | reclaim-tick в `run_forever` | `test_reclaim.py`, `test_reclaim_contract.py`, `test_e2e_reclaim_cache.py` | ✔ (атомарность — см. TD-006) |
| C-11 | Project lock: глобально ≤1 воркер на корень проекта | `src/safety/project_lock.py` | `can_run/acquire` перед claim | `test_workers_file_locks.py` | ✔ |
| C-12 | Git-безопасность: коммит только безопасной дельты (`plan_commit`), rollback конфликтов | `src/safety/gitops.py:237,480` | `rp_verify` при ok + plan (коммит только `plan.stage`) | `test_gitops.py`, `test_context_git_day3.py`, `test_verify_fail_closed.py` | ✔ (TD-001 исправлен 2026-10-08) |
| C-13 | Sub-агенты: `MAX_DEPTH=2`, детей ≤8/≤16, registry по `parent_id` | `src/intelligence/sub_agent.py` | `spawn_many`/`_save_registry` (флаг `sub_agents`) | тесты subagent-eda | ⚠ атомарность TD-002..004 |
| C-14 | Meta-decompose: 1.5B (OLLAMA) генерирует подзадачи из сложного DEFERRED | `src/skills/meta_decompose.py`, `intelligence/meta_classifier.py` | env `AGENTBUS_META`, `_check_meta` | `test_pev_and_meta_json.py` | ✔ |
| C-15 | Recovery: после billing/error — контекст вопроса человеку или replan, не слепое повторение | `src/core/handle_error_recovery*` | `_phase_error_recovery` | `test_recovery_chat_day14.py`, `test_recovery_controller_r3.py`, `test_recovery_ux_day12.py` | ✔ |
| C-16 | Night Mode: окно 22:00–06:00, лимиты задач/сложности, отчёт утром | `src/intelligence/night_scheduler.py`, `night_mode_controller.py` | `AGENTBUS_NIGHT_MODE` в `run_forever` | `test_night_mode_r6.py`, `test_night_rt_001.py`, `test_dev007_night_loop.py` | ✔ |

## 2. Контракты-ворота (детали)

### C-03 Intake (fail-closed)
`accept_task_raw` (strict=True) гарантирует: `id` (генерится при отсутствии), обязательные ключи (`task_contract.REQUIRED_KEYS=(id,message)`), безопасные `files`. Desktop-мода добавляет `_reject_unsafe_files` до попадания в очередь — никакой вход не может миновать проверку путей.

### C-04 DONE-gate
Воркер никогда не пишет `done/` сам: терминальная папка выбирается в `rp_verify._phase_verify_and_commit` через `terminal_path.FOLDER`, а `verification_allows_done(task)` требует явного положительного verify-сигнала (evidence). Если verify не подтверждён — повтор/`ERROR`, не DONE (`test_anti_false_done`, `test_verification_fc11: embed_report_forces_error_status`).

### C-06 Paid-gate
Реестр бесплатных: локальные провайдеры + CLI-хенарнесы (`opencode`). Неизвестный провайдер → трактуется платным (консервативно; кэш `_KNOWN_PROVIDER_IDS`). Результат: задача не семплируется платным провайдером без явного разрешения; однотипные платные ошибочные ответы дают 24h cooldown (`test_billing_protection`).
### C-12 Git-безопасность — исправлено 2026-10-08
Раньше `rp_verify.py:206` вызывал `gitops.commit_plan(...)`, но в `safety/gitops.py` метода `commit_plan` нет (есть `plan_commit` и `commit`): при `AGENTBUS_GIT=1` в git-репо путь DONE+plan падал с `AttributeError`, задача уходила в ERROR, а не DONE. Тест маскировал баг моком `commit_plan`. Теперь: вызов заменён на реальный `gitops.commit(build_commit_message(...), stage)` с коммитом только безопасных `plan.stage` путей; при пустом `stage` коммит не обязателен. Мок удалён, добавлен `test_git_commit_success_with_real_gitops` (AGENTBUS_GIT=1 + настоящий git-репо). См. TD-001.

## 3. Отклонения / замечания (все фиксируются в реестре долгов)

| ID | Что расходится | Влияние |
|----|----------------|---------|
| TD-001 | `gitops.commit_plan` не существовал (C-12) | High: DONE+git ломался при `AGENTBUS_GIT=1` |
| TD-011 | Дубли `_try_skill` в `rp_cache_skills` и `rp_skills_stage`; прод использует один, тесты якорят другой | Риск расхождения логики навыков |
| TD-005 | Прямая запись в `deferred` при `WAITING_FOR_CAPACITY` (`rp_lifecycle.py:204-213`) | Обход атомарного move |
| TD-022 | Расхождение заявленного числа тестов (1723) и реального (1823) | Документация/артефакты не синхронны |
| TD-028 | Loops = «шум модели», не отказ | Возможна маскировка зацикливаний |

## 4. Рекомендации (приоритет)

1. Добавить e2e-тест всего пути DONE+git с настоящим git-репо на уровне runtime (уровень mixin уже покрыт `test_git_commit_success_with_real_gitops`).
2. Errors-контракт уже fail-closed (C-03/C-04): любой новый вход обязан звать `accept_task_raw`, а не сам писать в канал.
3. Свести дубли навыков (TD-011) до одного mixin и перевести тесты `test_skills_verification_day11.py` на используемый в проде вариант.
4. Обновить число тестов в документации/артефактах (TD-022).