# -*- coding: utf-8 -*-
"""Product-facing task outcome contract (feature completion).

Unifies worker / skill / verify / git change data already produced by runtime
into one shape for UI history, slash status, and future API.

Does NOT replace FSM or bus JSON — embeds as ``result.task_result`` / helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChangeSet:
    """Files touched by a task (skill, worker, or git)."""

    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    patch_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files)[:50],
            "insertions": int(self.insertions),
            "deletions": int(self.deletions),
            "patch_preview": (self.patch_preview or "")[:2000],
        }

    @classmethod
    def from_any(cls, data: Any) -> "ChangeSet":
        if data is None:
            return cls()
        if isinstance(data, ChangeSet):
            return data
        if isinstance(data, dict):
            files = data.get("files") or data.get("files_changed") or []
            if isinstance(files, dict):
                files = list(files.keys())
            return cls(
                files=[str(f) for f in (files or [])][:50],
                insertions=int(data.get("insertions") or data.get("added") or 0),
                deletions=int(data.get("deletions") or data.get("removed") or 0),
                patch_preview=str(data.get("patch") or data.get("patch_preview") or "")[:2000],
            )
        if isinstance(data, (list, tuple)):
            return cls(files=[str(f) for f in data][:50])
        return cls()


@dataclass
class SkillResult:
    """Normalized skill outcome (replaces ad-hoc dict shapes over time)."""

    success: bool
    name: str = ""
    message: str = ""
    error: str = ""
    changed: bool = False
    files: list[str] = field(default_factory=list)
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "ok": self.success,
            "name": self.name,
            "message": self.message,
            "error": self.error,
            "changed": self.changed,
            "files": list(self.files)[:50],
            "files_changed": list(self.files)[:50],
            "result": self.data,
        }

    @classmethod
    def from_execute(cls, name: str, raw: dict[str, Any] | None) -> "SkillResult":
        """Normalize any skill execute() dict into SkillResult (FC-05)."""
        raw = dict(raw or {})
        payload = raw.get("result")
        err = str(raw.get("error") or "")
        ok = bool(raw.get("success", raw.get("ok", False)))

        if isinstance(payload, dict):
            if payload.get("error") and not err:
                err = str(payload.get("error"))
            if "ok" in payload and not payload.get("ok"):
                ok = False
            if "success" in payload and not payload.get("success"):
                ok = False
            if payload.get("formatted") is False or payload.get("sorted") is False:
                if payload.get("error"):
                    ok = False

        files: list[str] = []
        changed = bool(raw.get("changed", False))
        msg = str(raw.get("message") or "")[:500]

        def _add_files(val: Any) -> None:
            nonlocal files
            if val is None:
                return
            if isinstance(val, str) and val.strip():
                files.append(val.strip())
            elif isinstance(val, dict):
                files.extend(str(k) for k in val.keys())
            elif isinstance(val, (list, tuple, set)):
                for x in val:
                    if isinstance(x, str):
                        files.append(x)
                    elif isinstance(x, dict) and x.get("file"):
                        files.append(str(x["file"]))
                    elif isinstance(x, dict) and x.get("path"):
                        files.append(str(x["path"]))

        if isinstance(payload, dict):
            for key in (
                "files", "files_changed", "files_fixed", "changed", "changed_files",
                "modified", "paths", "targets", "written", "touched",
            ):
                if key in payload:
                    _add_files(payload.get(key))
            for key in ("path", "target", "file"):
                v = payload.get(key)
                if isinstance(v, str) and v.strip():
                    _add_files(v)
            if any(payload.get(k) for k in ("renamed", "fixed", "formatted", "sorted", "replaced", "files_fixed")):
                changed = True
            if not msg:
                for key in ("message", "summary", "hint"):
                    if payload.get(key):
                        msg = str(payload.get(key))[:500]
                        break
            if not msg and payload.get("formatted"):
                msg = "formatted"
            if not msg and payload.get("renamed") is not None:
                msg = f"renamed={payload.get('renamed')}"
            if not msg and payload.get("fixed") is not None:
                msg = f"fixed={payload.get('fixed')}"
        elif payload is not None and not msg:
            msg = str(payload)[:500]

        seen: set[str] = set()
        uniq: list[str] = []
        for f in files:
            f = str(f).replace("\\", "/").strip()
            if f and f not in seen:
                seen.add(f)
                uniq.append(f)
        files = uniq[:50]
        if files:
            changed = True
        if err:
            ok = False
        if not msg and err:
            msg = err[:500]
        return cls(
            success=bool(ok and not err),
            name=name,
            message=msg,
            error=err,
            changed=changed,
            files=files,
            data=payload,
        )


@dataclass
class TaskResult:
    """Single product outcome after worker/skill + optional verification."""

    task_id: str = ""
    status: str = ""  # DONE | ERROR | DEFERRED | DEDUPED | ...
    ok: bool = False
    worker: str = ""
    skill: str = ""
    duration_sec: float = 0.0
    attempts: int = 1
    summary: str = ""
    error: str = ""
    changes: ChangeSet = field(default_factory=ChangeSet)
    verification: dict[str, Any] = field(default_factory=dict)
    timeline: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "ok": self.ok,
            "worker": self.worker,
            "skill": self.skill,
            "duration_sec": round(float(self.duration_sec or 0.0), 3),
            "attempts": int(self.attempts or 1),
            "summary": (self.summary or "")[:1000],
            "error": (self.error or "")[:2000],
            "changes": self.changes.to_dict(),
            "verification": dict(self.verification or {}),
            "timeline": list(self.timeline)[:40],
            "meta": dict(self.meta or {}),
        }

    def format_human(self) -> str:
        """UI-friendly multi-line summary."""
        lines: list[str] = []
        st = (self.status or ("DONE" if self.ok else "ERROR")).upper()
        mark = "✓" if self.ok else "✗"
        lines.append(f"{mark} {st}" + (f" · {self.worker}" if self.worker else "") + (f" · skill:{self.skill}" if self.skill else ""))
        if self.summary:
            lines.append(self.summary[:300])
        if self.error and not self.ok:
            try:
                from core.error_ux import humanize_error
                vsum = ""
                if isinstance(self.verification, dict):
                    vsum = str(self.verification.get("summary") or self.verification.get("reason") or "")
                lines.append(
                    humanize_error(
                        self.error,
                        worker=self.worker,
                        skill=self.skill,
                        verification_summary=vsum,
                    )
                )
            except Exception:
                lines.append(f"Error: {self.error[:400]}")
        if self.changes.files:
            n = len(self.changes.files)
            extra = ""
            if self.changes.insertions or self.changes.deletions:
                extra = f"  +{self.changes.insertions} -{self.changes.deletions}"
            lines.append(f"Files ({n}){extra}: " + ", ".join(self.changes.files[:8]))
        ver = self.verification or {}
        if ver:
            passed = ver.get("passed")
            if passed is True:
                lines.append("Verify: PASS")
            elif passed is False:
                reason = ver.get("reason") or ""
                lines.append(f"Verify: FAIL {reason}"[:200])
            checks = ver.get("checks") or ver.get("details") or []
            for c in list(checks)[:6]:
                if isinstance(c, dict):
                    name = c.get("name") or "?"
                    ok = c.get("passed")
                    if ok is None:
                        ok = str(c.get("status") or "").lower() == "passed"
                    lines.append(f"  {'✓' if ok else '✗'} {name}")
        if self.duration_sec:
            lines.append(f"Duration: {self.duration_sec:.1f}s · attempts={self.attempts}")
        for step in self.timeline[:12]:
            lines.append(f"  · {step}")
        return "\n".join(lines)


def _tr_prepare(task_row: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    """Normalize raw bus/history row into (row, result, _raw_result, meta)."""
    row = dict(task_row or {})
    _raw_result = row.get("result")
    if isinstance(_raw_result, dict):
        result = _raw_result
    elif isinstance(_raw_result, str) and _raw_result.strip():
        # FC-20: legacy result as plain string
        result = {"ok": True, "summary": _raw_result.strip(), "stdout": _raw_result.strip()}
    else:
        result = {}
    # legacy top-level aliases
    if not row.get("message") and row.get("msg"):
        row["message"] = row.get("msg")
    if not row.get("message") and row.get("text"):
        row["message"] = row.get("text")
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if not meta and isinstance(result.get("metadata"), dict):
        meta = result["metadata"]


def _tr_normalize_worker_result(result: dict[str, Any], _raw_result: Any) -> dict[str, Any]:
    """FC-14: normalize worker-shaped result through WorkerResult."""
    # FC-14: normalize worker-shaped result through WorkerResult
    try:
        if result and (
            "stdout" in result
            or "stderr" in result
            or "files_changed" in result
            or "timed_out" in result
            or "latency" in result
        ):
            from core.worker_api import WorkerResult
            wr = WorkerResult.from_exec(result)
            # merge back without dropping verification / change_set
            merged = wr.to_task_result_fields()
            for k, v in result.items():
                if k not in merged or merged.get(k) in (None, "", [], {}):
                    merged[k] = v
            # preserve nested product fields
            for keep in ("verification", "change_set", "skill", "skill_result", "git", "summary"):
                if keep in result and keep not in merged:
                    merged[keep] = result[keep]
            result = merged
            if wr.worker and not result.get("worker"):
                result["worker"] = wr.worker
            # FC-20: only force ok from WorkerResult when explicit or timeout
            if wr.timed_out:
                result["ok"] = False
                result["timed_out"] = True
                if not result.get("error"):
                    result["error"] = "timeout"
            elif "ok" not in _raw_result if isinstance(_raw_result, dict) else True:
                # preserve ambiguous legacy: don't invent ok=False from missing field
                if isinstance(_raw_result, dict) and "ok" not in _raw_result and "success" not in _raw_result:
                    result.pop("ok", None)
    except Exception:
        pass


def _tr_status(row: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
    """Derive (ok, status); timeout or explicit ok=False wins over stale DONE."""
    status = str(
        row.get("status")
        or row.get("_state")
        or result.get("status")
        or ""
    ).upper()
    # FC-20: legacy status aliases
    if status in ("DONE", "SUCCESS", "OK", "COMPLETED"):
        ok = True
        status = "DONE"
    elif status in ("ERROR", "ERRORS", "FAILED", "FAIL", "FAILURE"):
        ok = False
        status = "ERROR"
    elif status in ("DEFERRED", "DEFER"):
        ok = False
        status = "DEFERRED"
    elif status in ("PENDING", "QUEUED", "INCOMING", "CLAIMED", "PROCESSING", "RUNNING"):
        ok = False  # not terminal success
        # keep status as-is for UI
    else:
        # no status: derive from result flags (legacy: success/ok)
        if "ok" in result:
            ok = bool(result.get("ok"))
        elif "success" in result:
            ok = bool(result.get("success"))
        else:
            ok = False
        if ok:
            status = status or "DONE"
        elif result or row.get("error"):
            status = status or "ERROR"
        else:
            status = status or "PENDING"
    # FC-14: timeout / explicit ok=False always wins over stale DONE
    if result.get("timed_out") or str(result.get("error") or "").lower() == "timeout":
        ok = False
        if status in ("DONE", "SUCCESS", ""):
            status = "ERROR"
        if not result.get("error"):
            result["error"] = "timeout"
    elif result.get("ok") is False:
        ok = False
        if status in ("DONE", "SUCCESS"):
            status = "ERROR"


def _tr_verification(result: dict[str, Any], meta: dict[str, Any], ok: bool, status: str) -> tuple[dict[str, Any], bool, str]:
    """FC-11/FC-02: structured verification report; FAIL cannot be presented as DONE."""
    # verification (FC-11: prefer structured engine report from metadata)
    ver = (
        result.get("verification")
        or meta.get("verification_report")
        or meta.get("verification")
        or result.get("verify")
        or {}
    )
    if hasattr(ver, "to_dict"):
        try:
            ver = ver.to_dict()
        except Exception:
            ver = {}
    if not isinstance(ver, dict):
        ver = {}
    # Product invariant: verification FAIL cannot be presented as DONE
    if ver and ver.get("passed") is False:
        ok = False
        if status in ("DONE", "SUCCESS", ""):
            status = "ERROR"
    # FC-02/11: prefer engine summary_line if present
    if ver and not ver.get("summary") and "passed" in ver:
        try:
            from core.verification_engine import VerificationReport, CheckResult
            checks = []
            for c in (ver.get("checks") or ver.get("details") or []):
                if not isinstance(c, dict):
                    continue
                checks.append(
                    CheckResult(
                        name=str(c.get("name") or "?"),
                        passed=bool(c.get("passed") if "passed" in c else str(c.get("status")) == "passed"),
                        detail=str(c.get("detail") or "")[:500],
                        code=c.get("code") if c.get("code") is not None else c.get("exit_code"),
                        duration_sec=float(c.get("duration_sec") or 0),
                    )
                )
            rep = VerificationReport(
                passed=bool(ver.get("passed")),
                checks=checks,
                reason=str(ver.get("reason") or ""),
                risk=str(ver.get("risk") or "low"),
                duration_sec=float(ver.get("duration_sec") or 0),
            )
            ver = rep.to_dict()
        except Exception:
            pass


def _tr_changes(result: dict[str, Any], meta: dict[str, Any]) -> ChangeSet:
    """FC-04: collect files / insertions / deletions into a ChangeSet."""
    # changes (FC-04: git / file_changes / worker fields)
    files: list[str] = []
    for key in ("files_changed", "files", "changed_files"):
        v = result.get(key) or meta.get(key)
        if isinstance(v, list):
            files = [str(x) for x in v]
            break
        if isinstance(v, dict):
            files = list(v.keys())
            break
    fc = meta.get("file_changes") or result.get("file_changes")
    if not files and isinstance(fc, list):
        for item in fc:
            if isinstance(item, dict) and item.get("file"):
                files.append(str(item["file"]))
            elif isinstance(item, str):
                files.append(item)
    if not files and isinstance(fc, dict):
        files = list(fc.keys())
    git = result.get("git") if isinstance(result.get("git"), dict) else {}
    cs = result.get("change_set") or meta.get("change_set") or git.get("change_set") or {}
    if isinstance(cs, dict) and cs.get("files") and not files:
        files = [str(x) for x in (cs.get("files") or [])]
    insertions = int(
        (cs.get("insertions") if isinstance(cs, dict) else 0)
        or git.get("insertions")
        or result.get("insertions")
        or 0
    )
    deletions = int(
        (cs.get("deletions") if isinstance(cs, dict) else 0)
        or git.get("deletions")
        or result.get("deletions")
        or 0
    )
    if (not insertions and not deletions) and isinstance(fc, list):
        for item in fc:
            if not isinstance(item, dict):
                continue
            new_c = str(item.get("new") or "")
            old_c = str(item.get("original") or "")
            nl, ol = new_c.count(chr(10)), old_c.count(chr(10))
            if nl > ol:
                insertions += nl - ol
            elif ol > nl:
                deletions += ol - nl
    changes = ChangeSet(
        files=files[:50],
        insertions=int(insertions),
        deletions=int(deletions),
        patch_preview=str(
            result.get("patch")
            or (cs.get("patch_preview") if isinstance(cs, dict) else "")
            or ""
        )[:2000],
    )


def _tr_timeline(meta: dict[str, Any], result: dict[str, Any], tid: str) -> list[str]:
    """Best-effort timeline from trace metadata (never raises)."""
    timeline: list[str] = []
    tr = meta.get("trace") or result.get("trace") or meta.get("task_trace") or result.get("task_trace")
    try:
        from utils.task_trace import timeline_from_any, GLOBAL_TRACES
        timeline = timeline_from_any(tr)
        if not timeline and tid:
            active = GLOBAL_TRACES.get(str(tid))
            if active is None:
                finished = getattr(GLOBAL_TRACES, "_finished", None) or {}
                active = finished.get(str(tid))
            if active is not None:
                timeline = active.timeline_lines()
    except Exception:
        if isinstance(tr, list):
            timeline = [str(x) for x in tr][:40]
        elif isinstance(tr, dict) and isinstance(tr.get("events"), list):
            timeline = [str(x) for x in tr["events"]][:40]


def build_task_result(task_row: dict[str, Any] | None) -> TaskResult:
    """Build TaskResult from bus JSON / history row (best-effort, never raises)."""
    row, result, _raw_result, meta = _tr_prepare(task_row)
    result = _tr_normalize_worker_result(result, _raw_result)
    tid = str(row.get("id") or result.get("task_id") or "")
    ok, status = _tr_status(row, result)

    worker = str(result.get("worker") or meta.get("worker") or "")
    skill = str(result.get("skill") or meta.get("skill") or "")
    if not skill and isinstance(result.get("skill_result"), dict):
        skill = str(result["skill_result"].get("name") or "")

    ver, ok, status = _tr_verification(result, meta, ok, status)
    changes = _tr_changes(result, meta)

    summary = ""
    if isinstance(ver, dict) and ver.get("summary"):
        summary = str(ver.get("summary"))[:500]
    if not summary:
        for key in ("summary", "message", "stdout"):
            if result.get(key):
                summary = str(result[key])[:500]
                break
    if not summary and skill:
        summary = f"skill:{skill}"
    err = str(result.get("error") or result.get("stderr") or row.get("error") or "")[:2000]

    duration = float(result.get("latency") or result.get("duration") or meta.get("duration") or 0)
    attempts = int(row.get("attempts") or meta.get("attempts") or result.get("attempts") or 1)

    timeline = _tr_timeline(meta, result, tid)

    # embedded task_result wins
    embedded = result.get("task_result") or row.get("task_result")
    if isinstance(embedded, dict) and embedded.get("status"):
        try:
            ch = ChangeSet.from_any(embedded.get("changes"))
            return TaskResult(
                task_id=str(embedded.get("task_id") or tid),
                status=str(embedded.get("status") or status),
                ok=bool(embedded.get("ok")),
                worker=str(embedded.get("worker") or worker),
                skill=str(embedded.get("skill") or skill),
                duration_sec=float(embedded.get("duration_sec") or duration),
                attempts=int(embedded.get("attempts") or attempts),
                summary=str(embedded.get("summary") or summary)[:1000],
                error=str(embedded.get("error") or err)[:2000],
                changes=ch,
                verification=dict(embedded.get("verification") or ver),
                timeline=list(embedded.get("timeline") or timeline)[:40],
                meta=dict(embedded.get("meta") or {}),
            )
        except Exception:
            pass

    return TaskResult(
        task_id=tid,
        status=status or ("DONE" if ok else "ERROR"),
        ok=ok,
        worker=worker,
        skill=skill,
        duration_sec=duration,
        attempts=attempts,
        summary=summary,
        error=err,
        changes=changes,
        verification=ver,
        timeline=timeline,
        meta={},
    )


def history_card_lines(row: dict[str, Any] | None) -> dict[str, str]:
    """FC-03/FC-10: pure summary bits for history UI (no toolkit deps).

    Keys:
      prompt, meta, verify, files, summary, error, lifecycle
    """
    out = {
        "prompt": "",
        "meta": "",
        "verify": "",
        "files": "",
        "summary": "",
        "error": "",
        "lifecycle": "",
        "retry": "",
        "trace": "",
    }
    try:
        row = dict(row or {})
        tr = build_task_result(row)
        prompt = str(row.get("message") or "").replace("\n", " ").strip()
        out["prompt"] = prompt[:160]

        bits: list[str] = []
        if tr.worker:
            bits.append(tr.worker)
        if tr.skill:
            bits.append(f"skill:{tr.skill}")
        if tr.duration_sec:
            bits.append(f"{tr.duration_sec:.0f}s")
        if tr.attempts and tr.attempts > 1:
            bits.append(f"×{tr.attempts}")
        out["meta"] = " · ".join(bits)

        ver = tr.verification or {}
        if ver.get("summary"):
            out["verify"] = str(ver["summary"])[:120]
        elif "passed" in ver:
            out["verify"] = (
                "Verify PASS" if ver.get("passed") else f"Verify FAIL {ver.get('reason') or ''}"
            )[:120]

        if tr.changes.files:
            n = len(tr.changes.files)
            extra = ""
            if tr.changes.insertions or tr.changes.deletions:
                extra = f" +{tr.changes.insertions} -{tr.changes.deletions}"
            out["files"] = (
                f"{n} file(s){extra}: "
                + ", ".join(tr.changes.files[:3])
                + ("…" if n > 3 else "")
            )

        out["summary"] = (tr.summary or "")[:200]
        if tr.error and not tr.ok:
            out["error"] = str(tr.error)[:160]

        # lifecycle timestamps (best-effort from existing JSON, no new storage)
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        created = meta.get("created_at") or row.get("created_at") or ""
        started = meta.get("started_at") or result.get("started_at") or ""
        finished = meta.get("finished_at") or result.get("finished_at") or ""
        life_bits: list[str] = []
        if created:
            life_bits.append(f"created={str(created)[:19]}")
        if started:
            life_bits.append(f"start={str(started)[:19]}")
        if finished:
            life_bits.append(f"end={str(finished)[:19]}")
        if tr.duration_sec and not any("s" in b for b in bits if b.endswith("s")):
            life_bits.append(f"{tr.duration_sec:.1f}s")
        if tr.attempts:
            life_bits.append(f"attempts={tr.attempts}")
        out["lifecycle"] = " · ".join(life_bits)[:180]
        try:
            from core.error_ux import retry_status_from_row
            out["retry"] = retry_status_from_row(row)
        except Exception:
            out["retry"] = ""
        if tr.timeline:
            out["trace"] = " → ".join(tr.timeline[:5])
    except Exception:
        pass
    return out


def format_task_report(row: dict[str, Any] | None) -> str:
    """Human multi-section report for one task row (never raises)."""
    row = dict(row or {})
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    try:
        tr = build_task_result(row)
    except Exception:
        tr = None

    if tr is not None:
        task_id = tr.task_id or str(row.get("id") or "")
        status = (tr.status or "").upper() or ("DONE" if tr.ok else "ERROR")
        request = str(row.get("message") or row.get("msg") or tr.summary or "")
        worker = " · ".join(
            x for x in (tr.worker, f"skill:{tr.skill}" if tr.skill else "") if x
        )
        files = list(tr.changes.files or [])
        verification = dict(tr.verification or {})
        error = tr.error or str(row.get("error") or "")
    else:
        task_id = str(row.get("id") or "")
        status = str(row.get("status") or "").upper()
        request = str(row.get("message") or "")
        worker = str(meta.get("worker") or "")
        files = list(meta.get("files_changed") or [])
        vr = meta.get("verification_report")
        verification = dict(vr) if isinstance(vr, dict) else {}
        error = str(row.get("error") or "")

    lines = [f"Task #{task_id}  RESULT: {status}"]
    if request:
        lines.append(f"REQUEST  : {request}")
    if worker:
        lines.append(f"WORKER   : {worker}")
    lines.append("CHANGES  : " + (", ".join(files) if files else "(none)"))
    if verification:
        parts: list[str] = []
        for key in ("syntax", "tests"):
            if key in verification:
                parts.append(f"{key}={verification[key]}")
        if verification.get("passed") is not None:
            parts.append(f"passed={verification['passed']}")
        if verification.get("summary"):
            parts.append(f"summary={verification['summary']}")
        lines.append("VERIFY   : " + " · ".join(parts))
    else:
        lines.append("VERIFY   : (no verification report)")
    if error:
        lines.append(f"ERROR    : {error}")
    return "\n".join(lines)


def history_detail_text(row: dict[str, Any] | None) -> str:
    """FC-10: human product detail block for history details window."""
    row = dict(row or {})
    lines: list[str] = []
    try:
        tr = build_task_result(row)
        card = history_card_lines(row)
        try:
            header = format_task_report(row).splitlines()[0]
        except Exception:
            header = f"{'✓' if tr.ok else '✗'} {tr.task_id or row.get('id') or ''}"
        lines.append(header)
        if card.get("prompt"):
            lines.append(f"Запрос: {card['prompt']}")
        if tr.worker or tr.skill:
            lines.append(
                "Исполнитель: "
                + " · ".join(x for x in (tr.worker, f"skill:{tr.skill}" if tr.skill else "") if x)
            )
        if card.get("lifecycle"):
            lines.append(f"Время: {card['lifecycle']}")
        if card.get("retry"):
            lines.append(card["retry"])
        if tr.timeline:
            lines.append("Ход:")
            for step in tr.timeline[:12]:
                lines.append(f"  · {step}")
        if card.get("files"):
            lines.append(f"Изменения: {card['files']}")
        if card.get("verify"):
            lines.append(card["verify"])
        if tr.summary:
            lines.append(f"Итог: {tr.summary[:300]}")
        if tr.error and not tr.ok:
            try:
                from core.error_ux import humanize_error
                vsum = ""
                if isinstance(tr.verification, dict):
                    vsum = str(tr.verification.get("summary") or "")
                lines.append(
                    humanize_error(
                        tr.error,
                        worker=tr.worker,
                        skill=tr.skill,
                        verification_summary=vsum,
                    )
                )
            except Exception:
                lines.append(f"Ошибка: {tr.error[:400]}")
        human = tr.format_human()
        if human and human not in "\n".join(lines):
            lines.append("")
            lines.append(human)
        return "\n".join(lines).strip()
    except Exception:
        try:
            from ui.result_text import extract_result_text
            return extract_result_text(row)
        except Exception:
            return str(row.get("message") or row.get("id") or "task")
