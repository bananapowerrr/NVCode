#!/usr/bin/env python3
"""Apply TD-032 fix to src/core/rp_llm.py (idempotent)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "core" / "rp_llm.py"
text = path.read_text(encoding="utf-8")
if "TD-032: files_payload must exist" in text:
    print("TD-032 already applied")
    sys.exit(0)

pat = re.compile(
    r"    def _llm_success_diff_preview\(self, task, proj, gitops, before_snapshot\) -> None:.*?"
    r"(?=\n    def _llm_capture_diff_payload)",
    re.S,
)
new_preview = '''    def _llm_success_diff_preview(self, task, proj, gitops, before_snapshot) -> None:
        """diff_preview: assess_risk + очередь превью-диффа для UI (fail-safe).

        TD-032: files_payload must exist *before* assess_risk so diff_lines
        reflects real content. Capture/queue runs first; risk uses that payload.
        """
        try:
            if not is_enabled("diff_preview"):
                raise ImportError("diff_preview disabled")
            from safety.diff_engine import queue_from_task_result
            from safety.diff_policy import assess_risk

            payload = getattr(task, "raw", None) or {}
            if not isinstance(payload, dict):
                payload = {}
            proot = getattr(proj, "root", None) if proj else None

            # 1) Build files_payload (queue side-effect), then score risk on it.
            files_payload: dict = {}
            queued = queue_from_task_result(payload, project_root=proot)
            if not queued and gitops is not None and before_snapshot is not None:
                files_payload = self._llm_capture_diff_payload(
                    payload, proot, gitops, before_snapshot, task
                ) or {}
            if not files_payload:
                # Fallback: metadata written by a prior capture / executor.
                try:
                    changes = (payload.get("metadata") or {}).get("file_changes") or []
                    for item in changes:
                        if not isinstance(item, dict):
                            continue
                        key = str(item.get("file") or item.get("path") or "")
                        if key:
                            files_payload[key] = item
                except Exception:
                    pass

            _diff_lines = 0
            for _fp in files_payload.values():
                if isinstance(_fp, dict):
                    _diff_lines += str(_fp.get("new") or "").count("\\n")
                else:
                    _diff_lines += str(_fp or "").count("\\n")

            _decision = assess_risk(
                files=list(getattr(task, "files", None) or []),
                complexity=int(getattr(task, "complexity", 3) or 3),
                diff_lines=_diff_lines,
                message=str(getattr(task, "message", "") or ""),
            )
            try:
                meta_d = dict(getattr(task, "metadata", None) or {})
                meta_d["diff_policy"] = _decision.to_dict()
                task.metadata = meta_d
                try:
                    from utils.pipeline_events import diff_policy as _pev_diff
                    _pev_diff(str(task.id), _decision.to_dict())
                except Exception:
                    pass
            except Exception:
                pass
            if _decision.action == "require_approval":
                # still queue for UI — never silent auto-apply on HIGH
                pass
        except Exception:
            pass

'''
# Fix double-escaped newlines in generated source: count("\\n") must become count("\n")
new_preview = new_preview.replace('.count("\\n")', '.count("\n")')

m = pat.search(text)
if not m:
    print("ABORT: preview method not found")
    sys.exit(1)
text = text[: m.start()] + new_preview + text[m.end() :]

text2, n = re.subn(
    r"(def _llm_capture_diff_payload\(self, payload, proot, gitops, before_snapshot,\s*task\) -> )None:",
    r"\1dict:",
    text,
    count=1,
)
if n != 1:
    print("ABORT: signature not updated", n)
    sys.exit(1)
text = text2

old = """                try:
                    task.raw = payload
                except Exception:
                    pass
        except Exception as _diff_exc:
            try:
                self.log.write(f"diff capture: {_diff_exc}")
            except Exception:
                pass

    def _llm_success_metrics_emit"""
new = """                try:
                    task.raw = payload
                except Exception:
                    pass
            return files_payload
        except Exception as _diff_exc:
            try:
                self.log.write(f"diff capture: {_diff_exc}")
            except Exception:
                pass
            return {}

    def _llm_success_metrics_emit"""
if old not in text:
    old_c = old.replace("\n", "\r\n")
    new_c = new.replace("\n", "\r\n")
    if old_c not in text:
        print("ABORT: capture tail not found")
        sys.exit(1)
    text = text.replace(old_c, new_c, 1)
else:
    text = text.replace(old, new, 1)

if "return files_payload" not in text or "TD-032: files_payload must exist" not in text:
    print("ABORT: post-check failed")
    sys.exit(1)

path.write_text(text, encoding="utf-8", newline="")
print("TD-032 applied to", path)
