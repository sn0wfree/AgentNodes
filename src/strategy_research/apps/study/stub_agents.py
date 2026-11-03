"""W-split: stub research-agent outputs for tests / no-LLM mode."""

from __future__ import annotations

import json
import os
import random


def _stub_agent_output(
    agent_name: str,
    current_state: dict,
    previous_outputs: list,
    behavior: str,
) -> str:
    """Stub-spawn an agent's output (test / CI path).

    Moved verbatim from ``cli/commands/autoresearch.py::_spawn_agent``
    (the stub branch only) so the study executor and CLI share one
    stub implementation.
    """
    round_num = current_state.get("total_runs", 0)

    handler = _STUB_AGENT_HANDLERS.get(agent_name)
    if handler is None:
        return json.dumps({"error": f"Unknown agent: {agent_name}"})
    return handler(round_num, previous_outputs, behavior)


def _stub_researcher(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the researcher agent."""
    if behavior == "varying":
        actions = ["search_external", "discover_local", "optimize_param", "remove_factor"]
        directions = ["momentum", "volatility", "value", "quality", "size"]
        idx = round_num % len(actions)
        return json.dumps({
            "action": actions[idx],
            "hypothesis": f"第 {round_num + 1} 轮: 尝试 {directions[idx]} 因子 ({random.randint(1, 100)})",
            "reason": f"基于上一轮结果探索 {directions[idx]} 维度",
            "avoid_actions": ["discover_local"] if round_num > 2 else [],
            "factor_direction": directions[idx],
            "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                          "narrative_bias": "pass", "confirmation_bias": "pass",
                          "recency_bias": "pass"},
        })
    if behavior == "improving":
        return json.dumps({
            "action": "optimize_param",
            "hypothesis": f"Round {round_num + 1}: 调整 top_n 参数",
            "reason": "降低 top_n 增加集中度",
            "avoid_actions": [],
            "factor_direction": "momentum",
            "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                          "narrative_bias": "pass", "confirmation_bias": "pass",
                          "recency_bias": "pass"},
        })
    return json.dumps({
        "action": "discover_local",
        "hypothesis": "波动率因子可能有效",
        "reason": "当前因子池缺少波动率维度",
        "avoid_actions": [],
        "factor_direction": "volatility",
        "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                      "narrative_bias": "pass", "confirmation_bias": "pass",
                      "recency_bias": "pass"}
    })


def _stub_data_quality(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the data_quality agent."""
    return json.dumps({
        "passed": True,
        "warnings": ["NaN 比例 0.02%"],
        "data_fingerprint": "abc123",
        "nan_ratio": 0.0002,
        "missing_days": 0,
        "price_anomalies": []
    })


def _stub_factor_analyst(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the factor_analyst agent."""
    if behavior == "varying":
        factors_pool = [
            [{"factor_name": "momentum_60d", "factor_code": "ts_return(close, 60)",
              "category": "momentum", "ic_mean": 0.045, "ir": 0.62, "overall_score": 0.68, "passed": True}],
            [{"factor_name": "vol_adj_mom", "factor_code": "ts_return(close, 20)/ts_std(return, 20)",
              "category": "momentum", "ic_mean": 0.052, "ir": 0.71, "overall_score": 0.75, "passed": True}],
            [],
            [{"factor_name": "reversal_10d", "factor_code": "-ts_return(close, 10)",
              "category": "reversal", "ic_mean": 0.038, "ir": 0.55, "overall_score": 0.62, "passed": True}],
            [],
            [{"factor_name": "momentum_120d", "factor_code": "ts_return(close, 120)",
              "category": "momentum", "ic_mean": 0.041, "ir": 0.58, "overall_score": 0.66, "passed": True}],
        ]
        candidates = factors_pool[round_num % len(factors_pool)]
        return json.dumps({
            "path_used": "local" if round_num % 2 == 0 else "alpha_zoo",
            "candidates": candidates,
            "rejected": [{"factor_name": f"bad_factor_{round_num}", "reason": "IC < 0.03"}],
            "combination_method": "ic_weighted",
            "recommendation": "建议集成新因子" if candidates else "无有效因子",
        })
    if behavior == "improving" and round_num >= 3:
        return json.dumps({
            "path_used": "local",
            "candidates": [{"factor_name": "vol_adj_mom", "factor_code": "ts_return(close, 20)/ts_std(return, 20)",
                            "category": "momentum", "ic_mean": 0.052, "ir": 0.71,
                            "overall_score": 0.75, "passed": True}],
            "rejected": [],
            "combination_method": "ic_weighted",
            "recommendation": "建议集成 vol_adj_mom",
        })
    return json.dumps({
        "path_used": "local",
        "candidates": [],
        "rejected": [
            {"factor_name": "ts_std_20d", "reason": "IC 0.018 < 0.03"}
        ],
        "combination_method": "ic_weighted",
        "recommendation": "无有效因子"
    })


def _stub_strategist(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the strategist agent."""
    if behavior == "improving" and round_num >= 3:
        return json.dumps({
            "action": "integrate",
            "changes": [{"param": "FACTOR_EXPRS", "old": [], "new": ["vol_adj_mom"]}],
            "reason": "集成 vol_adj_mom 因子",
            "expected_impact": "Calmar 提升",
        })
    return json.dumps({
        "action": "optimize",
        "changes": [],
        "reason": "无新因子,保持现有策略",
        "expected_impact": "无变化"
    })


def _stub_portfolio_construction(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the portfolio_construction agent."""
    return json.dumps({
        "method": "equal",
        "weights": {},
        "risk_contributions": {},
        "diversification_ratio": 1.0,
        "portfolio_vol": 0.15
    })


def _stub_risk_controller(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the risk_controller agent."""
    if behavior == "improving" and round_num >= 3:
        return json.dumps({
            "risk_passed": True,
            "risk_rating": "Green",
            "var_95": -0.018,
            "cvar_95": -0.025,
            "max_drawdown": -0.25,
            "stress_results": {},
            "tail_risk": {"kurtosis": 2.8, "skewness": -0.05}
        })
    return json.dumps({
        "risk_passed": False,
        "risk_rating": "Red",
        "var_95": -0.021,
        "cvar_95": -0.034,
        "max_drawdown": -0.50,
        "stress_results": {},
        "tail_risk": {"kurtosis": 3.2, "skewness": -0.15}
    })


def _stub_attribution_analyst(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the attribution_analyst agent."""
    if behavior == "improving" and round_num >= 3:
        return json.dumps({
            "alpha": 0.005 + round_num * 0.001,
            "beta_mkt": 0.85,
            "beta_smb": 0.05,
            "beta_hml": -0.02,
            "beta_mom": 0.08,
            "sector_allocation": 0.002,
            "stock_selection": 0.003 + round_num * 0.001,
            "interaction": 0.001,
            "bull_capture": 1.05,
            "bear_capture": 0.85,
            "r_squared": 0.90
        })
    return json.dumps({
        "alpha": -0.0039,
        "beta_mkt": 0.92,
        "beta_smb": 0.05,
        "beta_hml": -0.02,
        "beta_mom": 0.08,
        "sector_allocation": 0.001,
        "stock_selection": -0.005,
        "interaction": 0.001,
        "bull_capture": 0.95,
        "bear_capture": 1.12,
        "r_squared": 0.88
    })


def _stub_anti_overfit_analyst(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the anti_overfit_analyst agent."""
    metrics = {}
    if previous_outputs:
        last = previous_outputs[-1]
        if isinstance(last, dict):
            metrics = last

    try:
        calmar = float(metrics.get("calmar", 0.0)) if metrics else 0.0
    except (ValueError, TypeError):
        calmar = 0.0
    try:
        sharpe = float(metrics.get("sharpe", 0.0)) if metrics else 0.0
    except (ValueError, TypeError):
        sharpe = 0.0
    try:
        max_dd = float(metrics.get("max_dd", 0.0)) if metrics else 0.0
    except (ValueError, TypeError):
        max_dd = 0.0

    weights = {
        "start_dependency": 0.20,
        "parameter_perturbation": 0.20,
        "rebalance_offset": 0.15,
        "ablation": 0.15,
        "bootstrap": 0.15,
        "monte_carlo": 0.15,
    }

    try:
        pass_threshold = float(os.environ.get("ANTI_OVERFIT_THRESHOLD", "0.5"))
    except ValueError:
        pass_threshold = 0.5

    methods_passed = {
        "start_dependency": calmar >= 0.3,
        "rebalance_offset": abs(max_dd) <= 0.5,
        "parameter_perturbation": calmar >= 0.4,
        "ablation": calmar > 0.0,
        "bootstrap": sharpe >= 0.5,
        "monte_carlo": calmar >= 0.5 and sharpe >= 0.4,
    }

    weighted_score = sum(
        weights[k] * (1 if v else 0)
        for k, v in methods_passed.items()
    )

    if behavior == "improving" and round_num >= 4:
        for k in methods_passed:
            methods_passed[k] = True
        weighted_score = 1.0
        analysis = (
            f"所有抗过拟合方法通过 "
            f"(Calmar={calmar:.3f}, Sharpe={sharpe:.3f}, score={weighted_score:.2f})"
        )
    else:
        if weighted_score >= pass_threshold:
            analysis = (
                f"加权评分通过 "
                f"({weighted_score:.2f}, Calmar={calmar:.3f}, Sharpe={sharpe:.3f})"
            )
        else:
            failed = [k for k, v in methods_passed.items() if not v]
            analysis = (
                f"加权评分 {weighted_score:.2f} < {pass_threshold}, "
                f"失败: {', '.join(failed)}"
            )

    overfit_passed = weighted_score >= pass_threshold
    verdict = "keep" if overfit_passed else "discard"

    return json.dumps({
        "verdict": verdict,
        "overfit_passed": overfit_passed,
        "weighted_score": round(weighted_score, 3),
        "methods_passed": methods_passed,
        "analysis": analysis,
        "suggestions": [] if overfit_passed else ["调整因子参数", "增加训练数据"],
    })


def _stub_backtest_diagnostics(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the backtest_diagnostics agent."""
    return json.dumps({
        "error_type": "none",
        "severity": "info",
        "symptom": "无异常",
        "root_cause": "N/A",
        "fix_suggestion": "N/A",
        "confidence": 1.0
    })


def _stub_critic(round_num: int, previous_outputs: list, behavior: str) -> str:
    """Stub output for the critic agent."""
    if behavior == "improving" and round_num >= 2:
        approved = True
    else:
        approved = round_num >= 1
    return json.dumps({
        "approved": approved,
        "risk_rating": "low" if approved else "high",
        "concerns": [] if approved else ["过度拟合", "样本外未验证"],
        "suggested_fixes": [] if approved else ["延长样本", "加入 walk-forward 验证"],
        "confidence": 0.7 if approved else 0.4,
        "review_dimensions": {
            "risk": "pass" if approved else "fail",
            "attribution": "pass",
            "diagnostics": "pass" if approved else "fail",
            "statistics": "pass",
        },
    })


_STUB_AGENT_HANDLERS = {
    "researcher": _stub_researcher,
    "data_quality": _stub_data_quality,
    "factor_analyst": _stub_factor_analyst,
    "strategist": _stub_strategist,
    "portfolio_construction": _stub_portfolio_construction,
    "risk_controller": _stub_risk_controller,
    "attribution_analyst": _stub_attribution_analyst,
    "anti_overfit_analyst": _stub_anti_overfit_analyst,
    "backtest_diagnostics": _stub_backtest_diagnostics,
    "critic": _stub_critic,
}


# ── study single-round extraction ───────────────────────────────────


