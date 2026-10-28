# UB_MODE_AUDIT — применение скилла ub-mode (Uncle Bob) к AgentBus

> Дата: 2026-10-28. Скилл: github.com/unclebob/ub-mode — «First Make it Work → Then Make it Right → Practice» + quality gates (Crapper: CRAP < 10, Dryer: дубли, Mutator, рапчёт сложности, малые упорядоченные коммиты, ведение лога).

## Лог применения («Keep a Log»)

1. **Аудит (Crapper)**: цикломатическая сложность всех функций — инвентаризация 604 .py (src/, ui/, tests/, eventbus/, providers/, protocol/, plugins/, recipes/, nvcode_client/, scripts/), эвристический скан + ручная сверка топ-результатов по 51 файлу src/core (полные исходники).
2. **Аудит (Dryer-lite)**: нормализованные 6-строчные окна (маскирование строк/чисел/комментариев), порог ≥2 повторов.
3. **Build the Lever**: `scripts/quality_gates.py` — офлайн AST-гейт без сторонних зависимостей: сложность per function, worst-case CRAP = comp²+comp, allowlist-рапчёт, дубликаты report-only. Тесты: `tests/test_quality_gates.py` (hermetic, tmp_path).
4. **Декомпозиция топ-нарушителя CLI-слоя**: `dispatcher_main._diagnose` (comp 49) → 9 секционных хелперов `_diag_*` + оркестратор; `dispatcher_main.main` (comp 43) → 4 хелпера `_main_*` + оркестратор (коммит `66ccd8f`, дословный перенос, все счётчики try/except/SystemExit/print сохранены).
5. **CI-проводка**: секция 5 в `scripts/ci_offline.sh` — advisory до первой локальной валидации базовой линии, затем fail=1.
6. **Фикс TD-032** (коммит `bcb7f029`): в `_llm_success_diff_preview` `files_payload` теперь собирается ДО подсчёта `_diff_lines` — чтение вынесено в `_llm_read_diff_payload` (дословный перенос блока из `_llm_capture_diff_payload`, capture вызывает тот же хелпер). `assess_risk` впервые видит реальный размер диффа. Регресс-тесты: `tests/test_diff_preview_td032.py` (3 теста: реальные строки из payload, gitops=None -> 0 и без краша, фильтр по task.files); CI pytest-список дополнен. Оценки comp после фикса: preview 22, reader 19, capture 8.

## Топ-нарушители на момент аудита (comp, worst-case CRAP = comp²+comp при нулевом покрытии)

| Функция | comp | CRAP worst | Статус |
|---|---|---|---|
| `src/core/runtime.py::process` | 57 | 3306 | открыт (TD-031, приоритет 1) |
| `src/core/dispatcher_main.py::_diagnose` | 49 | 2499 | **разбита** → 9 хелперов (`66ccd8f`) |
| `src/core/runtime.py::run_forever` | 48 | 2352 | открыт (приоритет 2) |
| `src/core/task_result.py::_tr_changes` | 44 | 1980 | открыт (приоритет 3) |
| `src/core/dispatcher_main.py::main` | 43 | 1892 | **разбита** → 4 хелпера (`66ccd8f`) |
| `src/core/task_result.py::build_task_result` | 43 | 1892 | уже декомпозирована (TD-014, `6a9cebd`) |
| `src/core/rp_cache_skills.py::_try_skill` | 40 | 1640 | открыт (приоритет 4) |
| `src/core/task_result.py::history_card_lines` | 39 | 1596 | открыт (приоритет 5) |
| `src/core/runtime.py::diagnose` | 37 | 1406 | открыт |
| `src/core/rp_llm.py::_llm_workers_exhausted` | 36 | 1332 | перенос из TD-012 (не новый долг) |
| `src/core/task_result.py::format_task_report` | 36 | 1332 | открыт |

## Дистрибуция (src/core, 539 функций в 51 файле)

- comp ≥ 10: **121** (22%)
- comp 6–9: 71
- comp ≤ 5: 347

## Дубликаты (Dryer-lite)

Крупных блоков ≥0.8 нет (TD-020 закрыл последний крупный дубль). Находки — только шум: regex-таблицы `error_ux`, повторяющиеся import-fallback паттерны. Гейт показывает топ-10 дублей в report-режиме (report-only, никогда не фейлит).

## Рычаг (использование)

```bash
python scripts/quality_gates.py                          # отчёт: топ по сложности + дубли
python scripts/quality_gates.py --gate --max 60          # фейлит НОВЫЙ долг вне allowlist
python scripts/quality_gates.py --update-allowlist --max 60   # заморозить существующий долг
```

## План рапчёта

`--max 60 → 40 → 25 → 12`. При полном тестовом покрытии CRAP = comp, поэтому цель «CRAP < 10» ≈ comp ≤ 9 (порог 12 с запасом на review-ветку). Allowlist замораживает существующий долг — новый долг фейлит гейт сразу, независимо от порога.

## Оговорки

- Гейт и тесты написаны офлайн; в среде выполнения нет Python-интерпретатора, поэтому локальный прогон не выполнялся. Перед включением `fail=1` в CI: `python -m pytest -q tests/test_quality_gates.py` и `python scripts/quality_gates.py --gate --max 60`; при нарушителях >60 — декомпозировать или `--update-allowlist --max 60`, затем раскомментировать `fail=1` в `scripts/ci_offline.sh`.
- Связная запись реестра: TD-031 в `docs/TECHNICAL_DEBT_REGISTRY.md`.
