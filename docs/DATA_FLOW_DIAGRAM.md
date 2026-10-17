# DATA_FLOW_DIAGRAM.md — поток данных NVCode

> Дата аудита: 2026-10-08.
> Источник истины по файлам: `src/core/*`, `src/intelligence/*`, `src/safety/*` (см. ссылки `файл:строка` в тексте).
> Все границы ниже — **_fail-closed_**: при сбое раннего фильтра задача не выполняется «по-тихому», а уходит в `ERROR`/`DEFERRED` с причиной.

## 1. Общая схема (Mermaid)

```mermaid
flowchart TD
    subgraph IN["Вход / intake"]
        UI["ChatPanel / LocalQueue (desktop)"] --> SEC1
        FBB["channels/<channel>/incoming/*.json (FileBus)"] --> SEC1
        REC["Recipes / CLI / emit_recipe"] --> SEC1
        SEC1["local_queue._reject_unsafe_files<br/>intake_pipeline.accept_task_raw (strict, fail-closed)"]
    end

    subgraph CORE["Ядро runtime / воркер"]
        SEC1 --> CLAIM["Task.from_dict + claim (project_lock max_global=1)"]
        CLAIM --> DEDUP["dedupe fingerprint"]
        DEDUP -- "duplicate" --> DONE_PATH
        DEDUP --> PRE["rp_lifecycle._phase_pre_hooks: health / security / pre-check"]
        PRE --> HEALTH{"health.pre_check: cooldown / billing?"}
        HEALTH -- "billing-ошибка => 24h cooldown" --> DEFER_PATH
        HEALTH --> LLM["rp_llm._stage_llm_pipeline"]
        LLM --> ROUTER["router.select_executor: выбор воркера, бюджет контекста"]
        ROUTER --> PAY{"paid_gate: провайдер платный?"}
        PAY -- "да и не whitelisted => DEFERRED" --> DEFER_PATH
        PAY --> META{"meta-decompose: AGENTBUS_META + классификатор?"}
        META -- "да" --> MDEC["meta_decompose (1.5B OLLAMA) => план подзадач"]
        MDEC --> SUB["SubAgent.spawn_many: parent_id, max_depth=2, max_children"]
        SUB --> FBB
        SUB --> SUBR["sub_agent registry (idx + parent_id)"]
        META -- "нет" --> CACHE{"solution_cache: hit?"}
        CACHE -- "hit" --> CDONE["rp_cache_skills apply => DONE-канал"]
        CDONE --> DONE_PATH
        CACHE -- "miss" --> SKILLS{"skills stage: cache_skills._try_skill<br/>(RPCacheSkillsMixin)"}
        SKILLS -- "skill ok" --> CDONE
        SKILLS -- "нет / fail" --> EXEC["executor.run => воркер (opencode/aider/...)"]
        EXEC --> OUT["worker result (ok / err + evidence)"]
        OUT --> VERIF["rp_verify._phase_verify_and_commit: ladder, anti-false-done"]
        VERIF -- "fail => retry policy" --> ERR_PATH
        VERIF -- "ok + plan => git commit (gitops)" --> TERM["terminal_path: DONE => done/"]
        TERM --> DONE_PATH
        VERIF -- "manual/не-verify => ERROR/DEFERRED" --> DEFER_PATH
    end

    subgraph FILES["Хранилище FileBus (channels/<chan>/)"]
        DONE_PATH["done/*.json (терминал)"]
        DEFER_PATH["deferred/*.json (retry/reclaim)"]
        ERR_PATH["errors/*.json (терминал)"]
        PROC["processing/*.json (аренда, reclaim)"]
    end

    subgraph NIGHT["Night Mode: AGENTBUS_NIGHT_MODE=1"]
        NS["night_policy.plan_night_run:<br/>NightScheduler.is_night / filter_for_now"] --> NLOOP["night_policy.run_policy_night -><br/>night_mode_controller.run_autonomous_loop"]
        NLOOP --> CORE
        NLOOP --> REP["morning report (night_policy.build_morning_report) => done/"]
    end

    LOOP["safety/loopguard: новизна, повторы, строки"] --> PRE
    HC["health.end_task: тиры, cooldown"] --> PRE
    CORE --> FILES
```

## 2. Поток по шагам (file-bus)

