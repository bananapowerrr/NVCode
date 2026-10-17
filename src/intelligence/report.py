# -*- coding: utf-8 -*-
"""Nightly Report: сводка автопилота за сессию + бюджетная телеметрия."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Any
from utils.budget import GLOBAL_BUDGET

class NightlyReport:
    def __init__(self, log: Any = None, report_dir: str | Path | None = None):
        self.log=log; self.report_dir=Path(report_dir) if report_dir else None
        self.tasks_completed=0; self.tasks_failed=0; self.tasks_blocked=0; self.commits=0
        self.executor_stats:dict[str,dict[str,int]]={}; self.executor_notes:dict[str,str]={}
        self.provider_stats:dict[str,dict[str,int]]={}; self.provider_cooldowns:list[dict[str,Any]]=[]
    def record(self,status:str,executor:str="",attempts:int=0)->None:
        if status=="DONE": self.tasks_completed+=1
        elif status in ("ERROR","BLOCKED"):
            if status=="BLOCKED": self.tasks_blocked+=1
            else: self.tasks_failed+=1
        if executor:
            st=self.executor_stats.setdefault(executor,{"attempts":0,"success":0}); st["attempts"]+=1
            if status=="DONE": st["success"]+=1
    def record_provider(self,provider:str,status:str)->None:
        if not provider:return
        st=self.provider_stats.setdefault(provider,{"attempts":0,"success":0}); st["attempts"]+=1
        if status=="DONE": st["success"]+=1
    def set_provider_cooldowns(self,cooldowns:list[dict[str,Any]])->None: self.provider_cooldowns=list(cooldowns or [])
    def note_executor(self,executor:str,note:str)->None: self.executor_notes[executor]=note
    def _budget_lines(self)->list[str]:
        lines=["Limits:"]
        for name,d in GLOBAL_BUDGET.snapshot().items():
            parts=[]
            if d["day_limit"] is not None: parts.append(f"{d['day_calls']}/{d['day_limit']} сегодня")
            if d["month_token_limit"] is not None: parts.append(f"{d['month_tokens']/1_000_000:.2f}/{d['month_token_limit']/1_000_000:.2f}M ток/мес")
            if parts: lines.append(f"  {name:18} " + " · ".join(parts))
        if len(lines)==1: lines.append("  нет ограниченных воркеров")
        return lines
    def render(self)->str:
        lines=["="*30,"NIGHTLY REPORT","="*30,f"Time: {datetime.now():%Y-%m-%d %H:%M}",f"Tasks completed: {self.tasks_completed}",f"Tasks failed:    {self.tasks_failed}",f"Tasks blocked:   {self.tasks_blocked}",f"Commits:         {self.commits}","Executors:"]
        for name,st in sorted(self.executor_stats.items()): lines.append(f"  {name:12} {st['success']}/{st['attempts']}  {self.executor_notes.get(name,'')}")
        if self.provider_stats:
            lines.append("Providers:")
            for name,st in sorted(self.provider_stats.items()): lines.append(f"  {name:12} {st['success']}/{st['attempts']}")
        if self.provider_cooldowns:
            lines.append("Cooldowns:")
            for c in sorted(self.provider_cooldowns,key=lambda x:x.get("retry_in",0)): lines.append(f"  {c.get('key','')} [{c.get('status','')}] retry_in={c.get('retry_in',0)}s")
        lines.extend(self._budget_lines()); return "\n".join(lines)
    def save(self,suffix:str="")->Path|None:
        if not self.report_dir:
            if self.log is not None and hasattr(self.log,"write"): self.log.write("NIGHTLY REPORT\n"+self.render())
            return None
        self.report_dir.mkdir(parents=True,exist_ok=True)
        name=f"nightly_{suffix}_{datetime.now():%Y%m%d_%H%M%S}.txt" if suffix else f"nightly_{datetime.now():%Y%m%d_%H%M%S}.txt"
        # TD-025: atomic report write (tmp + os.replace)
        path=self.report_dir/name
        tmp=path.with_suffix(path.suffix+".tmp")
        tmp.write_text(self.render(),encoding="utf-8")
        tmp.replace(path); return path

