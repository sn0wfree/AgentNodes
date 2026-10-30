"""研究域共享契约：调参常量与停因枚举。

**为什么单独成模块**（docs/break-circular-import-design-20261030.md）：
这些是 `runner`（执行循环）与 `phase_engine`（阶段函数）**两侧都要用的
词汇表**。原住在 `runner.py` 导致 `phase_engine` 必须 import `runner`，
与 `runner → phase_engine`（调用 `run_round_phases`）构成循环。

它们是纯数据（无运行时依赖），正确位置是独立契约模块，而不是任何
一方的实现文件。**值必须与搬迁前逐字相同**——本模块只搬不改。

`runner.py` 保留 re-export，外部 `from .runner import ShutdownReason`
等旧路径继续可用（re-export 不产生循环：本模块不 import runner）。
"""
from __future__ import annotations

# ── v2 review-cycle tuning (design §10/§11) ─────────────────────────
SR_STUDY_MAX_DEVIATION = 3          # consecutive high deviations → stop
SR_STUDY_COLLECT_INTERVAL = 5       # force info collection every K rounds
SR_STUDY_MAX_DISCARD = 5            # consecutive discards → stagnation stop

# ── Agent resilience (Step B of round/retry/loop fix) ───────────────
SR_AGENT_MAX_ITER = 50              # high-iteration default for complex agents
SR_AGENT_NO_PROGRESS_WINDOW = 5     # tolerate 5 identical tool calls before approval gate
SR_AGENT_MAX_PARSE_RETRIES = 2      # retries per round on parse_failed
SR_AGENT_PARSE_BACKOFF_BASE = 5.0   # base seconds for parse_failed backoff
SR_AGENT_PARSE_BACKOFF_MAX = 30.0   # cap on backoff wait


class ShutdownReason:
    """study 生命周期终止原因（Director 的停因映射以此为输入）。"""

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


# Agent names whose parse_failed output triggers round-level retry
_PARSE_FAILED_AGENT_KEYS: tuple[str, ...] = (
    "data_quality_output",
    "factor_analyst_output",
    "strategist_output",
    "portfolio_construction_output",
)


def parse_failed_agents(exec_result: dict) -> list[str]:
    """Return the names of agents that returned parse_failed for this round."""
    failed: list[str] = []
    for key in _PARSE_FAILED_AGENT_KEYS:
        out = exec_result.get(key)
        if isinstance(out, dict) and out.get("error") == "parse_failed":
            # key like "data_quality_output" → "data_quality"
            failed.append(key[: -len("_output")])
    return failed


__all__ = [
    "SR_STUDY_MAX_DEVIATION",
    "SR_STUDY_COLLECT_INTERVAL",
    "SR_STUDY_MAX_DISCARD",
    "SR_AGENT_MAX_ITER",
    "SR_AGENT_NO_PROGRESS_WINDOW",
    "SR_AGENT_MAX_PARSE_RETRIES",
    "SR_AGENT_PARSE_BACKOFF_BASE",
    "SR_AGENT_PARSE_BACKOFF_MAX",
    "ShutdownReason",
    "parse_failed_agents",
]
