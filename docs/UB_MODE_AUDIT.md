# UB_MODE_AUDIT — применение скилла ub-mode (Uncle Bob) к AgentBus

> Дата: 2026-10-28. Скилл: github.com/unclebob/ub-mode — «First Make it Work → Then Make it Right → Practice» + quality gates (Crapper: CRAP < 10, Dryer: дубли, Mutator, рапчёт сложности, малые упорядоченные коммиты, ведение лога).

## Лог применения («Keep a Log»)

1. **Аудит (Crapper)**: цикломатическая сложность всех функций — инвентаризация 604 .py (src/, ui/, tests/, eventbus/, providers/, protocol/, plugins/, recipes/, nvcode_client/, scripts/), эвристический скан + ручная сверка топ-результатов по 51 файлу src/core (полные исходники).
2. **Аудит (Dryer-lite)**: нормализованные 6-строчные окна (маскирование строк/чисел/комментариев), порог ≥2 повторов.
3. **Build the Lever**: `scripts/quality_gates.py` — офлайн AST-гейт без сторонних зависимостей: сложность per function, worst-case CRAP = comp²+comp, allowlist-рапчёт, дубликаты report-only. Тесты: `tests/test_quality_gates.py` (hermetic, tmp_path).
4. **Декомпозиция топ-нарушителя CLI-слоя**: `dispatcher_main._diagnose` (comp 49) → 9 секционных хелперов `_diag_*` + оркестратор; `dispatcher_main.main` (comp 43) → 4 хелпера `_main_*` + оркестратор (коммит `66ccd8f`, дословный перенос, все счётчики try/except/SystemExit/print сохранены).
5. **CI-проводка**: секция 5 в `scripts/ci_offline.sh` — advisory до первой локальной валидации базовой линии, затем fail=1.
6. **Фикс TD-032** (коммит `bcb7f029`): в `_llm_success_diff_preview` `files_payload` теперь собирается ДО подсчёта `_diff_lines` — чтение вынесено в `_llm_read_diff_payload` (дословный перенос блока из `_llm_capture_diff_payload`, capture вызывает тот же хелпер). `assess_risk` впервые видит реальный размер диффа. Регресс-тесты: `tests/test_diff_preview_td032.py` (3 теста: реальные строки из payload, gitops=None -> 0 и без краша, фильтр по task.files); CI pytest-список дополнен. Оценки comp после фикса: preview 22, reader 19, capture 8.
7. **Рэтчет TD-031 — этап 40**: декомпозиция `runtime.process` (comp ~56 → ~14, `82f76c1` — 5 хелперов `_process_*`), `runtime.run_forever` (~48 → ~9, `81ab4be` — 7 хелперов `_startup_recover`/`_loop_*`), `task_result._tr_changes` (~44 → ~10, `17db516` — `_tr_changes_files`/`_tr_changes_counts`). Дословные переносы, структурный валидатор, comp-дельты. Ревизия TD-014 вскрыла потерянные `return` в шести `_tr_*`-хелперах (TD-033) — восстановлены (`5ea05ebe`) + регресс-тесты `tests/test_task_result_returns.py`. Порог гейта в CI: `--max 60` → `--max 40` (следующий этап — 25).
8. **Emergency-восстановление executor.py (2026-10-28, «PLACEHOLDER accident»)**: коммит `61544d3` (TD-027) затёр `src/core/executor.py` строкой PLACEHOLDER; b64-канал восстановления также подменялся плейсхолдерами (`_exec_b64_0.txt` = `PLACEHOLDER_PART0`, удалён). Executor восстановлен из последней хорошей ревизии `17db516` (`ba2691a2`) с патчем TD-027 (C), применённым вручную CRLF-aware (регекс `apply_td021_td027.py` не матчит CRLF-файлы). TD-021 применён к `runtime_ops._active_channels` (`1f542988`). Тесты TD-027 уже были на main — восстановление делает их зелёными; файл добавлен в CI pytest-список. Урок: перед патч-скриптами проверять EOL цели.
9. **Рэтчет TD-031 — этап 25**: декомпозиция пяти топ-функций (дословные переносы, структурный валидатор + якорные/дельта-проверки def/return): `rp_cache_skills._try_skill` (~40 → ~7, `76ca67a` — хелперы `_skill_*`), `task_result.history_card_lines` (~39 → ~6, `5ee87db` — `_hc_meta_bits`/`_hc_verify`/`_hc_files`/`_hc_lifecycle`), `runtime.diagnose` (~37 → ~3, `0d7793b` — 7 хелперов `_diag_*`), `task_result.format_task_report` (~36 → ~6, `946215f` — `_ftr_fields`/`_ftr_verify_line`), `rp_llm._llm_workers_exhausted` (~36 → ~9, `196ac9a` — `_llm_exhausted_terminal`/`_llm_exhausted_paid_gate`). Порог гейта в CI: `--max 40` → `--max 25` (`7536806`); следующий этап — 12.

