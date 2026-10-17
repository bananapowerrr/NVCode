# TECHNICAL_DEBT_REGISTRY.md — реестр технического долга AgentBus

> Дата: 2026-10-08. Метод: grep-скан + два параллельных суб-агентных скана (длинные функции/дубли/мёртвый код; гонки/атомарность) + ручная сверка строк.
> Соглашение аудита: **фиксируем, не чиним.** Исправления — по явному запросу.
> `Requires Human Review` — запись, которую стоит перепроверить человеком (вывод скана, а не ручной чек).

## Легенда

- ЛУ = Long function; ГРАЖД = Race/atomicity; ДУПЛ = Duplication; DEAD = мёртвый код; BUG = баг в коде; PROCESS = процесс/документация; DESIGN = архитектурное решение с риском.
- Критичность: **High** / Medium / Low.

## Реестр (30 записей)

| ID | Файл:строка | Тип | Крит. | Описание | Рекомендация | Примечание |
|----|-------------|-----|-------|----------|--------------|------------|
| TD-001 | `src/core/rp_verify.py:206` (vs `src/safety/gitops.py:237,285`) | BUG | **High** | Вызов `gitops.commit_plan(...)` — метода не существует (есть `plan_commit`/`commit`). При `AGENTBUS_GIT=1` в git-репо вся ветка DONE+plan падала: `AttributeError` ловился, `result.ok=False` → ERROR. | Заменить на реальный `gitops.commit` с сообщением `build_commit_message` (коммит только `plan.stage`); при пустом `stage` коммит не обязателен. | **FIXED 2026-10-08**: `rp_verify.py:204-225`; реальный тест `test_git_commit_success_with_real_gitops` без мока `commit_plan`; мок в `test_verify_fail_closed.py` удалён |
| TD-002 | `src/intelligence/sub_agent.py:205-213` | ГРАЖД | **High** | `_save_registry` пишет реестр одним `write_text` (213) без tmp+rename; при параллельных воркерах возможен partial write / потеря реестра подзадач. | Атомарная запись tmp+`os.replace`, блокировка на `parent_id`. | сканируемо; строки сверены с кодом  **FIXED 2026-10-17**: atomic registry write tmp+`os.replace` (`sub_agent._atomic_write_text`) |
| TD-003 | `src/intelligence/sub_agent.py:184-192` | ГРАЖД | **High** | `spawn_many` пишет задачи в `incoming` прямым `write_text` без уникальности/атомарности; два спавна могут гонять один файл. | Уникальные имена + tmp+rename + retry при конфликте. | сверено (write_text: 189)  **FIXED 2026-10-17**: child tasks written via atomic `bus.write` in `spawn_many` |
| TD-004 | `src/intelligence/sub_agent.py:317` | ГРАЖД | **High** | `cancel_pending` пишет `dest.write_text` в канал без блокировки/атомарности; гонка с claim/reclaim. | tmp+rename, единый путь через `bus.move`. | сверено  **FIXED 2026-10-17**: cancel via atomic `bus.write` (tmp+rename inside FileBus) |
| TD-005 | `src/core/rp_lifecycle.py:204-213` | ГРАЖД | **High** | `WAITING_FOR_CAPACITY`: после `bus.move(processing→deferred)` идёт прямая запись `path.write_text` (213) — обход атомарного move, риск двойной записи/потери метаданных. | Единый способ: вернуть через bus, не писать в канал напрямую. | сверено (`_finalize_early_terminal`: 88)  **FIXED 2026-10-17**: DEFERRED payload written via `bus.move` + `bus.write` (no direct channel write) |
| TD-006 | `src/core/reclaim.py:193,220,231,249` | ГРАЖД | **High** | `write_lease`/`touch_lease` перезаписывают аренду без проверки владельца; два reclaim-процесса могут конфликтовать (tmp есть в 220/249, но семантика «кто владелец» не защищена). | CAS по владельцу lease + TTL-инвазию; проверка что аренда ещё наша до записи. | сверено  **FIXED 2026-10-17**: `_atomic_write_json` for leases; owner CAS in `touch_lease(expected_worker_id)` |
| TD-007 | `src/intelligence/living_plan.py:402,412` | ГРАЖД | Medium | `save_living_plan` пишет `living_plan.json` и `.md` двумя `write_text` без блокировки; два воркера → рассинхрон. | Атомарная запись + единая точка сохранения. | сверено  **FIXED 2026-10-17**: atomic json+md save (tmp+`os.replace`) in `save_living_plan` |
| TD-008 | `src/safety/health.py` (состояние) | ГРАЖД | Medium | Файл состояния пишется неатомарно; ошибки записи глотаются (`test_missing_state_file_is_not_an_error`). | tmp+rename; логировать сбой записи. | тест подтверждает поведение  **FIXED 2026-10-17**: state write already tmp+replace; save failure now logged (`health.save_state`) |
| TD-009 | `src/skills/custom/format_code.py:4` | DEAD | Low | Стаб `"""TODO: wire to existing SkillRegistry or implement."""` — не подключён ни в один реестр. | Подключить или удалить. | сверено grep TODO |
| TD-010 | `src/safety/gitops.py:525` `build_commit_message` | DEAD | Low | Импортировалась (`runtime.py:34`, `runtime_ops.py:27`), но нигде не вызывалась. | Использовать в реальном коммите задач. | **FIXED 2026-10-08**: применяется в `rp_verify.py` при коммите `plan.stage` |
| TD-011 | `src/core/rp_cache_skills.py:415` vs `src/core/rp_skills_stage.py:68` | ДУПЛ | Medium | Полный дубль `_try_skill` (~140 строк). Прод MRO runtime использует `RPCacheSkillsMixin`, а тесты якорят `rp_skills_stage` (`tests/test_skills_verification_day11.py:41-62`) — прод и тесты смотрят на разные копии. | Один mixin; перенести тесты на используемый вариант. | сверено |
| TD-012 | `src/core/rp_llm.py:466` `_stage_llm_pipeline` | ЛУ | Medium | 648 строк — единственный вход LLM-этапа (роутинг/gate/cache/skills/sub-agents/worker). Высокий порог ревью. | Декомпозиция на под-методы по шагам пайплайна. | scan |
| TD-013 | `src/core/runtime_ops.py:277` `finish_task` | ЛУ | Medium | 298 строк, содержит resign/очередь/финализации. | Декомпозиция, извлечение resign. | scan |
| TD-014 | `src/core/task_result.py:258` `build_task_result` | ЛУ | Medium | 283 строки форматирования результата для UI/чата. | Таблица/шаблоны вместо ветвления. | scan |
| TD-015 | `src/intelligence/autonomous_loop.py:152` `run_tick` | ЛУ | Medium | 268 строк ночного цикла с recovery-ветками; циклонно complex. | Декомпозиция + тесты на сценарии recovery. | scan |
| TD-016 | `src/core/night_mode_controller.py:243` `run_autonomous_loop` | ЛУ | Medium | 257 строк бюджета/итераций Night Mode. | Декомпозиция. | scan |
| TD-017 | `src/core/doctor.py:124` `run_doctor` | ЛУ | Medium | 246 строк диагностики (`--diagnose`). | Декомпозиция + таблицы. | scan |
| TD-018 | `src/core/rp_verify.py:24` `_phase_verify_and_commit` | ЛУ | Medium | 237 строк; содержит git-блок с багом TD-001. | Вынести коммит в отдельный хелпер (и починить TD-001). | scan |
| TD-019 | `src/intelligence/project_snapshot.py:87` `build_project_snapshot` | ЛУ | Medium | 222 строки снапшота проекта. | Декомпозиция. | scan |
| TD-020 | `src/core/diagnose.py:21` `diagnose_environment` | ЛУ | Medium | 207 строк; дублирована `_diagnose` в `dispatcher_main.py:37`. | Свести к одной реализации. | scan |
| TD-021 | `src/core/bus.py` `desktop`-канал | DESIGN | Medium | Known soft-ok для `desktop`-статуса (~строка 119): расходится со строгой трактовкой остальных каналов. Осознано, но создаёт второй стандарт. | Единая политика статусов (или явный флаг legacy). | Requires Human Review |
| TD-022 | документы/артефакты (включая задачу аудита) | PROCESS | Low | Заявлено «1723 теста», фактически `pytest --collect-only` = **1823** (2026-10-08, интерпретатор `D:\Workspace\.venv`). | Обновить число в документации и статус-отчёте. | проверено прогоном |
| TD-023 | `src/intelligence/solution_cache.py:370,418` | ГРАЖД | Medium | Запись кэша решения: 370 — прямой write_text, 418 — tmp (не единообразно); кэш и каналы живут на облачном диске — риск при sync/lock. | Унифицировать tmp+rename на всех ветках записи кэша. | сверено |
| TD-024 | `src/intelligence/conversation.py:318` | ГРАЖД | Low | История чата пишется `write_text` без блокировки. | tmp+rename. | сверено  **FIXED 2026-10-17**: atomic session write in `ConversationStore._save`; duplicate `@dataclass` removed |
| TD-025 | `src/intelligence/report.py:54` | ГРАЖД | Low | Утренний отчёт пишется `write_text` (неатомарно). | tmp+rename. | сверено  **FIXED 2026-10-17**: atomic report write (tmp+`os.replace`) in `NightlyReport.save` |
| TD-026 | `src/core/local_queue.py:58,73,85-88` | ГРАЖД | Medium | Spill-механизм: write (58) и unlink (73)/claim-read-unlink (85-88) не атомарны; при краше между ними — риск дубля или потерянной задачи. | Атомарная связка (tmp+rename + «метка claimed» отдельным файлом). | сверено  **FIXED 2026-10-17**: spill put via tmp+`os.replace`; claim via atomic rename to `*.claiming.json` |
| TD-027 | `src/core/executor.py:670` `write_text("")` | DESIGN | Medium | «Опустошение» целевого файла при сбое — деструктивная операция без undo. | Перед изменением — бэкап/копия в `.agentbus`. | сверено; Requires Human Review |
| TD-028 | `src/safety/health.py` (loop-тиры) | DESIGN | Low | Loops трактуются как «шум модели», а не отказ воркера; при длинных монотонных циклах возможна недооценка. | Отдельная метрика «число итераций без прогресса» и алерт. | scan |
| TD-029 | `src/core/router.py` `estimate_tokens`/`BYTES_PER_TOKEN=4` | DESIGN | Low | Приблизительный токенизатор (bytes/4); для кириллицы занижает/завышает бюджет контекста. | Замер реальных токенов на провайдере или таблица по типам контента. | сверено |
| TD-030 | `src/safety/loopguard.py` `collapse_numbers=False` | DESIGN | Low | Новизна не схлопывает цифры; шаги, меняющие только числа, легче проходят за порог новизны в длинных циклах. | Точечный регресс-тест, при необходимости включить схлопывание. | сверено (осознанное решение) |

## Статистика по критичности
- **High: 6** (TD-001..006)
- Medium: 15
- Low: 9

## Правила ведения
1. Новые находки добавляются с актуальной датой и `file:line`.
2. Исправление записи → статус `FIXED` + ссылка на коммит/тест (аудит сам ничего не чинит).
3. `Requires Human Review` НЕ считается подтверждённой к критическим действиям без живого человека.
