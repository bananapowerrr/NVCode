# -*- coding: utf-8 -*-
"""Task Graph for planner / autopilot (Stage 11).

Nodes are subtasks with optional depends_on. Scheduler yields ready nodes
when dependencies are DONE. No uncontrolled recursion — max_nodes cap.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GraphNode:
    id: str
    message: str
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    complexity: int = 3
    status: str = "PENDING"  # PENDING|READY|DONE|ERROR|BLOCKED|SUPERSEDED|OBSOLETE|REPLACED|CANCELLED
    meta: dict[str, Any] = field(default_factory=dict)

    def to_task_dict(self, *, parent_id: str = "", channel: str = "gpt", project: str = "") -> dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "files": list(self.files),
            "channel": channel,
            "project": project,
            "complexity": self.complexity,
            "status": "PENDING",
            "metadata": {
                "source": "task_graph",
                "graph_node_id": self.id,
                "parent_id": parent_id,
                "depends_on": list(self.depends_on),
                **self.meta,
            },
        }


@dataclass
class TaskGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    max_nodes: int = 12

    def add(
        self,
        message: str,
        *,
        files: list[str] | None = None,
        depends_on: list[str] | None = None,
        complexity: int = 3,
        node_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        if len(self.nodes) >= self.max_nodes:
            raise ValueError(f"task graph full (max_nodes={self.max_nodes})")
        raw = f"{message}:{','.join(files or [])}:{','.join(depends_on or [])}"
        nid = node_id or ("g_" + hashlib.md5(raw.encode()).hexdigest()[:10])
        if nid in self.nodes:
            nid = nid + f"_{len(self.nodes)}"
        self.nodes[nid] = GraphNode(
            id=nid,
            message=message,
            files=list(files or []),
            depends_on=list(depends_on or []),
            complexity=int(complexity),
            meta=dict(meta or {}),
        )
        return nid

    def mark(self, node_id: str, status: str) -> None:
        n = self.nodes.get(node_id)
        if n:
            n.status = status

    def ready(self) -> list[GraphNode]:
        """Nodes with all deps DONE (or no deps); re-evaluates BLOCKED nodes.

        FC-26: SUPERSEDED/OBSOLETE/REPLACED/CANCELLED are never eligible.
        """
        _closed = frozenset({
            "DONE", "ERROR",
            "SUPERSEDED", "OBSOLETE", "REPLACED", "CANCELLED",
        })
        out: list[GraphNode] = []
        for n in self.nodes.values():
            if n.status in _closed:
                continue
            deps_ok = True
            for d in n.depends_on:
                dep = self.nodes.get(d)
                if dep is None or dep.status != "DONE":
                    deps_ok = False
                    break
            if deps_ok:
                n.status = "READY"
                out.append(n)
            else:
                if n.status not in ("IN_PROGRESS",):
                    n.status = "BLOCKED"
        return out

    def all_finished(self) -> bool:
        """FC-26: inactive nodes count as closed."""
        _closed = frozenset({
            "DONE", "ERROR",
            "SUPERSEDED", "OBSOLETE", "REPLACED", "CANCELLED",
        })
        return all(n.status in _closed for n in self.nodes.values())


    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {
                k: {
                    "id": v.id,
                    "message": v.message,
                    "files": v.files,
                    "depends_on": v.depends_on,
                    "complexity": v.complexity,
                    "status": v.status,
                }
                for k, v in self.nodes.items()
            }
        }

    @classmethod
    def from_goal_steps(cls, steps: list[str], *, files: list[str] | None = None, max_nodes: int = 12) -> "TaskGraph":
        """Linear chain: each step depends on previous."""
        g = cls(max_nodes=max_nodes)
        prev: str | None = None
        for i, step in enumerate(steps[:max_nodes]):
            deps = [prev] if prev else []
            nid = g.add(step, files=files, depends_on=deps, complexity=3 if i else 2)
            prev = nid
        return g


def emit_ready_to_bus(
    graph: TaskGraph,
    *,
    bus_root: str | Path | None = None,
    channel: str = "autopilot",
    project: str = "",
    parent_id: str = "",
    max_emit: int = 4,
) -> list[str]:
    """Write READY nodes as incoming JSON tasks. Returns task ids emitted."""
    from pathlib import Path as P
    from core.bus import FileBus

    root = P(bus_root) if bus_root else P(".")
    bus = FileBus(root, (channel,))
    emitted: list[str] = []
    for node in graph.ready()[:max_emit]:
        payload = node.to_task_dict(parent_id=parent_id, channel=channel, project=project)
        bus.write(
            channel,
            "incoming",
            f"{node.id}.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        node.status = "PENDING"  # in queue; graph tracks via mark when done
        # keep READY→emitted as processing marker in meta
        node.meta["emitted"] = True
        emitted.append(node.id)
    return emitted


# late import for type hint
