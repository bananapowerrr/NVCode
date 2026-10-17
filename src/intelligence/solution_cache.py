# -*- coding: utf-8 -*-
"""Solution cache — reuse past DONE results without burning LLM quota.

Key = project + message + files + verify + git HEAD (+ dirty flag).
Optional content fingerprint: if source files changed since cache write → miss.
TTL + LRU eviction already applied on get/put.
On hit with file_snapshots: restore content under project root (path-safe).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def _env_path(name: str, default: str) -> Path:
    raw = (os.getenv(name) or "").strip()
    return Path(raw) if raw else Path(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _norm_message(message: str) -> str:
    return " ".join((message or "").strip().lower().split())


def _norm_rel(path: str) -> str:
    """Normalize relative path without treating '..' as stripable dots."""
    p = str(path or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_safe_rel(path: str) -> bool:
    p = _norm_rel(path)
    if not p or p.startswith("/") or p.startswith("~"):
        return False
    if any(part == ".." for part in Path(p).parts):
        return False
    return True


def _norm_files(files: list[str] | None) -> list[str]:
    out = set()
    for f in files or []:
        if not f:
            continue
        p = _norm_rel(str(f))
        if _is_safe_rel(p):
            out.add(p)
    return sorted(out)


def _cache_git_enabled() -> bool:
    raw = (os.getenv("AGENTBUS_CACHE_GIT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def git_repo_state(project_root: str | Path | None) -> tuple[str, bool]:
    """Return (head_sha, is_dirty). Empty sha if not a git repo / disabled.

    dirty=True means unstaged/untracked changes exist — cache keys diverge so
    a clean-HEAD entry is not reused over a dirty tree (and vice versa).
    """
    if not _cache_git_enabled() or not project_root:
        return "", False
    root = Path(project_root)
    try:
        root = root.resolve()
    except OSError:
        return "", False
    # walk up to find .git
    cur = root
    found = False
    for _ in range(10):
        if (cur / ".git").exists():
            root = cur
            found = True
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    if not found:
        return "", False

    import subprocess

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                args,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if r.returncode != 0:
                return ""
            return (r.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    head = _run(["git", "rev-parse", "HEAD"])
    if not head or len(head) < 7:
        return "", False
    # porcelain: any output ⇒ dirty
    status = _run(["git", "status", "--porcelain"])
    dirty = bool(status)
    return head[:40], dirty


class SolutionCache:
    """Persistent JSON cache of successful task solutions."""

    def __init__(
        self,
        cache_path: str | Path | None = None,
        *,
        max_entries: int | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        default = _env_path("AGENTBUS_SOLUTION_CACHE", "solution_cache.json")
        self.cache_path = Path(cache_path) if cache_path else default
        self.max_entries = (
            max_entries
            if max_entries is not None
            else max(10, _env_int("AGENTBUS_SOLUTION_CACHE_MAX", 500))
        )
        self.ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else max(0.0, _env_float("AGENTBUS_SOLUTION_CACHE_TTL", 7 * 24 * 3600))
        )
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"version": 1, "entries": {}}
        self._load()

    # ------------------------------------------------------------------
    # Keying
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(
        *,
        message: str,
        files: list[str] | None = None,
        project: str = "",
        verify: list[str] | None = None,
        git_head: str = "",
        git_dirty: bool = False,
    ) -> str:
        payload = {
            "project": (project or "").strip(),
            "message": _norm_message(message),
            "files": _norm_files(files),
            "verify": sorted(str(v) for v in (verify or []) if v),
            "git_head": (git_head or "").strip(),
            "git_dirty": bool(git_dirty),
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def content_fingerprint(project_root: str | Path | None, files: list[str] | None) -> str:
        """Hash of current file contents; empty files list → stable empty fp."""
        if not files:
            return hashlib.sha256(b"").hexdigest()
        root = Path(project_root).resolve() if project_root else None
        h = hashlib.sha256()
        for rel in _norm_files(files):
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                path = (root / rel).resolve() if root else Path(rel).resolve()
                if root and root not in path.parents and path != root:
                    h.update(b"OUT\0")
                    continue
                if path.is_file():
                    h.update(path.read_bytes())
                else:
                    h.update(b"MISSING\0")
            except OSError:
                h.update(b"ERR\0")
            h.update(b"\n")
        return h.hexdigest()

    def key_for_task(
        self,
        task: Any,
        *,
        project_root: str | Path | None = None,
        git_head: str | None = None,
        git_dirty: bool | None = None,
    ) -> str:
        if git_head is None or git_dirty is None:
            head, dirty = git_repo_state(project_root)
            if git_head is None:
                git_head = head
            if git_dirty is None:
                git_dirty = dirty
        return self.make_key(
            message=getattr(task, "message", "") or "",
            files=list(getattr(task, "files", None) or []),
            project=str(getattr(task, "project", "") or ""),
            verify=list(getattr(task, "verify", None) or []),
            git_head=git_head or "",
            git_dirty=bool(git_dirty),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(
        self,
        task: Any,
        *,
        project_root: str | Path | None = None,
        require_content_match: bool = True,
    ) -> dict[str, Any] | None:
        """Return cache entry dict or None."""
        key = self.key_for_task(task, project_root=project_root)
        with self._lock:
            self._purge_unlocked()
            entry = self._data.get("entries", {}).get(key)
            if not entry:
                return None
            if self.ttl_seconds > 0:
                ts = float(entry.get("timestamp") or 0)
                if ts and (time.time() - ts) > self.ttl_seconds:
                    self._data["entries"].pop(key, None)
                    return None
            if require_content_match and entry.get("content_fp"):
                files = list(getattr(task, "files", None) or [])
                current = self.content_fingerprint(project_root, files)
                # At lookup time files may already equal cached snapshots
                # if previous apply ran; compare against stored pre-image fp.
                if current != entry.get("content_fp") and current != entry.get("post_content_fp"):
                    return None
            entry = dict(entry)
            entry["_key"] = key
            return entry

    def put(
        self,
        task: Any,
        solution: dict[str, Any],
        *,
        project_root: str | Path | None = None,
        file_snapshots: dict[str, str] | None = None,
        content_fp: str | None = None,
    ) -> str:
        """Store successful solution. Returns cache key."""
        head, dirty = git_repo_state(project_root)
        key = self.key_for_task(
            task, project_root=project_root, git_head=head, git_dirty=dirty
        )
        files = list(getattr(task, "files", None) or [])
        if content_fp is None and project_root is not None:
            content_fp = self.content_fingerprint(project_root, files)
        entry = {
            "timestamp": time.time(),
            "project": str(getattr(task, "project", "") or ""),
            "message": (getattr(task, "message", "") or "")[:2000],
            "files": _norm_files(files),
            "verify": list(getattr(task, "verify", None) or []),
            "git_head": head,
            "git_dirty": dirty,
            "content_fp": content_fp or "",
            "post_content_fp": "",
            "solution": {
                "method": solution.get("method") or "llm",
                "worker": solution.get("worker") or "",
                "skill": solution.get("skill") or "",
                "stdout": (solution.get("stdout") or "")[:8000],
                "commit": solution.get("commit") or "",
                "summary": (solution.get("summary") or "")[:2000],
                "result": solution.get("result"),
            },
            "file_snapshots": {},
        }
        if file_snapshots:
            # Cap snapshot size (~1.5MB total text)
            total = 0
            capped: dict[str, str] = {}
            for rel, text in file_snapshots.items():
                rel_n = _norm_rel(str(rel))
                if not _is_safe_rel(rel_n):
                    continue
                chunk = text if len(text) <= 400_000 else text[:400_000]
                total += len(chunk.encode("utf-8", errors="replace"))
                if total > 1_500_000:
                    break
                capped[rel_n] = chunk
            entry["file_snapshots"] = capped
            if project_root is not None and capped:
                entry["post_content_fp"] = self.content_fingerprint(
                    project_root, list(capped.keys())
                )
        with self._lock:
            self._data.setdefault("entries", {})[key] = entry
            self._evict_unlocked()
            self._save_unlocked()
        return key

    def invalidate_task(
        self, task: Any, *, project_root: str | Path | None = None
    ) -> bool:
        key = self.key_for_task(task, project_root=project_root)
        with self._lock:
            existed = key in self._data.get("entries", {})
            self._data.get("entries", {}).pop(key, None)
            if existed:
                self._save_unlocked()
            return existed

    def stats(self) -> dict[str, Any]:
        with self._lock:
            entries = self._data.get("entries", {})
            return {
                "path": str(self.cache_path),
                "count": len(entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
            }

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply_snapshots(
        self,
        entry: dict[str, Any],
        project_root: str | Path,
    ) -> dict[str, Any]:
        """Restore file_snapshots into project_root. Path-traversal safe."""
        root = Path(project_root).resolve()
        snapshots = entry.get("file_snapshots") or {}
        written: list[str] = []
        skipped: list[str] = []
        for rel, content in snapshots.items():
            rel_n = _norm_rel(str(rel))
            if not _is_safe_rel(rel_n):
                skipped.append(rel_n)
                continue
            path = (root / rel_n).resolve()
            if root not in path.parents and path != root:
                skipped.append(rel_n)
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # TD-023: atomic restore (tmp + replace). Cache snapshots and
                # project files may live on a cloud-synced drive; a partial
                # write would corrupt the restored file.
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, path)
                written.append(rel_n)
            except OSError:
                skipped.append(rel_n)
        return {"written": written, "skipped": skipped}

    def snapshot_files(
        self,
        project_root: str | Path,
        files: list[str] | None,
    ) -> dict[str, str]:
        """Read current file contents for cache storage."""
        root = Path(project_root).resolve()
        out: dict[str, str] = {}
        for rel in _norm_files(files):
            if ".." in Path(rel).parts:
                continue
            path = (root / rel).resolve()
            if root not in path.parents and path != root:
                continue
            try:
                if path.is_file():
                    out[rel] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            if not self.cache_path.is_file():
                return
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                self._data = raw
            elif isinstance(raw, dict):
                # plain key→entry map
                self._data = {"version": 1, "entries": raw}
        except (OSError, json.JSONDecodeError):
            self._data = {"version": 1, "entries": {}}

    def _save_unlocked(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            tmp.replace(self.cache_path)
        except OSError:
            pass

    def _purge_unlocked(self) -> None:
        if self.ttl_seconds <= 0:
            return
        now = time.time()
        entries = self._data.get("entries", {})
        dead = [
            k
            for k, v in entries.items()
            if (now - float(v.get("timestamp") or 0)) > self.ttl_seconds
        ]
        for k in dead:
            entries.pop(k, None)

    def _evict_unlocked(self) -> None:
        entries = self._data.get("entries", {})
        if len(entries) <= self.max_entries:
            return
        ordered = sorted(
            entries.items(),
            key=lambda kv: float(kv[1].get("timestamp") or 0),
        )
        for k, _ in ordered[: max(0, len(entries) - self.max_entries)]:
            entries.pop(k, None)


def force_refresh_requested(raw: dict | None, task: Any = None) -> bool:
    """True if task asks to bypass cache."""
    raw = raw or {}
    meta = {}
    if isinstance(raw.get("metadata"), dict):
        meta = raw["metadata"]
    elif task is not None and isinstance(getattr(task, "metadata", None), dict):
        meta = task.metadata  # type: ignore[assignment]
    for src in (raw, meta):
        for key in ("force_refresh", "skip_cache", "no_cache"):
            val = src.get(key)
            if val is True or str(val).strip().lower() in {"1", "true", "yes", "on"}:
                return True
    return False


# Process-wide singleton (path overridable via env)
GLOBAL_CACHE = SolutionCache()