1. **Вход.** Внешний канал кладёт JSON в `channels/<chan>/incoming/`; desktop-чат идёт через `LocalQueue.put` → spill в `.agentbus/desktop_queue/` (`src/core/local_queue.py:41-64,166-186`). FileBus читает/переносит `incoming → processing` (`bus.py`, move = copy2 + unlink, устойчиво к Dropbox-lock).
2. **Intake (fail-closed).** `accept_task_raw` нормализует контракт (`task_contract.normalize_task`) и поднимает `IntakeError` при небезопасном payload/пути (`src/core/intake_pipeline.py:21-62`); desktop дополнительно фильтрует опасные пути в `_reject_unsafe_files` (`local_queue.py:17-28`).
3. **Claim.** Воркер забирает задачу с учётом глобального лока проекта (`project_lock.py`, `max_global=1`). Дубли отсекаются fingerprint'ом (`runtime.py:310-318 dedupe`).
4. **Pre-hooks.** `_phase_pre_hooks` (`rp_lifecycle.py`) прогоняет health pre-check: billing-пометки → cooldown, тайры ошибок → паузы (`health.py`). При billing-ошибке задача не семплируется платным провайдером повторно 24h.
5. **LLM-этап** `_stage_llm_pipeline` (`rp_llm.py:466, ~648 строк`):
   - роутер выбирает воркера и подгоняет контекст (`router.select_executor`, `LOCAL_CTX_BUDGET=6000`);
   - paid-gate: неизвестный провайдер трактуется **платным** → `DEFERRED` с причиной (`paid_gate.py`, см. `CONTRACTS.md`);
   - meta-decompose (env `AGENTBUS_META`): сложная задача → `meta_decompose` (1.5B через `OLLAMA_HOST`) → подзадачи от `SubAgent` с `parent_id` в `incoming` (`sub_agent.py:184-213 spawn_many/_save_registry`), глубина ≤ `MAX_DEPTH=2`, детей ≤ `DEFAULT_MAX_CHILDREN=8` / жёстко `ABSOLUTE_MAX_CHILDREN=16`;
   - solution-cache: hit → кэш-применение → DONE (быстрый путь, `rp_cache_skills.py`);
   - skills: `_try_skill` сначала из кэша навыков, иначе навык запускается как под-исполнитель; при неудаче → переход к воркеру.
6. **Worker.** `executor` вызывает выбранного воркера; результат — структура `WorkerResult` с evidence (`test_worker_contract_fc14`).
7. **Verify + terminal.** `_phase_verify_and_commit` (`rp_verify.py:24, 237 строк`): лестница верификации, анти-false-done; при ok + plan ≠ None → git-коммит (см. **TD-001**, баг `commit_plan`); терминальную папку определяет runtime через `terminal_path.py` (`FOLDER`: `DONE→done`, `ERROR→errors`, `DEFERRED→deferred`; `verification_allows_done` требует явный positive verify-сигнал).
8. **Терминалы.** `done/`, `errors/` — конечные; `deferred/` — повторный вход в `CLAIM` (reclaim, `reclaim.py` lease).

## 3. Поток desktop-чата

`ChatPanel → enqueue_desktop_task` (`local_queue.py:166`) → `LocalQueue` (per-root, spill для межпроцессности UI↔dispatcher) → воркер/dispatcher `claim()` (spill-восстановление) → тот же `_process_body` (шаги 4–8) → результат обратно в UI. Ошибка форматируется в human-readable строку (`error_ux`, `task_result.build_task_result`).

## 4. Night Mode

При `AGENTBUS_NIGHT_MODE=1` ночной цикл планируется через `night_policy` (`plan_night_run` / `run_policy_night`, `night_policy.py:208,244`): `NightScheduler` (`night_scheduler.py:57-78`) выбирает задачи в ночном окне (`start 22:00/end 06:00`, `max_tasks_per_night`, `min_complexity`), затем исполняющий цикл — `night_mode_controller.run_autonomous_loop` (`night_mode_controller.py:243`), привязанный к Runtime через `night_runtime_bridge.runtime_execute_fn`; утром пишется отчёт (`night_policy.build_morning_report` + `report.py`). Всё идёт через те же ворота верификации и paid-gate (вечером платных задач не набирают сверх бюджета).

## 5. Ключевые решения и риски

- **Fail-closed на всех границах** — единственный способ попасть в `done/` — вся лестница верификации + терминальный контроль `verification_allows_done`; воркер сам DONE не объявляет.
- **DESIGN** (риск): loops классифицируются как «шум модели», а не отказ воркера (`health.py`) — см. `TECHNICAL_DEBT_REGISTRY.md` TD-028.
- **KNOWN GAP (закрыт TD-001, 2026-10-08)**: при `AGENTBUS_GIT=1` путь DONE+git коммитит только `plan.stage` через реальный `gitops.commit` (вместо несуществовавшего `gitops.commit_plan`); при пустом `stage` коммит не требуется. См. TD-001.