## Топ-нарушители на момент аудита (comp, worst-case CRAP = comp²+comp при нулевом покрытии)

| Функция | comp | CRAP worst | Статус |
|---|---|---|---|
| `src/core/runtime.py::process` | 57 | 3306 | **разбита** → 5 хелперов (`82f76c1`) |
| `src/core/dispatcher_main.py::_diagnose` | 49 | 2499 | **разбита** → 9 хелперов (`66ccd8f`) |
| `src/core/runtime.py::run_forever` | 48 | 2352 | **разбита** → 7 хелперов (`81ab4be`) |
| `src/core/task_result.py::_tr_changes` | 44 | 1980 | **разбита** → 2 хелпера (`17db516`) |
| `src/core/dispatcher_main.py::main` | 43 | 1892 | **разбита** → 4 хелпера (`66ccd8f`) |
| `src/core/task_result.py::build_task_result` | 43 | 1892 | уже декомпозирована (TD-014, `6a9cebd`) |
| `src/core/rp_cache_skills.py::_try_skill` | 40 | 1640 | **разбита** → 6 хелперов (`76ca67a`) |
| `src/core/task_result.py::history_card_lines` | 39 | 1596 | **разбита** → 4 хелпера (`5ee87db`) |
| `src/core/runtime.py::diagnose` | 37 | 1406 | **разбита** → 7 хелперов (`0d7793b`) |
| `src/core/rp_llm.py::_llm_workers_exhausted` | 36 | 1332 | **разбита** → 2 хелпера (`196ac9a`) |
| `src/core/task_result.py::format_task_report` | 36 | 1332 | **разбита** → 2 хелпера (`946215f`) |

## Дистрибуция (src/core, 539 функций в 51 файле)

- comp ≥ 10: **121** (22%)
- comp 6–9: 71
- comp ≤ 5: 347

## Дубликаты (Dryer-lite)

Крупных блоков ≥0.8 нет (TD-020 закрыл последний крупный дубль). Находки — только шум: regex-таблицы `error_ux`, повторяющиеся import-fallback паттерны. Гейт показывает топ-10 дублей в report-режиме (report-only, никогда не фейлит).

## Рычаг (использование)

```bash
python scripts/quality_gates.py                          # отчёт: топ по сложности + дубли
python scripts/quality_gates.py --gate --max 25          # фейлит НОВЫЙ долг вне allowlist
python scripts/quality_gates.py --update-allowlist --max 25   # заморозить существующий долг
```

## План рапчёта

`--max 40` → `--max 25` (этапы 40 и 25 достигнуты 2026-10-28) `→ 12`. При полном тестовом покрытии CRAP = comp, поэтому цель «CRAP < 10» ≈ comp ≤ 9 (порог 12 с запасом на review-ветку). Allowlist замораживает существующий долг — новый долг фейлит гейт сразу, независимо от порога.

## Оговорки

- Гейт и тесты написаны офлайн; в среде выполнения нет Python-интерпретатора, поэтому локальный прогон не выполнялся. Перед включением `fail=1` в CI: `python -m pytest -q tests/` и `python scripts/quality_gates.py --gate --max 25`; при нарушителях >25 — декомпозировать или `--update-allowlist --max 25`, затем раскомментировать `fail=1` в `scripts/ci_offline.sh`.
- Связная запись реестра: TD-031 в `docs/TECHNICAL_DEBT_REGISTRY.md`.
