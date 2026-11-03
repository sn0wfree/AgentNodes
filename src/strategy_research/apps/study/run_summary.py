"""W-split: run summary generation/persistence (from autoresearch)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _extract_actions(agent_outputs: dict[str, dict]) -> list[str]:
    """从 agent outputs 提取 actions 列表。

    Args:
        agent_outputs: {"agent_name": output_dict}

    Returns:
        actions 列表 (e.g., ["researcher: search_external", "change: top_n=5"])
    """
    actions = []

    researcher = agent_outputs.get("researcher", {})
    if researcher.get("action"):
        actions.append(f"researcher: {researcher['action']}")

    strategist = agent_outputs.get("strategist", {})
    if strategist.get("action"):
        actions.append(f"strategist: {strategist['action']}")

    for change in strategist.get("changes", []):
        actions.append(f"change: {change.get('param', '?')}={change.get('new', '?')}")

    return actions


def _summarize_each_agent(agent_outputs: dict[str, dict]) -> dict[str, str]:
    """每个 Agent 一句话总结 (~50-100 chars)。

    Args:
        agent_outputs: {"agent_name": output_dict}

    Returns:
        {"agent_name": "summary_text"}
    """
    summaries = {}

    # Researcher
    researcher = agent_outputs.get("researcher", {})
    if researcher:
        action = researcher.get("action", "?")
        direction = researcher.get("factor_direction", "")
        hypothesis = researcher.get("hypothesis", "")[:40]
        summaries["researcher"] = f"{action} | {direction} | {hypothesis}"[:100]

    # Data Quality
    data_quality = agent_outputs.get("data_quality", {})
    if data_quality:
        passed = data_quality.get("passed", False)
        warnings_count = len(data_quality.get("warnings", []))
        summaries["data_quality"] = f"passed={passed} | warnings={warnings_count}"

    # Factor Analyst
    factor = agent_outputs.get("factor_analyst", {})
    if factor:
        candidates = factor.get("candidates", [])
        rec = factor.get("recommendation", "")[:30]
        summaries["factor_analyst"] = f"{len(candidates)} candidates | {rec}"

    # Strategist
    strategist = agent_outputs.get("strategist", {})
    if strategist:
        changes = strategist.get("changes", [])
        action = strategist.get("action", "?")
        summaries["strategist"] = f"{action} | {len(changes)} changes"

    # Portfolio Construction
    portfolio = agent_outputs.get("portfolio_construction", {})
    if portfolio:
        method = portfolio.get("method", "?")
        vol = portfolio.get("portfolio_vol", 0)
        summaries["portfolio_construction"] = f"{method} | vol={vol:.3f}"[:80]

    # Risk Controller
    risk = agent_outputs.get("risk_controller", {})
    if risk:
        passed = risk.get("risk_passed", False)
        rating = risk.get("risk_rating", "?")
        summaries["risk_controller"] = f"passed={passed} | rating={rating}"

    # Attribution Analyst
    attribution = agent_outputs.get("attribution_analyst", {})
    if attribution:
        alpha = attribution.get("alpha", 0)
        beta = attribution.get("beta_mkt", 0)
        summaries["attribution_analyst"] = f"alpha={alpha:.4f} | beta={beta:.2f}"

    # Anti-overfit Analyst
    anti_overfit = agent_outputs.get("anti_overfit_analyst", {})
    if anti_overfit:
        verdict = anti_overfit.get("verdict", "?")
        passed = anti_overfit.get("overfit_passed", False)
        weighted_score = anti_overfit.get("weighted_score")
        if weighted_score is not None:
            summaries["anti_overfit_analyst"] = (
                f"{verdict} | score={weighted_score:.2f} | overfit_passed={passed}"
            )
        else:
            summaries["anti_overfit_analyst"] = f"{verdict} | overfit_passed={passed}"

    # Backtest Diagnostics
    diagnostics = agent_outputs.get("backtest_diagnostics", {})
    if diagnostics:
        error_type = diagnostics.get("error_type", "?")
        severity = diagnostics.get("severity", "?")
        summaries["backtest_diagnostics"] = f"error={error_type} | severity={severity}"

    return summaries


def _extract_key_insight(
    agent_outputs: dict[str, dict],
    metrics: dict[str, Any],
) -> str:
    """提取本轮关键洞察 (~100-150 chars)。

    Args:
        agent_outputs: 所有 agent 输出
        metrics: 本轮 metrics

    Returns:
        关键洞察字符串
    """
    insights = []

    # 从 anti_overfit 提取
    anti_overfit = agent_outputs.get("anti_overfit_analyst", {})
    analysis = anti_overfit.get("analysis", "")
    if analysis:
        insights.append(analysis[:80])

    # 从 attribution 提取
    attribution = agent_outputs.get("attribution_analyst", {})
    alpha = attribution.get("alpha", 0)
    if alpha:
        insights.append(f"alpha={alpha:.3f}")

    # 从 researcher 提取
    researcher = agent_outputs.get("researcher", {})
    reason = researcher.get("reason", "")
    if reason:
        insights.append(f"reason: {reason[:50]}")

    return " | ".join(insights)[:150] if insights else ""


def _compute_performance_change(
    current_metrics: dict[str, Any],
    previous_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算本轮相对于上轮的变化。

    Args:
        current_metrics: 本轮 metrics
        previous_summary: 上轮的 summary (从 summary.json 读取)

    Returns:
        {
            "calmar_delta": float,
            "sharpe_delta": float,
            "max_dd_delta": float,
            "verdict_changed": bool,
        }
    """
    if not previous_summary:
        return {}

    prev_metrics = previous_summary.get("metrics", {})
    prev_verdict = previous_summary.get("verdict", "")

    current_verdict = current_metrics.get("verdict", "")

    return {
        "calmar_delta": current_metrics.get("calmar", 0) - prev_metrics.get("calmar", 0),
        "sharpe_delta": current_metrics.get("sharpe", 0) - prev_metrics.get("sharpe", 0),
        "max_dd_delta": current_metrics.get("max_dd", 0) - prev_metrics.get("max_dd", 0),
        "verdict_changed": prev_verdict != current_verdict,
    }


