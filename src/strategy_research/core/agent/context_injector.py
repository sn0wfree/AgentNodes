"""ContextInjector protocol — pluggable context injection for AgentLoop.

Eliminates the 4 hardcoded injection points in loop.py by defining a
protocol that injectors implement.  Injectors are called at well-defined
boundaries:

  inject_pre_run   – before the loop starts, can modify the task string
  inject_per_iter  – at the start of each iteration, can append messages
  inject_post_resp – after LLM response (no tool calls), can force continue

Three concrete injectors are provided for the legacy injection points:

  GoalContextInjector       – prepends <current-research-goal> block
  TodosInjector             – appends <current-todos> block (deduped)
  GoalContinuationInjector  – injects continuation prompt when goal is active

The compaction injection is handled by DefaultCompactionStep (not here)
since it's already pluggable via the strategy's step system.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, Sequence, cast, runtime_checkable

if TYPE_CHECKING:
    from .loop import AgentLoop, LoopResult

logger = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class ContextInjector(Protocol):
    """Injects context into the agent loop at well-defined boundaries.

    Implementations should be lightweight and never raise — failures
    are logged and swallowed to preserve loop resilience.
    """

    @property
    def name(self) -> str:
        """Short name for tracing / debugging."""
        ...

    @property
    def order(self) -> int:
        """Execution order.  Lower runs first.

        -100  = pre-run goal context (before task enters system prompt)
           0  = per-iteration (compaction, todos, memory, etc.)
         100  = post-response (goal continuation)
        """
        ...

    def inject_pre_run(
        self, loop: AgentLoop, task: str, messages: list[dict[str, Any]],
    ) -> str:
        """Modify the task string before it enters the system prompt.

        Return the (possibly modified) task.  Default: return task unchanged.
        """
        return task

    def inject_per_iteration(
        self, loop: AgentLoop, messages: list[dict[str, Any]],
    ) -> None:
        """Append context messages per iteration (e.g. todos, memory).

        Called after compaction but before LLM call.  Append directly
        to ``messages``.
        """
        pass

    def inject_post_response(
        self,
        loop: AgentLoop,
        response: Any,
        messages: list[dict[str, Any]],
        result: LoopResult,
        iteration: int,
    ) -> bool:
        """Check if the loop should continue after a non-tool-call response.

        Return True to force continuation (appended prompt is in messages).
        """
        return False


# ── Concrete: Goal Context ────────────────────────────────────────


class GoalContextInjector:
    """Prepends <current-research-goal> block to the task string.

    Replaces the hardcoded ``_get_goal_context()`` call in
    ``AgentLoop._prepare_run()``.
    """

    @property
    def name(self) -> str:
        return "goal_context"

    @property
    def order(self) -> int:
        return -100

    def inject_pre_run(
        self, loop: AgentLoop, task: str, messages: list[dict[str, Any]],
    ) -> str:
        if not loop.enable_goal_injection:
            return task
        if not loop.session_id:
            return task
        try:
            from ..goal import get_current_goal_context
            ctx, _ = get_current_goal_context(loop.session_id)
            if ctx:
                return ctx + "\n\n" + task
        except Exception as exc:  # noqa: BLE001
            logger.debug("GoalContextInjector failed: %s", exc)
        return task


# ── Concrete: Todos Snapshot ──────────────────────────────────────


class TodosInjector:
    """Appends <current-todos> system block when the session has todos.

    Replaces the hardcoded ``_inject_todos_snapshot()`` call in
    ``DefaultCompactionStep.execute()``.
    """

    def __init__(self) -> None:
        self._last_hash: int | None = None

    @property
    def name(self) -> str:
        return "todos_snapshot"

    @property
    def order(self) -> int:
        return 0

    def inject_per_iteration(
        self, loop: AgentLoop, messages: list[dict[str, Any]],
    ) -> None:
        if not loop.session_id:
            return
        try:
            from .builtin_tools.todo_tools import TodoStore, _format_todos_snapshot
            todos = TodoStore.get(loop.session_id)
        except Exception:  # noqa: BLE001
            return
        if not todos:
            return
        block = _format_todos_snapshot(todos)
        h = hash(block)
        if self._last_hash == h:
            return
        self._last_hash = h
        messages.append({"role": "system", "content": block})


# ── Concrete: Goal Continuation ──────────────────────────────────


class GoalContinuationInjector:
    """Injects continuation prompt when goal needs more work.

    Replaces the hardcoded ``_check_goal_continuation()`` call in
    ``_run_loop_core()``.
    """

    @property
    def name(self) -> str:
        return "goal_continuation"

    @property
    def order(self) -> int:
        return 100

    def inject_post_response(
        self,
        loop: AgentLoop,
        response: Any,
        messages: list[dict[str, Any]],
        result: LoopResult,
        iteration: int,
    ) -> bool:
        # Final-JSON guard: a complete JSON object IS the turn's final
        # structured answer. Study agents (researcher/strategist/…) emit
        # exactly this shape every round; treating it as "goal not yet
        # done" forced the loop to max_iterations and discarded valid
        # output (see docs/rootcause-goal-injection-maxiter.md).
        # Non-JSON / malformed / partial responses still get the
        # continuation nudge — that is the injector's intended purpose.
        # str() 归一：非 str content（测试替身/异常 provider）走 TypeError
        # 容忍路径而非 AttributeError（与本函数下方注释承诺一致）。
        c = str(getattr(response, "content", "") or "").strip()
        if c.startswith("{") and c.endswith("}"):
            try:
                import json as _json

                # An EMPTY dict ({}) carries no answer at all — it is a
                # degenerate response, not a complete structured one, and
                # must stay a continuation candidate like malformed JSON.
                if isinstance(_json.loads(c), dict) and _json.loads(c):
                    return False
            except (ValueError, TypeError):
                # ValueError: malformed / partial JSON — still a
                # continuation candidate. TypeError: content is not a
                # str at all (e.g. test doubles) — same treatment; the
                # real LLMResponse contract keeps content a str.
                pass
        if not loop.enable_goal_injection:
            return False
        if not loop.session_id:
            return False
        try:
            from ..goal.context import (
                format_goal_continuation_prompt,
                goal_needs_continuation,
            )
            goal_snapshot = _get_goal_snapshot(loop)
            if goal_snapshot is None:
                return False
            if not goal_needs_continuation(goal_snapshot):
                return False
            continuation = format_goal_continuation_prompt(
                goal_snapshot, previous_answer=response.content or "",
            )
            messages.append({"role": "user", "content": continuation})
            result.messages.append({"role": "user", "content": continuation})
            loop._trace({
                "type": "goal_continuation",
                "iteration": iteration,
                "goal_id": goal_snapshot.get("goal", {}).get("goal_id", ""),
            })
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("GoalContinuationInjector failed: %s", exc)
            return False


def _get_goal_snapshot(loop: AgentLoop) -> dict[str, Any] | None:
    """Internal helper — mirrors AgentLoop._get_goal_snapshot logic."""
    if not loop.enable_goal_injection or not loop.session_id:
        return None
    try:
        # Import _get_goal_store from loop module (avoids circular import)
        from . import loop as _loop_mod
        get_goal_store = getattr(_loop_mod, "_get_goal_store", None)
        if get_goal_store is None:
            return None
        return get_goal_store().get_current_snapshot(loop.session_id)
    except Exception:  # noqa: BLE001
        return None


# ── Concrete: Memory Retrieval (A4) ───────────────────────────────


class MemoryRetrieverInjector:
    """Prepends <relevant-memory> block from PersistentMemory (A4).

    Retrieval is best-effort: empty hits inject nothing; exceptions are
    swallowed so a broken memory store never blocks a research run.
    """

    def __init__(
        self,
        memory: Any | None = None,
        *,
        max_results: int = 5,
        max_chars: int = 2000,
        body_chars: int = 200,
        include_hypotheses: bool = False,
    ) -> None:
        self._memory = memory
        self._max_results = max(0, int(max_results))
        self._max_chars = max(0, int(max_chars))
        self._body_chars = max(0, int(body_chars))
        self._include_hypotheses = include_hypotheses

    @property
    def name(self) -> str:
        return "memory_retriever"

    @property
    def order(self) -> int:
        # After goal context (-100), still pre-run.
        return -90

    def inject_pre_run(
        self, loop: AgentLoop, task: str, messages: list[dict[str, Any]],
    ) -> str:
        if not getattr(loop, "enable_memory_injection", True):
            return task
        if not task or not task.strip():
            return task
        try:
            block = self._build_block(loop, task)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryRetrieverInjector failed: %s", exc)
            return task
        if not block:
            return task
        return f"{block}\n\n{task}"

    # ── internals ────────────────────────────────────────────────

    def _build_block(self, loop: AgentLoop, task: str) -> str:
        lines: list[str] = []
        for entry in self._retrieve(loop, task):
            line = self._format_entry(entry)
            if line:
                lines.append(line)
        if not lines:
            return ""
        body = "<relevant-memory>\n" + "\n".join(lines) + "\n</relevant-memory>"
        if self._max_chars and len(body) > self._max_chars:
            body = body[: self._max_chars] + "\n[relevant-memory truncated]"
        return body

    def _retrieve(self, loop: AgentLoop, task: str) -> list[Any]:
        hits: list[Any] = []
        mem = self._memory
        if mem is None:
            mem = self._load_memory()
        if mem is not None and self._max_results:
            hits.extend(list(mem.find_relevant(task, max_results=self._max_results) or []))
        if self._include_hypotheses:
            hits.extend(self._retrieve_hypotheses(loop, task)[: self._max_results])
        return hits[: self._max_results] if self._max_results else hits

    def _load_memory(self) -> Any | None:
        try:
            from ..memory.persistent import PersistentMemory

            return PersistentMemory()
        except Exception:  # noqa: BLE001
            return None

    def _retrieve_hypotheses(self, loop: AgentLoop, task: str) -> list[Any]:
        try:
            from strategy_research.apps.hypothesis.registry import HypothesisRegistry

            reg = HypothesisRegistry()
            items = getattr(reg, "list_hypotheses", None) or getattr(reg, "list", None)
            if not callable(items):
                return []
            rows = list(items() or [])
            # Lightweight keyword filter against task tokens.
            tokens = {t for t in task.lower().split() if len(t) > 2}
            scored = []
            for row in rows:
                text = " ".join(str(row.get(k, "")) for k in ("title", "statement", "description", "name"))
                score = sum(1 for t in tokens if t in text.lower())
                if score:
                    scored.append((score, row))
            scored.sort(key=lambda x: -x[0])
            return [r for _, r in scored]
        except Exception:  # noqa: BLE001
            return []

    def _format_entry(self, entry: Any) -> str:
        if isinstance(entry, dict):
            title = entry.get("title") or entry.get("name") or entry.get("id") or "note"
            typ = entry.get("memory_type") or entry.get("type") or "memory"
            body = str(entry.get("body") or entry.get("description") or entry.get("statement") or "")
        else:
            title = getattr(entry, "title", None) or getattr(entry, "name", None) or "note"
            typ = getattr(entry, "memory_type", None) or "memory"
            body = str(getattr(entry, "body", None) or getattr(entry, "description", None) or "")
        body = " ".join(body.split())
        if self._body_chars and len(body) > self._body_chars:
            body = body[: self._body_chars] + "…"
        return f"- [{typ}/{title}] {body}".rstrip()


# ── Factory ──────────────────────────────────────────────────────


def build_default_injectors() -> Sequence[ContextInjector]:
    """Return the default injector chain (sorted by order)."""
    return cast(Sequence[ContextInjector], sorted(
        [
            GoalContextInjector(),
            MemoryRetrieverInjector(),
            TodosInjector(),
            GoalContinuationInjector(),
        ],
        key=lambda inj: inj.order,
    ))


__all__ = [
    "ContextInjector",
    "GoalContextInjector",
    "MemoryRetrieverInjector",
    "TodosInjector",
    "GoalContinuationInjector",
    "build_default_injectors",
]
