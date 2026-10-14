# -*- coding: utf-8 -*-
"""BROKEN PLACEHOLDER — restore immediately.

Accidental overwrite during automated fix (2026-10-15).

Restore on your machine:

    git fetch origin
    git checkout a85ea79f60063f8a6dbe993dc796150dfc40ad95 -- src/core/rp_cache_skills.py
    git add src/core/rp_cache_skills.py
    git commit -m "restore rp_cache_skills from a85ea79"

Optional W3 (after restore): remove GLOBAL_METRICS.record_task(...) after
finish_task in _apply_cached_solution / skill finalize — finish_task already records.
"""
from __future__ import annotations

raise ImportError(
    "src/core/rp_cache_skills.py must be restored from commit a85ea79 "
    "(git checkout a85ea79 -- src/core/rp_cache_skills.py)"
)
