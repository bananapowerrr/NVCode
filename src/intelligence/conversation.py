# -*- coding: utf-8 -*-
"""Multi-turn conversation sessions over file-bus (not a parallel executor).

Each session is persisted under .agentbus/sessions/<id>.json.
Task JSON carries metadata.session_id + metadata.conversation_tail so
workers still receive a single message, enriched with prior turns.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _sessions_dir() -> Path:
    raw = (os.getenv("AGENTBUS_SESSIONS_DIR") or "").strip()
    if raw:
        return Path(raw)
    try:
        from core.config import BASE_DIR
        return BASE_DIR / ".agentbus" / "sessions"
    except Exception:
        return Path(".agentbus") / "sessions"


@dataclass
class Turn:
    role: str  # user | assistant | system
    content: str
    timestamp: float = field(default_factory=time.time)
    files: list[str] = field(default_factory=list)
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "files": list(self.files),
            "task_id": self.task_id,
        }



@dataclass
class Conversation:
    """One chat session bound to a project."""

    session_id: str
    project: str = ""
    messages: list[Turn] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_message(
        self,
        role: str,
        content: str,
        *,
        files: list[str] | None = None,
        task_id: str = "",
    ) -> Turn:
        turn = Turn(
            role=role,
            content=(content or "")[:20000],
            files=list(files or []),
            task_id=task_id or "",
        )
        self.messages.append(turn)
        for f in turn.files:
            if f and f not in self.files_touched:
                self.files_touched.append(f)
        self.updated_at = time.time()
        return turn

    def get_context(self, max_turns: int = 20, max_chars: int = 12000) -> list[dict[str, str]]:
        """Recent turns for prompt injection (role/content only)."""
        tail = self.messages[-max_turns:]
        out: list[dict[str, str]] = []
        total = 0
        for t in reversed(tail):
            chunk = f"{t.role}: {t.content}"
            if total + len(chunk) > max_chars and out:
                break
            out.append({"role": t.role, "content": t.content})
            total += len(chunk)
        out.reverse()
        return out

    def format_for_worker(self, max_turns: int = 12) -> str:
        """Compact transcript appended to worker message."""
        turns = self.get_context(max_turns=max_turns)
        if not turns:
            return ""
        lines = ["CONVERSATION (recent):"]
        for t in turns:
            role = t.get("role", "?")
            content = (t.get("content") or "").strip().replace("\n", " ")
            lines.append(f"  {role}: {content[:500]}")
        return "\n".join(lines)

    def compact(self, keep_last: int = 8, *, use_llm: bool = True) -> str:
        """Drop middle turns; optionally summarize via local meta model.

        Pending user turns (task_id set, no later assistant with same task_id)
        are always preserved in the tail.
        """
        if len(self.messages) <= keep_last + 2:
            return "already compact"
        # pending: user messages waiting for assistant
        answered = {t.task_id for t in self.messages if t.role == "assistant" and t.task_id}
        pending = [
            t for t in self.messages
            if t.role == "user" and t.task_id and t.task_id not in answered
        ]
        head = self.messages[:1]
        tail = self.messages[-keep_last:]
        # ensure pending in tail
        for p in pending:
            if p not in tail:
                tail = tail + [p]
        old = [m for m in self.messages[1 : len(self.messages) - keep_last] if m not in pending]
        dropped = len(old)
        summary_text = f"[compacted {dropped} earlier turns; project={self.project}]"
        if use_llm and old:
            try:
                summary_text = self._summarize_turns(old) or summary_text
            except Exception:
                pass
        summary = Turn(role="system", content=summary_text[:4000])
        self.messages = head + [summary] + tail
        self.updated_at = time.time()
        return f"compacted {dropped} turns"

    def _summarize_turns(self, turns: list[Turn]) -> str:
        """Best-effort local summary via ollama meta model; heuristic fallback."""
        blob = "\n".join(f"{t.role}: {t.content[:300]}" for t in turns[-15:])
        # try meta / ollama
        try:
            import urllib.request
            model = (os.getenv("AGENTBUS_META_MODEL") or "qwen2.5:1.5b-instruct").strip()
            payload = json.dumps({
                "model": model,
                "prompt": (
                    "Summarize this coding chat in 5 short bullet points (RU or EN). "
                    "Keep file names and decisions.\n\n" + blob[:6000]
                ),
                "stream": False,
                "options": {"num_predict": 200},
            }).encode()
            req = urllib.request.Request(
                (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434") + "/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                text = str(data.get("response") or "").strip()
                if text:
                    return "Summary:\n" + text[:2000]
        except Exception:
            pass
        # heuristic: files, decisions, open questions
        import re
        files = re.findall(r"[\w./-]+\.\w{1,8}", blob)
        uniq: list[str] = []
        for f in files:
            if f not in uniq and not f.startswith("http"):
                uniq.append(f)
        decisions = []
        for t in turns:
            c = (t.content or "").strip()
            low = c.lower()
            if any(k in low for k in ("decided", "решили", "выбрали", "will use", "используем", "fixed", "исправил")):
                decisions.append(c[:120])
        bullets = [
            f"turns={len(turns)}",
            f"files={', '.join(uniq[:12]) or '—'}",
        ]
        if decisions:
            bullets.append("decisions: " + " | ".join(decisions[:4]))
        bullets.append("recent: " + " | ".join(
            (t.content or "")[:70].replace("\n", " ") for t in turns[-3:]
        ))
        return "Summary (heuristic):\n- " + "\n- ".join(bullets)

    def export_markdown(self) -> str:
        """Human-readable history for save/export."""
        lines_out = [
            f"# Session `{self.session_id}`",
            f"project: {self.project or '—'}",
            f"files_touched: {', '.join(self.files_touched[:30]) or '—'}",
            "",
        ]
        for t in self.messages:
            role = (t.role or "?").upper()
            body = (t.content or "").strip()
            lines_out.append(f"## {role}")
            lines_out.append(body)
            lines_out.append("")
        return chr(10).join(lines_out)

    def clear(self, *, keep_system: bool = True) -> int:
        """Drop turns (optionally keep system summaries). Returns removed count."""
        before = len(self.messages)
        if keep_system:
            self.messages = [m for m in self.messages if m.role == "system"]
        else:
            self.messages = []
        self.updated_at = time.time()
        return before - len(self.messages)

    def maybe_auto_compact(self, threshold: int = 24, keep_last: int = 8) -> str | None:
        """Compact when history grows past *threshold*."""
        if len(self.messages) < threshold:
            return None
        return self.compact(keep_last=keep_last, use_llm=True)


    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project": self.project,
            "messages": [m.to_dict() for m in self.messages],
            "files_touched": list(self.files_touched),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conversation":
        conv = cls(
            session_id=str(data.get("session_id") or uuid.uuid4().hex[:12]),
            project=str(data.get("project") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            files_touched=list(data.get("files_touched") or []),
        )
        for m in data.get("messages") or []:
            if not isinstance(m, dict):
                continue
            conv.messages.append(
                Turn(
                    role=str(m.get("role") or "user"),
                    content=str(m.get("content") or ""),
                    timestamp=float(m.get("timestamp") or time.time()),
                    files=list(m.get("files") or []),
                    task_id=str(m.get("task_id") or ""),
                )
            )
        return conv


class ConversationStore:
    """Thread-safe session persistence."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _sessions_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._active: dict[str, Conversation] = {}

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
        return self.root / f"{safe}.json"

    def create(self, project: str = "") -> Conversation:
        sid = uuid.uuid4().hex[:12]
        conv = Conversation(session_id=sid, project=project)
        with self._lock:
            self._active[sid] = conv
            self._save(conv)
        return conv

    def get(self, session_id: str) -> Conversation | None:
        if not session_id:
            return None
        with self._lock:
            if session_id in self._active:
                return self._active[session_id]
            path = self._path(session_id)
            if not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                conv = Conversation.from_dict(data if isinstance(data, dict) else {})
                self._active[session_id] = conv
                return conv
            except Exception:
                return None

    def get_or_create(self, session_id: str | None, project: str = "") -> Conversation:
        if session_id:
            existing = self.get(session_id)
            if existing:
                if project and not existing.project:
                    existing.project = project
                return existing
        return self.create(project=project)

    def save(self, conv: Conversation) -> None:
        with self._lock:
            self._active[conv.session_id] = conv
            self._save(conv)

    def _save(self, conv: Conversation) -> None:
        path = self._path(conv.session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # TD-024: atomic session write (tmp + os.replace)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(conv.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._active.pop(session_id, None)
            path = self._path(session_id)
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass


GLOBAL_CONVERSATIONS = ConversationStore()