def generate_run_summary(
    agent_outputs: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    verdict: str,
    round_num: int,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成 run 摘要 (~300 chars)。

    借鉴 OpenCode CompactionPart 的设计,提供快速访问层而不删除原始 agents/。

    Args:
        agent_outputs: 所有 agent 的输出 {"agent_name": output_dict}
        metrics: 本轮 metrics (calmar, sharpe, max_dd, ann_return, ann_vol, turnover)
        verdict: 本轮 verdict (keep/discard)
        round_num: 当前轮数
        previous_summary: 上轮的 summary (用于计算 performance_change)

    Returns:
        summary dict (~300 chars):
        {
            "round": int,
            "timestamp": str,
            "verdict": "keep|discard",
            "metrics": {...},
            "actions": [...],
            "agent_summaries": {...},
            "hypothesis": str,
            "key_insight": str,
            "performance_change": {...},
        }
    """
    summary = {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "verdict": verdict,
        "metrics": {
            "calmar": metrics.get("calmar", 0),
            "sharpe": metrics.get("sharpe", 0),
            "max_dd": metrics.get("max_dd", 0),
            "ann_return": metrics.get("ann_return", 0),
            "ann_vol": metrics.get("ann_vol", 0),
            "turnover": metrics.get("turnover", 0),
        },
        "actions": _extract_actions(agent_outputs),
        "agent_summaries": _summarize_each_agent(agent_outputs),
        "hypothesis": agent_outputs.get("researcher", {}).get("hypothesis", ""),
        "key_insight": _extract_key_insight(agent_outputs, metrics),
    }

    # 计算 performance_change (需要包含 verdict 用于对比)
    metrics_with_verdict = dict(metrics)
    metrics_with_verdict["verdict"] = verdict
    summary["performance_change"] = _compute_performance_change(
        metrics_with_verdict, previous_summary
    )

    return summary


def save_run_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    """保存 summary.json 到 run_dir。

    Args:
        run_dir: run 目录路径 (e.g., runs/run_0042/)
        summary: generate_run_summary() 返回的 dict

    Returns:
        保存的文件路径
    """
    summary_file = run_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary_file


def load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    """快速加载 summary.json (~300 chars)。

    Args:
        run_dir: run 目录路径

    Returns:
        summary dict 或 None (如果不存在)
    """
    summary_file = run_dir / "summary.json"
    if summary_file.exists():
        try:
            return json.loads(summary_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


# ============================================================
# Layered Read Configuration (Phase 2)
# ============================================================

# DEFAULT_KEEP_RECENT 已在文件顶部定义


