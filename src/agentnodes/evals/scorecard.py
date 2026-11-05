"""A7 eval scorecard — score agent/tool runs against golden tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class GoldenTask:
    id: str
    prompt: str
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    toolset: str | None = None
    budget_max_tokens: int | None = None
    tags: list[str] = field(default_factory=list)
    must_contain: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GoldenTask":
        return cls(
            id=str(d["id"]),
            prompt=str(d.get("prompt") or d.get("task") or ""),
            expected_tools=list(d.get("expected_tools") or []),
            forbidden_tools=list(d.get("forbidden_tools") or []),
            toolset=d.get("toolset"),
            budget_max_tokens=d.get("budget_max_tokens"),
            tags=list(d.get("tags") or []),
            must_contain=list(d.get("must_contain") or []),
        )


def load_golden_tasks(path: str | Path) -> list[GoldenTask]:
    p = Path(path)
    tasks: list[GoldenTask] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tasks.append(GoldenTask.from_dict(json.loads(line)))
    return tasks


@dataclass
class RunRecord:
    task_id: str
    tools_used: list[str] = field(default_factory=list)
    tokens_used: int = 0
    finished_reason: str = "stop"
    answer: str = ""
    evidence: str = ""  # tool outputs / transcript snippets for content match
    success: bool = True
    error: str | None = None
    # Trajectory (observation only — never scored; docs/phase0-trajectory-eval-design.md)
    iterations: int = 0
    tool_calls_made: int = 0
    tool_sequence: list[str] = field(default_factory=list)  # ordered, repeats kept


@dataclass
class TaskScore:
    task_id: str
    tool_recall: float
    tool_violations: int
    budget_ok: bool
    content_ok: bool
    completed: bool
    total: float
    notes: list[str] = field(default_factory=list)
    # Trajectory passthrough (observation only)
    iterations: int = 0
    tool_calls_made: int = 0
    tool_sequence: list[str] = field(default_factory=list)
    finished_reason: str = "stop"


def score_run(task: GoldenTask, run: RunRecord) -> TaskScore:
    """Score one run against one golden task (0..1 total)."""
    used = set(run.tools_used)
    expected = set(task.expected_tools)
    forbidden = set(task.forbidden_tools)

    # Tool recall: fraction of expected tools used (empty expected → 1.0).
    if not expected:
        recall = 1.0
    else:
        recall = len(used & expected) / len(expected)

    violations = len(used & forbidden)

    budget_ok = True
    if task.budget_max_tokens and run.tokens_used > task.budget_max_tokens:
        budget_ok = False

    answer = run.answer or ""
    haystack = f"{answer}\n{run.evidence or ''}".lower()
    if task.must_contain:
        ok_flags = []
        for s in task.must_contain:
            alts = [a for a in s.split("|") if a]
            ok_flags.append(any(a.lower() in haystack for a in alts))
        content_ok = all(ok_flags) if ok_flags else True
    else:
        content_ok = True

    completed = run.success and run.finished_reason in ("stop", "max_iter")

    # Weights: tools 0.4, violations 0.2, budget 0.15, content 0.15, completed 0.1
    total = (
        0.40 * recall
        + 0.20 * (1.0 if violations == 0 else 0.0)
        + 0.15 * (1.0 if budget_ok else 0.0)
        + 0.15 * (1.0 if content_ok else 0.0)
        + 0.10 * (1.0 if completed else 0.0)
    )

    notes: list[str] = []
    if violations:
        notes.append(f"forbidden_tools_hit={sorted(used & forbidden)}")
    if not budget_ok:
        notes.append(f"budget_exceeded tokens={run.tokens_used}>{task.budget_max_tokens}")
    if not content_ok:
        notes.append("missing_must_contain")
    if not completed:
        notes.append(f"not_completed reason={run.finished_reason}")

    return TaskScore(
        task_id=task.id,
        tool_recall=round(recall, 4),
        tool_violations=violations,
        budget_ok=budget_ok,
        content_ok=content_ok,
        completed=completed,
        total=round(total, 4),
        notes=notes,
        iterations=run.iterations,
        tool_calls_made=run.tool_calls_made,
        tool_sequence=list(run.tool_sequence),
        finished_reason=run.finished_reason,
    )


@dataclass
class Scorecard:
    scores: list[TaskScore]

    @property
    def mean(self) -> float:
        return round(sum(s.total for s in self.scores) / len(self.scores), 4) if self.scores else 0.0

    def pass_rate(self, threshold: float = 0.7) -> float:
        if not self.scores:
            return 0.0
        return round(sum(1 for s in self.scores if s.total >= threshold) / len(self.scores), 4)

    def to_dict(self) -> dict[str, Any]:
        steps = [s.iterations for s in self.scores]
        tool_calls = [s.tool_calls_made for s in self.scores]
        terminals: dict[str, int] = {}
        for s in self.scores:
            terminals[s.finished_reason] = terminals.get(s.finished_reason, 0) + 1
        return {
            "n": len(self.scores),
            "mean": self.mean,
            "pass_rate@0.7": self.pass_rate(0.7),
            "trajectory": {
                "steps_mean": round(sum(steps) / len(steps), 3) if steps else 0.0,
                "steps_p90": sorted(steps)[int(len(steps) * 0.9)] if steps else 0,
                "tool_calls_mean": round(sum(tool_calls) / len(tool_calls), 3) if tool_calls else 0.0,
                "terminal": terminals,
            },
            "tasks": [
                {
                    "id": s.task_id,
                    "total": s.total,
                    "tool_recall": s.tool_recall,
                    "violations": s.tool_violations,
                    "budget_ok": s.budget_ok,
                    "notes": s.notes,
                    "iterations": s.iterations,
                    "tool_calls_made": s.tool_calls_made,
                    "tool_sequence": s.tool_sequence,
                    "finished_reason": s.finished_reason,
                }
                for s in self.scores
            ],
        }


def build_scorecard(
    tasks: Iterable[GoldenTask], runs: Iterable[RunRecord]
) -> Scorecard:
    by_id = {r.task_id: r for r in runs}
    scores = [
        score_run(t, by_id[t.id])
        for t in tasks
        if t.id in by_id
    ]
    return Scorecard(scores=scores)


__all__ = [
    "GoldenTask",
    "RunRecord",
    "Scorecard",
    "TaskScore",
    "build_scorecard",
    "load_golden_tasks",
    "score_run",
]
