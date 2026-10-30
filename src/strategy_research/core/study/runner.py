"""AutoresearchRunner — AEGIS-powered study executor.

Upgraded version of ``AutoresearchExecutor`` with built-in AEGIS mechanisms:
Novelty Gate, Attribution, Lever Scoreboard, Regression Gate, and Early-stop.

Uses the phase-split ``run_researcher_phase`` / ``run_execution_phase`` /
``run_evaluation_phase`` from ``autoresearch.py`` so AEGIS hooks can be
injected between phases.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ..observability import new_trace_id
from . import budget as _budget
from . import state_utils as _state_utils
from .metric_targets import metric_pass_set as _metric_pass_set  # noqa: F401  (re-export for tests)
from .models import StudyRecord, StudyStatus

if TYPE_CHECKING:
    from .models import StudyGraph


from .store import StudyStore

logger = logging.getLogger(__name__)

# v2 review-cycle tuning (design §10/§11)
SR_STUDY_MAX_DEVIATION = 3          # consecutive high deviations → stop
SR_STUDY_COLLECT_INTERVAL = 5       # force info collection every K rounds
SR_STUDY_MAX_DISCARD = 5            # consecutive discards → stagnation stop

# Agent resilience (Step B of round/retry/loop fix)
SR_AGENT_MAX_ITER = 50              # high-iteration default for complex agents
SR_AGENT_NO_PROGRESS_WINDOW = 5     # tolerate 5 identical tool calls before approval gate
SR_AGENT_MAX_PARSE_RETRIES = 2      # retries per round on parse_failed
SR_AGENT_PARSE_BACKOFF_BASE = 5.0   # base seconds for parse_failed backoff
SR_AGENT_PARSE_BACKOFF_MAX = 30.0   # cap on backoff wait

# Agent names whose parse_failed output triggers round-level retry
_PARSE_FAILED_AGENT_KEYS: tuple[str, ...] = (
    "data_quality_output",
    "factor_analyst_output",
    "strategist_output",
    "portfolio_construction_output",
)


def _parse_failed_agents(exec_result: dict) -> list[str]:
    """Return the names of agents that returned parse_failed for this round."""
    failed: list[str] = []
    for key in _PARSE_FAILED_AGENT_KEYS:
        out = exec_result.get(key)
        if isinstance(out, dict) and out.get("error") == "parse_failed":
            # key like "data_quality_output" → "data_quality"
            failed.append(key[: -len("_output")])
    return failed


def _dlog(module: str, msg: str, *args) -> None:
    msg_fmt = msg % args if args else msg
    logger.info("[RUNNER:%s] %s", module, msg_fmt)
    print(f"[RUNNER:{module}] {msg_fmt}", flush=True)  # noqa: T201


# ── shutdown reasons ────────────────────────────────────────────────


class ShutdownReason:
    TARGETS_MET = "targets_met"
    MAX_ROUNDS = "max_rounds"
    STAGNATION = "stagnation"
    BUDGET = "budget_exceeded"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    ERROR = "error"
    EARLY_STOPPED = "early_stopped"
    NOVELTY_REJECTED = "novelty_rejected"
    # v2 review cycle (design §10.2/§10.3)
    REPEATED_DEVIATION = "repeated_deviation"
    REVIEW_FAILED = "review_failed"
    DISCARD_STREAK = "stagnation_discard_streak"
    # v2 monitor (design §15)
    NEEDS_REFRESH = "needs_refresh"


# ── metric target comparison (reused from executor.py) ──────────────
from .metric_targets import acceptance_config_from_targets, meets_metric_targets  # noqa: F401

# ── event emitter protocol ──────────────────────────────────────────


class EventEmitter(Protocol):
    def emit(self, session_id: str, event: str, data: dict) -> None: ...


@dataclass
class NullEmitter:
    def emit(self, session_id: str, event: str, data: dict) -> None:
        return None


# ── control token ───────────────────────────────────────────────────


@dataclass
class ControlToken:
    paused: bool = False
    cancelled: bool = False


# ── AutoresearchRunner ─────────────────────────────────────────────


from .runner_aegis import RunnerAegisMixin
from .runner_knowledge import RunnerKnowledgeMixin

# P5-2: RunnerLangGraphMixin retired — the graph path (graph_preset) is the
# only round executor; HITL pause/resume lives on this class directly.


class AutoresearchRunner(RunnerAegisMixin, RunnerKnowledgeMixin):
    """AEGIS-powered study executor with round-loop + AEGIS hooks.

    Replaces AutoresearchExecutor for studies using executor_type='autoresearch'.
    The round loop calls phase-split functions from autoresearch.py, with AEGIS
    mechanisms injected between phases.
    """

    def __init__(
        self,
        study: StudyRecord,
        store: StudyStore,
        *,
        control: ControlToken | None = None,
        emitter: EventEmitter | None = None,
        goal_store: Any = None,
        artifacts: Any = None,
    ) -> None:
        self.study = study
        self.study_id = study.study_id
        self.study_store = store
        self.control = control or ControlToken()
        self.emitter = emitter or NullEmitter()
        # R4：P-3 通道（大结果落盘+登记，事件只带指针）。None = 现行为。
        self._artifacts = artifacts
        self._own_goal_store = goal_store is None
        self._goal_store = goal_store or _state_utils.open_goal_store()
        # AEGIS state
        self._prev_passed: set[str] = set()
        self._best_score: float = 0.0
        self._idle_rounds: int = 0
        # Agent resilience: resolve loop strategy from study's evolved config
        # or default to ExplorerStrategy (max_iter=50, no_progress_window=5).
        try:
            from ..agent.strategy.profile_resolver import resolve_loop_strategy
            loop_cfg = getattr(study, "loop_config", None)
            if loop_cfg:
                self._loop_strategy = resolve_loop_strategy(
                    {"name": "explorer", "config": loop_cfg}
                )
            else:
                from ..agent.strategy.explorer import ExplorerStrategyFactory
                self._loop_strategy = ExplorerStrategyFactory.create()
        except Exception as exc:  # noqa: BLE001
            logger.debug("strategy resolution failed, falling back to default: %s", exc)
            self._loop_strategy = None
        # Budget accumulators
        self._round_start_clock: float | None = None
        self._total_used_time: float = 0.0
        self._total_used_turns: int = 0
        # Study-row cache for live reads: DB is the source of truth for
        # mutable fields (current_round, last_metrics, last_verdict,
        # execution_status), but re-reading on every access is wasteful.
        # 5s TTL keeps monitor-loop snapshots fresh without hammering
        # SQLite. Writers (cancel/pause/cancel_study) call
        # ``invalidate_study_cache()`` after ``update_execution_status``.
        self._study_cache: StudyRecord | None = None
        self._study_cache_ts: float = 0.0
        # v4: study-scoped trace id — stable across rounds so every SSE
        # event + log line for this study correlates to one research line.
        self._trace_id = new_trace_id()

    def _get_study(self, *, force: bool = False) -> StudyRecord:
        """Return current ``StudyRecord`` from DB, cached for ~5s.

        The constructor's ``study`` arg is treated as the initial value;
        subsequent reads pick up DB mutations (current_round,
        last_metrics, execution_status, etc.) without requiring the
        caller to manually re-fetch.
        """
        now = time.monotonic()
        if not force and self._study_cache is not None and (now - self._study_cache_ts) < 5.0:
            return self._study_cache
        loaded = self.study_store.get_study(self.study_id)
        if loaded is None:
            return self.study  # study row vanished; fall back to last known
        self._study_cache = loaded
        self._study_cache_ts = now
        return loaded

    def invalidate_study_cache(self) -> None:
        self._study_cache = None
        self._study_cache_ts = 0.0

    def _current_db_status(self) -> StudyStatus | None:
        """Bypass the 5s study cache to read the freshest execution_status."""
        try:
            fresh = self.study_store.get_study(self.study_id)
        except Exception:  # noqa: BLE001
            return None
        return fresh.execution_status if fresh else None

    # ── public entrypoint ───────────────────────────────────────────

    async def run(self) -> str:
        sid = self._get_study().study_id
        session = self._get_study().session_id
        _dlog("runner", "run() starting study=%s session=%s max_rounds=%s",
              sid, session, self._get_study().max_rounds)
        # v4: bind the study-scoped trace for the whole lifecycle so
        # every log line + emitted event carries trace_id.
        from ..observability import bind_trace
        with bind_trace(trace_id=self._trace_id, study_id=sid):
            reason = await self._run_lifecycle(sid, session)
            self._emit_directive(sid, session, reason)
            return reason

    def _emit_directive(self, sid: str, session: str, reason: str) -> None:
        """R6：Director 产出「下一步建议」并只发事件——**不自动开 run**。

        红线（架构图 ② Director 格）：不读预算、不得自行开 run。
        是否执行建议由上层/人工决定；本方法无副作用（除一条事件）。
        失败绝不影响 study 收尾。
        """
        try:
            from .director import propose_next

            last = self.study_store.get_study(sid)
            proposal = propose_next(
                last, str(reason), metrics=getattr(last, "last_metrics", None),
            )
            self._emit(session, "study.directive", proposal.to_dict())
        except Exception:  # noqa: BLE001 —— 建议失败不影响收尾
            logger.debug("directive proposal failed study=%s", sid, exc_info=True)

    async def _run_lifecycle(self, sid: str, session: str) -> str:
        self._emit(session, "study_started", {"study_id": sid, "round": self._get_study().current_round})
        reason = ShutdownReason.ERROR
        try:
            # v2 §15.2 recover: a MONITORING study restarts directly into
            # the monitor phase (no research rounds).
            if self._get_study().execution_status == StudyStatus.MONITORING:
                reason = await self._monitor_phase()
                return reason
            reason = await self._run_loop()
            # Parameter evolution: record fitness observation + maybe evolve.
            self._record_evolution_observation()
            # v2 §15.2: on E2 completion with monitoring enabled, enter the
            # post-completion monitor phase (rounds stop; periodic re-checks).
            # Runs in-sequence so the scheduler's control token and semaphore
            # stay alive for pause/resume/cancel during monitoring.
            if reason == ShutdownReason.TARGETS_MET and (
                self._get_study().monitor_interval_seconds or 0
            ) > 0:
                reason = await self._monitor_phase()
        except Exception as exc:
            _dlog("runner", "run() FAILED: study=%s error=%s", sid, exc)
            logger.exception("study %s failed: %s", sid, exc)
            tb = traceback.format_exc()
            # Honour any status another actor (e.g. scheduler.archive)
            # may have persisted while the round was in flight — don't
            # overwrite ARCHIVED with ERROR.
            live_status = self._current_db_status()
            if live_status == StudyStatus.ARCHIVED:
                _dlog(
                    "runner",
                    "run() exception suppressed: study=%s is ARCHIVED",
                    sid,
                )
            else:
                self.study_store.update_execution_status(
                    sid, StudyStatus.ERROR,
                    last_error=f"{type(exc).__name__}: {exc}"[:500],
                    last_traceback=tb[:8000],
                )
            self._emit(session, "study_failed", {"study_id": sid, "error": f"{type(exc).__name__}: {exc}"[:500], "reason": ShutdownReason.ERROR})
        finally:
            if self._own_goal_store:
                try:
                    self._goal_store.close()
                except Exception:
                    pass
            # Safety net: drop any background tasks this study left behind
            # on early/abnormal exits (round-end harvest covers the norm).
            try:
                from strategy_research.core.utils.bg_proc import harvest_by_owner
                harvest_by_owner(sid)
            except Exception:  # noqa: BLE001
                pass
            self._emit(session, "study_executor_stopped", {"study_id": sid, "reason": reason})
        return reason

    # ── main loop ──────────────────────────────────────────────────

    def _check_stop_conditions(
        self,
        result: dict,
        metrics: dict,
        verdict: str,
        round_num: int,
        session: str,
        sid: str,
    ) -> str | None:
        """Check post-round shutdown conditions; returns stop reason or None."""
        from strategy_research.core.study import state_store as _ss

        # targets met (E2: targets ∧ keep ∧ gates pass)
        if result.get("e2_passed"):
            self._complete_goal(result)
            self._mark_terminal(StudyStatus.COMPLETE, last_metrics=metrics, reason=ShutdownReason.TARGETS_MET)
            self._emit(session, "study_completed", {
                "study_id": sid, "goal_id": self._get_study().goal_id,
                "metrics": metrics, "round": round_num, "recap": verdict,
            })
            return ShutdownReason.TARGETS_MET

        # budget
        if self._budget_exceeded():
            self._mark_terminal(StudyStatus.BUDGET_LIMITED, last_metrics=metrics,
                                last_error=self._budget_summary(), reason=ShutdownReason.BUDGET)
            self._emit(session, "study_budget_limited", {
                "study_id": sid, "used": self._budget_summary(),
            })
            return ShutdownReason.BUDGET

        # stagnation
        decision = result.get("decision")
        if decision and isinstance(decision, dict) and decision.get("stagnation_triggered"):
            self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                last_error="stagnation", reason=ShutdownReason.STAGNATION)
            return ShutdownReason.STAGNATION

        # v2 review cycle stop (repeated deviation / review failure)
        review_stop = result.get("review_stop")
        if review_stop:
            if review_stop == ShutdownReason.REPEATED_DEVIATION:
                self._mark_terminal(
                    StudyStatus.ERROR, last_metrics=metrics,
                    last_error="repeated deviation (3x high)",
                    reason=ShutdownReason.REPEATED_DEVIATION,
                )
            else:
                self._mark_terminal(
                    StudyStatus.ERROR, last_metrics=metrics,
                    last_error=review_stop, reason=review_stop,
                )
            self._emit(session, "study_early_stopped", {
                "study_id": sid, "round": round_num, "reason": review_stop,
            })
            return review_stop

        # discard streak (design §8.2)
        _st = _ss.load(Path(self._get_study().workspace_path), sid)
        if _st.discard_streak >= SR_STUDY_MAX_DISCARD:
            self._mark_terminal(
                StudyStatus.ERROR, last_metrics=metrics,
                last_error=f"discard_streak={_st.discard_streak}",
                reason=ShutdownReason.DISCARD_STREAK,
            )
            self._emit(session, "study_early_stopped", {
                "study_id": sid, "round": round_num,
                "reason": ShutdownReason.DISCARD_STREAK,
            })
            return ShutdownReason.DISCARD_STREAK

        return None

    # ── Step 3: loop-control helpers (docs/step3-run-loop-extraction-design.md) ──

    async def _top_control_check(self, round_num: int, session: str, sid: str) -> str:
        """Loop-top control state check.

        Returns ``"cancelled"`` (loop must return CANCELLED), ``"continue"``
        (cancelled while paused — H3; re-check at loop top), or ``"resumed"``
        (paused and resumed cleanly, or no pause — proceed to round prep).
        """
        if self.control.cancelled:
            # Honour any status another actor (e.g. scheduler.archive)
            # may have persisted while this iteration was in flight —
            # never overwrite an ARCHIVED row with CANCELLED.
            live_status = self._current_db_status()
            if live_status == StudyStatus.ARCHIVED:
                self._emit(session, "study_cancelled", {
                    "study_id": sid,
                    "note": f"preserved live status={live_status.value}",
                })
                return "cancelled"
            self._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
            self._emit(session, "study_cancelled", {"study_id": sid})
            return "cancelled"

        if self.control.paused:
            self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
            self._emit(session, "study_paused", {"study_id": sid, "round": round_num})
            await self._wait_until_resumed()
            if self.control.cancelled:
                # Cancelled while paused: let the loop top finalize
                # CANCELLED instead of flipping back to RUNNING and
                # burning a full LLM round (H3).
                return "continue"
            self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
            self._emit(session, "study_resumed", {"study_id": sid, "round": round_num})

        return "resumed"

    async def _handle_hitl_pause(
        self,
        result: dict,
        round_num: int,
        session: str,
        sid: str,
        directive_text: str | None,
    ) -> tuple[dict | None, str]:
        """Wait for HITL approval, then resume the round or skip it.

        Returns ``(resumed_result, "advance")`` on the approved path, or
        ``(None, "continue")`` for every skip path: rejection/timeout,
        cancelled-while-waiting, re-pause, missing checkpointer, and
        resume-abort.

        Semantics carried over verbatim from the inline block (M2 bank
        the approval-wait time, M1 expire the interrupt, H2 never write
        a ghost empty-metrics round, H1 recompute e2/passed_now after
        resume) — see docs/step3-run-loop-extraction-design.md §3.3.
        """
        _dlog("loop", "round %d paused for HITL approval", round_num)
        self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
        self._emit(session, "study_paused", {
            "study_id": sid, "round": round_num,
            "reason": "hitl_approval",
            "interrupt_id": result.get("interrupt_id"),
            "hypothesis": result.get("hypothesis"),
        })
        # The approval wait must not count against the time
        # budget (M2): bank the pre-wait elapsed time and stop
        # the round clock; it restarts after the resume.
        if self._round_start_clock is not None:
            self._total_used_time += time.perf_counter() - self._round_start_clock
        self._round_start_clock = None
        # Poll for approval (max 10 minutes, check every 5 seconds)
        decision = await self._wait_for_approval(sid, round_num, timeout_s=600)
        if decision != "approved":
            # Timeout or rejection — skip this round and keep the
            # study loop alive (status must leave PAUSED).
            self._account_round_budget(result)
            self._expire_interrupt(sid, round_num)  # M1: no zombie pending interrupt
            if self.control.cancelled:
                return None, "continue"  # loop top finalizes CANCELLED
            self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
            self._emit(session, "study_round_rejected", {
                "study_id": sid, "round": round_num,
                "reason": f"hitl_{decision}",
            })
            return None, "continue"
        if self.control.cancelled:
            # Approved, but the study was cancelled while we
            # waited — same handling as the H3 pause path.
            return None, "continue"
        # Resume the round from checkpoint
        self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
        # Reconstruct context from study object (same as _run_one_round):
        # read state against the run's own strategy copy so resumed phases
        # see exactly what the interrupted round was seeing.
        study = self._get_study()
        _path = Path(study.workspace_path).resolve()
        _strategy = study.strategy_name
        _graph = self._load_graph(_path, sid)
        from strategy_research.core.autoresearch import read_current_state
        from strategy_research.core.study import round_manifest as rm
        _round_dir = rm.round_dir(_path, sid, round_num)
        _run_dirs = sorted(
            d for d in _round_dir.iterdir()
            if d.is_dir() and d.name.startswith("run_")
        ) if _round_dir.exists() else []
        if _run_dirs:
            _run_dir = _run_dirs[-1]
        else:
            logger.warning(
                "HITL resume: no run_* dirs under %s, falling back to run_0001",
                _round_dir,
            )
            _run_dir = _round_dir / "run_0001"
        _current_state = read_current_state(
            _path, _strategy,
            strategy_file=_run_dir / "strategy.py",
        )
        _current_state["study_strategy_path"] = str(
            _run_dir.relative_to(_path) / "strategy.py"
        )
        # Resume from persisted artifacts (plan A): rebuild state from the
        # interrupt payload and continue at claims — no checkpointer.
        self._round_start_clock = time.perf_counter()
        resumed = await asyncio.to_thread(
            self._resume_round_via_graph, _path, _strategy,
            _current_state, _run_dir, _graph,
            session=session, sid=sid, round_num=round_num,
            directive_text=directive_text,
        )
        if resumed.get("paused_for_approval"):
            # The resume hit the gate again (typically: no
            # checkpointer → the fallback re-ran the round and
            # interrupted afresh). There is no safe way to
            # finalize this round here — skip it instead of
            # recording a ghost empty-metrics round (H2).
            self._expire_interrupt(sid, round_num)
            if self.control.cancelled:
                return None, "continue"
            self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
            self._emit(session, "study_round_rejected", {
                "study_id": sid, "round": round_num,
                "reason": "hitl_repause",
            })
            return None, "continue"
        if resumed.get("error") == "no_checkpointer":
            # Resume failed because no checkpointer was available.
            # Expire the interrupt and let the user know.
            self._expire_interrupt(sid, round_num)
            if self.control.cancelled:
                return None, "continue"
            self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
            self._emit(session, "study_round_rejected", {
                "study_id": sid, "round": round_num,
                "reason": "no_checkpointer",
                "detail": "Resume unavailable: checkpoint store not configured for this engine.",
            })
            return None, "continue"
        if resumed.get("aborted"):
            self._account_round_budget(resumed)
            return None, "continue"
        # H1 (partial): the resume path bypasses phase_engine's
        # finalization, so e2_passed/passed_now would be missing
        # and a targets-met round could never COMPLETE. Recompute
        # them from the resumed metrics (the eval gate check is
        # folded into verdict=="keep", same as the phases branch).
        _study_now = self._get_study()
        if _study_now.metric_targets:
            from .metric_targets import (
                meets_metric_targets as _meets,
            )
            from .metric_targets import (
                metric_pass_set as _pass_set,
            )
            _m = resumed.get("metrics", {})
            resumed["passed_now"] = _pass_set(_m, _study_now.metric_targets)
            resumed["e2_passed"] = bool(
                _m and _meets(_m, _study_now.metric_targets)
                and resumed.get("verdict", "discard") == "keep"
            )
        return resumed, "advance"

    async def _wait_for_approval(
        self, sid: str, round_num: int, timeout_s: int = 600
    ) -> str:
        """Poll for HITL approval.

        Returns the resolved decision: ``"approved"``, ``"rejected"``,
        ``"cancelled"`` (cancel/pause observed while waiting), or
        ``"timeout"`` when no response arrived within ``timeout_s``.

        (Was on RunnerLangGraphMixin until P5-2 — engine-agnostic, the
        graph-path HITL loop uses it.)
        """
        import asyncio
        start = time.time()
        while time.time() - start < timeout_s:
            # M1/L9: cancel (and pause) must cut the wait short — the old
            # loop slept through a cancel for up to the full timeout.
            if self.control.cancelled or self.control.paused:
                return "cancelled"
            interrupt = self.study_store.get_interrupt_for_round(sid, round_num)
            if interrupt is not None and interrupt.status in ("approved", "rejected"):
                return interrupt.status
            await asyncio.sleep(5)
        return "timeout"

    def _expire_interrupt(self, sid: str, round_num: int) -> None:
        """Mark the round's pending HITL interrupt expired (M1).

        Without this the interrupt row stayed ``pending`` forever after a
        timeout/rejection: the webui approval card reappeared on reload
        and a late answer was silently written to no consumer.
        """
        try:
            interrupt = self.study_store.get_pending_interrupt(sid, round_num)
            if interrupt is not None:
                self.study_store.respond_interrupt(interrupt.interrupt_id, "expired")
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "failed to expire interrupt round=%s study=%s",
                round_num, sid, exc_info=True,
            )

    def _load_graph(self, path: Path, sid: str) -> "StudyGraph":
        """Load the study's persisted execution graph.

        Falls back to ``DEFAULT_STANDARD_GRAPH`` when ``graph.json`` is
        missing or malformed (legacy studies pre-migration). Validates
        and logs warnings; never raises — a broken graph must not block
        a running study.

        (Was on RunnerLangGraphMixin until P5-2 — it is engine-agnostic
        graph.json loading consumed by the round preamble and HITL
        resume, so it lives on the runner directly now.)
        """
        from .graph import StudyGraph
        from .graph_templates import DEFAULT_STANDARD_GRAPH

        graph = StudyGraph.load(path, sid)
        if graph is None:
            graph = DEFAULT_STANDARD_GRAPH
        errors = graph.validate()
        if errors:
            logger.warning(
                "Study %s graph validation warnings: %s; "
                "falling back to standard template",
                sid, errors,
            )
            graph = DEFAULT_STANDARD_GRAPH
        return graph

    def _resume_round_via_graph(
        self, path, strategy, current_state, run_dir, graph,
        *, session: str, sid: str, round_num: int,
        directive_text: str | None,
    ) -> dict:
        """方案 A resume：interrupt payload 重建 state + ``start=claims``。

        不走 SqliteSaver（决策记录
        docs/hitl-resume-industry-research-20261027.md）：暂停契约保证
        payload 携带后继节点要读的每个键；researcher/novelty/gate 不重跑。
        输出 dict 与旧 resume 同形（metrics/verdict/...；e2_passed/
        passed_now 由 _handle_hitl_pause 的 H1 段补齐）。
        """
        from .graph_preset import resume_round_via_graph
        from .phase_engine import BranchContext
        from .state_store import study_root as _study_root

        payload: dict = {}
        interrupt = self.study_store.get_interrupt_for_round(sid, round_num)
        raw = getattr(interrupt, "payload", None)
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    payload = loaded
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "HITL resume: unreadable interrupt payload for "
                    "study %s round %d; resuming with minimal state",
                    sid, round_num,
                )

        study = self._get_study()
        root = _study_root(path, sid)
        ctx = BranchContext(
            runner=self, study=study, round_num=round_num,
            previous_summary=None, directive_text=directive_text,
            path=path, strategy=strategy, current_state=current_state,
            run_dir=run_dir, runs_dir=run_dir.parent,
            run_name=run_dir.name, results_tsv=root / "results.tsv",
            graph=graph, session=session, sid=sid,
            metric_targets=study.metric_targets, guidance=None,
        )
        outcome = resume_round_via_graph(ctx, interrupt_payload=payload)
        if isinstance(outcome, dict):
            # resume 途中再中断/abort：交给 runner 循环的既有分支处理
            return outcome
        return {
            "run_name": ctx.run_name,
            "metrics": outcome.metrics,
            "verdict": outcome.verdict,
            "eval_result": outcome.eval_result,
            "hypothesis": outcome.hypothesis,
            "predicted_affected": outcome.predicted_affected,
            "backtest_error": outcome.backtest_error,
        }

    def _finalize_round(
        self,
        result: dict,
        metrics: dict,
        verdict: str,
        round_num: int,
        session: str,
        sid: str,
    ) -> str | None:
        """Post-round finalize: budget, heartbeat, SSE, terminal checks.

        Returns ``None`` to continue looping, or the terminal
        ShutdownReason. Covers blocks E+F of the design doc (budget
        accounting, bg harvest, three SSE emits, stop_conditions,
        max_rounds, AEGIS early-stop). ``_prev_passed`` is updated on
        the way out.
        """
        # ── budget ─────────────────────────────────────────────
        self._account_round_budget(result)
        self.study_store.update_round_heartbeat(sid, round_num)
        self.study_store.update_last_metrics(sid, metrics, verdict)

        # ── research discipline: B2 health scan (observation-first,
        # docs/phase1-research-discipline.md §3) ──
        try:
            from .strategy_acceptance.health import health_check

            findings = health_check(metrics)
            if findings:
                self._emit(session, "study_health_check", {
                    "study_id": sid,
                    "round": round_num,
                    "findings": [f.to_dict() for f in findings],
                })
        except Exception:  # noqa: BLE001 — health scan must never break a round
            _dlog("loop", "health_check failed for round %d", round_num)

        # ── round end: harvest this study's background tasks ──
        # A backgrounded agent tool (run_backtest background=True /
        # run_bg_command) abandoned mid-poll must not linger into the
        # next round (kill live ones, drop finished ones).
        from strategy_research.core.utils.bg_proc import harvest_by_owner
        killed = harvest_by_owner(sid)
        if killed:
            _dlog("loop", "round %d: harvested %d stale bg tasks", round_num, killed)

        # ── SSE: study_round ───────────────────────────────────
        # R4（P-3）：metrics 超过阈值 → 落盘 + 登记，事件只带指针；
        # 小 metrics（绝大多数）原样进事件——字节不变（等价门）。
        round_metrics = metrics
        if self._artifacts is not None:
            try:
                maybe = self._artifacts.maybe_artifact(
                    session_id=session,
                    payload=metrics,
                    kind="round_metrics",
                    run_id=f"{sid}:R{round_num}",
                    study_id=sid,
                    round_id=f"R{round_num}",
                )
                if maybe is not metrics:
                    round_metrics = maybe.to_dict()
                    self._emit(session, "artifact.registered", {
                        "study_id": sid, "round": round_num,
                        "kind": "round_metrics",
                        **round_metrics,
                    })
            except Exception:  # noqa: BLE001 —— 落盘失败降级为内联
                logger.warning("artifact channel failed round=%s", round_num,
                               exc_info=True)
        self._emit(session, "study_round", {
            "study_id": sid, "round": round_num,
            "run": result.get("run_name", ""),
            "metrics": round_metrics, "verdict": verdict,
        })

        # ── SSE: study_budget (per-round accounting) ─────────
        self._emit(session, "study_budget", {
            "study_id": sid,
            "budget": {
                "budget_used_turns": self._total_used_turns,
                "budget_used_time_s": round(self._total_used_time, 1),
                "budget_turn": self._get_study().budget_turn,
                "budget_time_seconds": self._get_study().budget_time_seconds,
            },
        })

        # ── SSE: study_scoreboard (lever precision + claims calibration) ──
        sb_extra: dict = {}
        try:
            if self._goal_store is not None and self._get_study().goal_id:
                from .claims import summarize_prediction_accuracy
                _entries = self._goal_store.list_journal_entries(
                    self._get_study().goal_id, limit=10)
                sb_extra = summarize_prediction_accuracy(_entries)
        except Exception:  # noqa: BLE001 — claims stats are best-effort
            sb_extra = {}
        if hasattr(self, "_scoreboard"):
            sb = self._scoreboard.get_scoreboard()
            sb_data = [
                {"lever": s.lever, "attempts": s.attempts,
                 "accepted": s.accepted, "reverted": s.reverted}
                for s in sb if s.attempts > 0
            ]
            if sb_data or sb_extra.get("n_predictions"):
                self._emit(session, "study_scoreboard", {
                    "study_id": sid,
                    "round": round_num,
                    "scoreboard": sb_data,
                    "claims": sb_extra,
                })

        # ── shutdown conditions (targets/budget/stagnation/review/discard) ──
        stop_reason = self._check_stop_conditions(
            result, metrics, verdict, round_num, session, sid
        )
        if stop_reason is not None:
            return stop_reason

        # ── shutdown: max_rounds ───────────────────────────────
        max_rounds = self._get_study().max_rounds
        if max_rounds is not None and round_num >= max_rounds:
            self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                last_error=f"max_rounds={max_rounds}", reason=ShutdownReason.MAX_ROUNDS)
            return ShutdownReason.MAX_ROUNDS

        # ── AEGIS: Early-stop (only when max_rounds is configured) ──
        if max_rounds is not None:
            current_score = metrics.get("calmar", 0.0) or 0.0
            if current_score > self._best_score:
                self._best_score = current_score
                self._idle_rounds = 0
            else:
                self._idle_rounds += 1
            if self._idle_rounds >= self._get_study().early_stop_patience:
                self._mark_terminal(StudyStatus.EARLY_STOPPED, last_metrics=metrics,
                                    last_error=f"idle={self._idle_rounds} rounds, best={self._best_score}",
                                    reason=ShutdownReason.EARLY_STOPPED)
                self._emit(session, "study_early_stopped", {
                    "study_id": sid, "round": round_num,
                    "idle_rounds": self._idle_rounds, "best_score": self._best_score,
                })
                return ShutdownReason.EARLY_STOPPED

        self._prev_passed = result.get("passed_now", set())

        return None

    async def _run_loop(self) -> str:
        sid = self._get_study().study_id
        session = self._get_study().session_id

        # Load previous summary for cross-round context (fresh record —
        # strategy_name / workspace_path may have been updated).
        previous_summary = self._maybe_load_previous_summary(self._get_study())
        # v2: best score comes from state.json (keep-only, design §8.4)
        from strategy_research.core.study import state_store as ss
        state = ss.load(Path(self._get_study().workspace_path), sid)
        best_calmar = (state.best_metrics or {}).get("calmar")
        if best_calmar is not None:
            self._best_score = float(best_calmar)
        elif previous_summary and previous_summary.get("metrics"):
            self._best_score = previous_summary["metrics"].get("calmar", 0.0)
        # M2: restore budget counters from the authoritative workspace
        # state. Without this, ERROR→continue/retry restarted the budget
        # from zero and budget_turn/budget_time_seconds were soft limits.
        # max() guards against double-restore on re-entry.
        self._total_used_turns = max(
            self._total_used_turns, int(state.budget_used_turns or 0),
        )
        self._total_used_time = max(
            self._total_used_time, float(state.budget_used_time_s or 0.0),
        )

        round_num = self._get_study().current_round

        while True:
            control = await self._top_control_check(round_num, session, sid)
            if control == "cancelled":
                return ShutdownReason.CANCELLED
            if control == "continue":
                continue
            # control == "resumed" (or no-op) falls through to round prep

            round_num += 1
            self._round_start_clock = time.perf_counter()

            # ── Flush pending objective replacements (Step B1) ───────
            # The user may have replaced the objective between rounds;
            # the store updated ``studies.objective`` already, but the
            # audit row is still flagged ``applied_round=NULL``. Mark
            # them applied here so the history UI can distinguish
            # pending vs applied. Also force-reload the study cache so
            # the freshly persisted objective is picked up.
            applied_now = self.study_store.mark_pending_objectives_applied(
                sid, round_num,
            )
            if applied_now:
                self.invalidate_study_cache()
                self._emit(session, "study_objective_applied", {
                    "study_id": sid,
                    "round": round_num,
                    "count": applied_now,
                })

            # Consume directives
            pending = self.study_store.list_pending_directives(sid)
            directive_text = self._format_directives(pending) if pending else None

            _dlog("loop", "round %d start study=%s", round_num, sid)

            # ── Run one round (overridable for tests) ──────────────
            result = await asyncio.to_thread(
                self._run_one_round, round_num, previous_summary, directive_text,
            )
            _dlog("loop", "round %d result: aborted=%s, paused=%s, metrics=%s, verdict=%s",
                  round_num, result.get("aborted"), result.get("paused_for_approval"),
                  bool(result.get("metrics")), result.get("verdict"))

            # Mark directives consumed
            if pending:
                self.study_store.mark_directives_consumed(sid, [d.directive_id for d in pending])

            # Handle aborted round (novelty rejected)
            if result.get("aborted"):
                # Budget accounting still happens: the researcher phase
                # consumed real time/turns even though the round was
                # rejected before execution (M2).
                self._account_round_budget(result)
                _dlog("loop", "round %d aborted: %s", round_num, result.get("reason"))
                continue

            # P4: Handle HITL approval pause
            if result.get("paused_for_approval"):
                resumed, hitl = await self._handle_hitl_pause(
                    result, round_num, session, sid, directive_text,
                )
                if hitl == "continue":
                    continue
                result = resumed


            if result is None:
                result = {}
            metrics = result.get("metrics", {})
            verdict = result.get("verdict", "discard")
            summary = result.get("summary")
            previous_summary = summary or previous_summary

            # ── finalize: budget / SSE / terminal checks (Step 3) ──
            stop = self._finalize_round(
                result, metrics, verdict, round_num, session, sid,
            )
            if stop is not None:
                return stop


            # ── cooldown ───────────────────────────────────────────
            cooldown = self._round_cooldown()
            _dlog("loop", "cooldown %.1fs before round %d", cooldown, round_num + 1)
            await asyncio.sleep(cooldown)

    # ── v2 monitor phase (design §15) ──────────────────────────────

    async def _monitor_phase(self) -> str:
        """Post-completion monitoring — delegates to monitor.py."""
        from .monitor import monitor_phase
        return await monitor_phase(self)

    def _run_one_round(
        self,
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Execute one round: phases + AEGIS hooks + v2 artifacts.

        v2 (design §8.1): per-round autonomous directory under
        ``study/<id>/rounds/round_NNNN/``; the round starts from the
        adopted run (last keep run, else baseline) copied into the round's
        first run dir; three artifacts (manifest/summary.md/journal.md)
        land at round end (phase 1); state.json is the authority.

        Overridable for tests to stub round execution.
        """
        from ..observability import bind_trace, get_trace_context

        # v4: ensure a trace_id exists for the round (study-scoped stable
        # id: generated once per study, reused across rounds so all rounds
        # correlate to the same research line). Outer attempts may already
        # have bound one.
        outer = get_trace_context()
        with bind_trace(
            trace_id=outer.get("trace_id") or self._trace_id,
            study_id=self._get_study().study_id,
            round_num=round_num,
        ):
            return self._run_one_round_impl(
                round_num, previous_summary, directive_text,
            )

    def _run_one_round_impl(
        self,
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Actual round implementation — delegates to phase_engine.py."""
        from .phase_engine import run_round_phases
        return run_round_phases(self, round_num, previous_summary, directive_text)

    # ── v2 review cycle (design §10) ────────────────────────────────

    @staticmethod
    def _try_parse_json(text: str) -> Any:
        """Parse agent output JSON; return the raw string on failure."""
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def _build_round_task_text(
        self, current_state: dict, directive_text: str | None,
    ) -> str:
        """Compose the base task text for every agent in the round."""
        parts: list[str] = ["根据研究目标与历史结果完成当前轮次的工作。"]
        if current_state:
            parts.append("## 当前状态\n" + json.dumps(
                current_state, ensure_ascii=False, default=str,
            ))
        if directive_text:
            parts.append("## 用户指令\n" + directive_text)
        return "\n\n".join(parts)

    def _save_agent_output(
        self, run_dir: Path, agent_id: str, result: Any,
    ) -> None:
        """Persist an agent's output to ``run_dir/<agent_id>.json``."""
        from ..autoresearch import save_agent_record
        try:
            # result is a dict from save_agent_outputs, not an object
            output_data = result.get("output", result) if isinstance(result, dict) else result
            save_agent_record(
                run_dir, agent_id, 3, {}, output_data,
            )
        except Exception:  # noqa: BLE001
            logger.debug("save_agent_record failed for %s", agent_id)

    def _emit_topology(
        self,
        session: str,
        sid: str,
        round_num: int,
        graph: "StudyGraph",
        agent_outputs: dict,
    ) -> None:
        """Emit SSE events describing the current layer being processed.

        The frontend uses these events to highlight which layer is
        active and to flag nodes that errored (so they can show a red
        border).
        """
        layers = graph.topological_layers()
        completed_ids = {
            nid for nid, out in agent_outputs.items()
            if out and not (isinstance(out, dict) and out.get("error"))
        }
        for layer_idx, layer_ids in enumerate(layers):
            for nid in layer_ids:
                node = graph.node_map.get(nid)
                if node is None:
                    continue
                status = (
                    "completed" if nid in completed_ids
                    else "pending"
                )
                self._emit(session, "study_graph_node", {
                    "study_id": sid,
                    "round": round_num,
                    "layer": layer_idx,
                    "node_id": nid,
                    "node_type": node.type,
                    "node_label": node.label or nid,
                    "enabled": node.enabled,
                    "status": status,
                })

    def _account_round_budget(self, exec_result: dict) -> None:
        self._total_used_time, self._total_used_turns = _budget.account_round_budget(
            self._total_used_time, self._total_used_turns,
            self._round_start_clock, exec_result,
        )

    def _budget_exceeded(self) -> bool:
        # Fresh study record (5s-cached), not the constructor snapshot —
        # budget limits can change mid-run via replace_objective / API.
        return _budget.budget_exceeded(
            self._get_study(), self._total_used_time, self._total_used_turns,
        )

    def _budget_summary(self) -> str:
        return _budget.budget_summary(self._total_used_turns, self._total_used_time)

    # ── goal completion (delegating to budget.complete_goal) ───────

    def _complete_goal(self, exec_result: dict) -> None:
        _budget.complete_goal(self._goal_store, self._get_study(), exec_result)

    # ── results.tsv ────────────────────────────────────────────────

    @staticmethod
    def _update_results_tsv(
        runs_dir: Path,
        run_name: str,
        verdict: str,
        *,
        round_num: int | None = None,
        results_tsv: Path | None = None,
    ) -> None:
        """In-place verdict update with (round, run) composite matching.

        v2: run ids repeat per round (round_NNNN/run_0001…), so rows are
        matched by round (trailing column 13) + run name; legacy CLI rows
        (no round column) fall back to run-name matching.
        """
        results_path = results_tsv or (runs_dir / "results.tsv")
        if not results_path.exists():
            return
        content = results_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        for i in range(len(lines) - 1, 0, -1):
            parts = lines[i].split("\t")
            if len(parts) < 12:
                continue
            row_run = parts[0]
            row_round = parts[13] if len(parts) >= 14 else ""
            if row_run != run_name:
                continue
            if round_num is not None and row_round != str(round_num):
                continue
            if len(parts) >= 12:
                parts[11] = verdict
                lines[i] = "\t".join(parts)
            break
        results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── misc (delegating to budget.py / state_utils.py) ────────────

    def _round_cooldown(self) -> float:
        return _budget.round_cooldown(self._get_study())

    def _maybe_load_previous_summary(self, study: StudyRecord) -> dict | None:
        return _budget.maybe_load_previous_summary(study)

    def _mark_terminal(self, status: StudyStatus, *, last_metrics=None, last_error=None, reason=None):
        """Persist a terminal status to the DB (best-effort, H2)."""
        _state_utils.mark_terminal(
            self.study_store, self._get_study().study_id, status,
            last_metrics=last_metrics, last_error=last_error, reason=reason,
        )

    async def _wait_until_resumed(self):
        await _state_utils.wait_until_resumed(self.control)

    def _emit(self, session_id: str, event: str, data: dict) -> None:
        # v4 observability: state_utils.emit_with_trace attaches the live
        # trace context (trace_id / study_id / round_num) to every SSE
        # event so the UI can show a copyable trace_id for log correlation.
        _state_utils.emit_with_trace(self.emitter, session_id, event, data)

    def _open_goal_store(self):
        return _state_utils.open_goal_store()

    @staticmethod
    def _format_directives(directives) -> str:
        return _state_utils.format_directives(directives)

    def _record_evolution_observation(self) -> None:
        """Record fitness for this study + trigger GA evolution if due.

        Called once per study completion (from _run_lifecycle).  Failures
        are silently swallowed — evolution is best-effort observability.
        """
        try:
            from .loop_evolution import maybe_evolve, record_observation
            study = self._get_study()
            record_observation(self.study_store, study)
            maybe_evolve(self.study_store)
        except Exception:  # noqa: BLE001 — evolution must never break runs
            logger.debug("evolution observation failed", exc_info=True)

    def _run_monitor_check(self) -> dict:
        """Re-backtest the last keep run and check if metrics still meet targets.

        Called by the monitor phase. Returns a dict with:
        - meets_targets: bool
        - metrics: dict
        - now_iso: str
        - verdict: "monitor"
        - reason: human-readable drift description ("all targets met"
          when on target; carries backtest-failure notes when the
          re-run could not execute)

        §15.2 semantics: the check must RE-RUN the kept strategy, not
        re-read ``state.best_metrics`` — comparing a stored snapshot to
        itself can never detect drift.
        """
        from datetime import datetime, timezone

        from strategy_research.core.backtest import run_backtest_script
        from strategy_research.core.study import state_store as ss

        study = self._get_study()
        sid = study.study_id
        path = Path(study.workspace_path).resolve()
        state = ss.load(path, sid)

        # Re-backtest the last keep run (its own run dir supplies
        # strategy.py / config.yaml / results.tsv).
        metrics: dict = {}
        notes: list[str] = []
        keep_rel = state.last_keep_run_dir
        keep_dir = ss.study_root(path, sid) / keep_rel if keep_rel else None
        if keep_dir is not None and (keep_dir / "strategy.py").exists():
            try:
                bt = run_backtest_script(
                    workspace_path=path,
                    strategy_name=study.strategy_name,
                    action="monitor",
                    strategy_dir=keep_dir,
                )
            except Exception as exc:  # noqa: BLE001 — reported via reason
                logger.warning("monitor re-backtest crashed study=%s: %s", sid, exc)
                bt = {"success": False, "error": str(exc)}
            if bt.get("success"):
                metrics = dict(bt.get("metrics") or {})
            else:
                notes.append(f"re-backtest failed: {bt.get('error') or 'unknown error'}")
                metrics = dict(state.best_metrics or {})
        else:
            notes.append(
                f"keep run unavailable: {keep_rel or 'none recorded'}"
            )
            metrics = dict(state.best_metrics or {})

        from strategy_research.core.study.metric_targets import target_failures
        failures = target_failures(metrics, study.metric_targets or [])
        meets = bool(study.metric_targets) and not failures
        notes.extend(failures)

        return {
            "meets_targets": meets,
            "metrics": metrics,
            "now_iso": datetime.now(timezone.utc).isoformat(),
            "verdict": "monitor",
            "reason": "; ".join(notes) if notes else "all targets met",
        }


# ── Backward-compat alias ──────────────────────────────────────────
AutoresearchExecutor = AutoresearchRunner
