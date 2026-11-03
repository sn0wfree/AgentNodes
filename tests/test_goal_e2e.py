"""E2E tests for the goal system with real data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.agent.builtin_tools import (
    FactorCrossSectionalAnalysis,
    FactorICDecay,
    FactorQuintileReturns,
)
from strategy_research.kernel.agent.builtin_tools.data_tools import ImportDataTool
from strategy_research.core.agent.tools import ToolContext
from strategy_research.apps.config_runner import load_data
from strategy_research.apps.db import init_db, save_ohlcv_to_db
from strategy_research.apps.goal.context import (
    format_goal_context,
    format_goal_continuation_prompt,
    goal_needs_continuation,
    goal_progress_tuple,
)
from strategy_research.apps.goal.models import (
    EvidenceInput,
    GoalStatus,
)
from strategy_research.apps.goal.policy import reject_live_execution_objective
from strategy_research.apps.goal.store import GoalStore

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def goal_store(tmp_path: Path) -> GoalStore:
    return GoalStore(db_path=str(tmp_path / "goals.db"))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    init_db(tmp_path)
    return tmp_path


@pytest.fixture
def real_market_data(workspace: Path) -> dict:
    import numpy as np
    import pandas as pd
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=252, freq="B")
    stocks = {
        "000001.SZ": 12.0, "600519.SH": 1800.0, "000858.SZ": 150.0,
        "601318.SH": 45.0, "000333.SZ": 55.0, "600036.SH": 35.0,
        "000651.SZ": 38.0, "601012.SH": 25.0, "300750.SZ": 200.0,
        "002594.SZ": 250.0,
    }
    data = {}
    for code, base in stocks.items():
        returns = np.random.randn(len(dates)) * 0.02
        prices = base * np.exp(np.cumsum(returns))
        stock_data = []
        for d, close in zip(dates, prices):
            stock_data.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": round(close * 0.99, 2),
                "high": round(close * 1.01, 2),
                "low": round(close * 0.98, 2),
                "close": round(close, 2),
                "volume": int(np.random.lognormal(15, 0.5)),
            })
        data[code] = stock_data
    return data


@pytest.fixture
def populated_workspace(workspace: Path, real_market_data: dict) -> Path:
    tool = ImportDataTool()
    result = json.loads(tool.execute(ctx=ToolContext(workspace=workspace), data=real_market_data))
    assert result["status"] == "ok"
    return workspace


# ── E2E: Goal Lifecycle ─────────────────────────────────────────────


class TestGoalLifecycleE2E:
    def test_create_goal_with_criteria(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-lifecycle",
            objective="分析20日动量因子在A股的IC表现",
            criteria=["获取A股行情数据", "计算横截面IC", "分析五分位收益", "生成研究报告"],
        )
        assert goal.goal_id is not None
        assert goal.status == GoalStatus.ACTIVE
        current = goal_store.get_current_goal("e2e-lifecycle")
        assert current.objective == "分析20日动量因子在A股的IC表现"

    def test_add_evidence_and_track_progress(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-evidence",
            objective="分析20日动量因子",
            criteria=["数据获取", "IC分析", "报告生成"],
        )
        criteria = goal_store.list_criteria(goal.goal_id)
        assert len(criteria) == 3

        evidence = EvidenceInput(
            text="已获取10只A股252个交易日的OHLCV数据",
            criterion_id=criteria[0].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence,
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            session_id="e2e-evidence",
        )
        records = goal_store.list_evidence(goal.goal_id)
        assert len(records) == 1
        assert "OHLCV" in records[0].text

    def test_complete_goal_lite(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-complete",
            objective="分析20日动量因子",
            criteria=["数据获取", "IC分析"],
        )
        criteria = goal_store.list_criteria(goal.goal_id)
        for c in criteria:
            evidence = EvidenceInput(text="已完成", criterion_id=c.criterion_id)
            goal_store.append_evidence(
                evidence=evidence,
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                session_id="e2e-complete",
            )
        completed = goal_store.complete_lite(
            goal_id=goal.goal_id,
            session_id="e2e-complete",
            expected_goal_id=goal.goal_id,
        )
        assert completed.status == GoalStatus.COMPLETE


# ── E2E: Context Injection ──────────────────────────────────────────


class TestGoalContextE2E:
    def test_goal_context_format(self, goal_store: GoalStore):
        goal_store.replace_goal(session_id='e2e-context', objective='分析20日动量因子IC', criteria=['数据获取', 'IC分析'])
        snapshot = goal_store.get_current_snapshot("e2e-context")
        context = format_goal_context(snapshot)
        assert "current-research-goal" in context
        assert "分析20日动量因子IC" in context

    def test_goal_continuation_prompt(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-continuation",
            objective="分析20日动量因子",
            criteria=["数据获取", "IC分析", "报告生成"],
        )
        criteria = goal_store.list_criteria(goal.goal_id)
        evidence = EvidenceInput(text="已获取数据", criterion_id=criteria[0].criterion_id)
        goal_store.append_evidence(
            evidence=evidence,
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            session_id="e2e-continuation",
        )
        snapshot = goal_store.get_current_snapshot("e2e-continuation")
        prompt = format_goal_continuation_prompt(snapshot)
        assert "goal-continuation" in prompt

    def test_needs_continuation(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-continue-check",
            objective="分析动量因子",
            criteria=["数据获取", "IC分析"],
        )
        snapshot = goal_store.get_current_snapshot("e2e-continue-check")
        needs = goal_needs_continuation(snapshot)
        assert needs is True

        criteria = goal_store.list_criteria(goal.goal_id)
        for c in criteria:
            evidence = EvidenceInput(text="已完成", criterion_id=c.criterion_id)
            goal_store.append_evidence(
                evidence=evidence,
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                session_id="e2e-continue-check",
            )
        covered, evidence_count = goal_progress_tuple(
            goal_store.get_current_snapshot("e2e-continue-check")
        )
        assert covered == 2
        assert evidence_count == 2


# ── E2E: Factor Analysis + Goal Integration ─────────────────────────


class TestFactorAnalysisGoalE2E:
    def test_factor_analysis_produces_evidence(self, populated_workspace: Path, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-factor-goal",
            objective="分析20日动量因子在A股的IC表现",
            criteria=["横截面IC分析", "五分位收益分析", "IC衰减分析"],
        )
        criteria = goal_store.list_criteria(goal.goal_id)

        ic_tool = FactorCrossSectionalAnalysis()
        ic_result = json.loads(ic_tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert ic_result["status"] == "ok"
        evidence = EvidenceInput(
            text=f"Pearson IC={ic_result.get('ic_pearson_mean')}, IR={ic_result.get('ir')}",
            criterion_id=criteria[0].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-factor-goal",
        )

        quintile_tool = FactorQuintileReturns()
        quintile_result = json.loads(quintile_tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert quintile_result["status"] == "ok"
        evidence2 = EvidenceInput(
            text=f"多空价差={quintile_result.get('long_short_spread')}",
            criterion_id=criteria[1].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence2, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-factor-goal",
        )

        decay_tool = FactorICDecay()
        decay_result = json.loads(decay_tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert decay_result["status"] == "ok"
        evidence3 = EvidenceInput(
            text=f"IC衰减曲线={decay_result.get('ic_decay')}",
            criterion_id=criteria[2].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence3, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-factor-goal",
        )

        records = goal_store.list_evidence(goal.goal_id)
        assert len(records) == 3
        covered, _ = goal_progress_tuple(goal_store.get_current_snapshot("e2e-factor-goal"))
        assert covered == 3


# ── E2E: Data Pipeline ──────────────────────────────────────────────


class TestDataPipelineE2E:
    def test_import_then_load_data(self, workspace: Path, real_market_data: dict):
        tool = ImportDataTool()
        result = json.loads(tool.execute(ctx=ToolContext(workspace=workspace), data=real_market_data))
        assert result["status"] == "ok"
        cfg = {
            "strategy": {"name": "default"},
            "data": {"source": "duckdb", "start_date": "2023-01-01", "end_date": "2023-12-31"},
        }
        df = load_data(cfg, workspace)
        assert not df.empty

    def test_config_runner_with_cache(self, workspace: Path):
        import numpy as np
        import pandas as pd
        dates = pd.date_range("2023-01-01", periods=30, freq="B")
        data_map = {}
        for code in ["000001.SZ", "600519.SH"]:
            np.random.seed(hash(code) % 2**31)
            prices = 100 * np.exp(np.cumsum(np.random.randn(30) * 0.02))
            data_map[code] = pd.DataFrame({
                "date": dates.strftime("%Y-%m-%d"),
                "open": prices * 0.99, "high": prices * 1.01,
                "low": prices * 0.98, "close": prices,
                "volume": [1000000] * 30,
            })
        save_ohlcv_to_db(workspace, data_map, "test_strat")
        cfg = {
            "strategy": {"name": "test_strat"},
            "data": {"source": "duckdb", "codes": ["000001.SZ", "600519.SH"],
                     "start_date": "2023-01-01", "end_date": "2023-12-31"},
        }
        df = load_data(cfg, workspace)
        assert not df.empty


# ── E2E: Goal Supersession ──────────────────────────────────────────


class TestGoalSupersessionE2E:
    def test_new_goal_supersedes_old(self, goal_store: GoalStore):
        goal_store.replace_goal(session_id='e2e-supersede', objective='第一个目标', criteria=['标准1'])
        goal2 = goal_store.replace_goal(
            session_id="e2e-supersede", objective="第二个目标", criteria=["标准2"],
        )
        current = goal_store.get_current_goal("e2e-supersede")
        assert current.goal_id == goal2.goal_id
        all_goals = goal_store.list_goals("e2e-supersede")
        superseded = [g for g in all_goals if g.status == GoalStatus.SUPERSEDED]
        assert len(superseded) == 1

    def test_budget_tracking(self, goal_store: GoalStore):
        goal = goal_store.replace_goal(
            session_id="e2e-budget", objective="快速分析", criteria=["分析"],
            token_budget=10000, turn_budget=5, time_budget_seconds=300,
        )
        goal_store.account_usage(
            goal_id=goal.goal_id, session_id="e2e-budget",
            expected_goal_id=goal.goal_id,
            token_delta=5000, turn_delta=2, time_delta_seconds=60,
        )
        current = goal_store.get_current_goal("e2e-budget")
        assert current.tokens_used == 5000
        assert current.turns_used == 2


# ── E2E: Policy Enforcement ─────────────────────────────────────────


class TestGoalPolicyE2E:
    def test_reject_live_execution(self):
        with pytest.raises(ValueError):
            reject_live_execution_objective("Execute trade now")
        with pytest.raises(ValueError):
            reject_live_execution_objective("下单买入")
        with pytest.raises(ValueError):
            reject_live_execution_objective("buy 100 shares now")

    def test_accept_research_objectives(self):
        reject_live_execution_objective("分析动量因子IC")
        reject_live_execution_objective("Analyze momentum factor")
        reject_live_execution_objective("研究市场风险")


# ── E2E: Strategy Auto-Iteration ────────────────────────────────────


class TestStrategyAutoIterationE2E:
    def test_iterate_strategy_parameters(self, populated_workspace: Path, goal_store: GoalStore):
        import numpy as np
        goal = goal_store.replace_goal(
            session_id="e2e-iteration",
            objective="优化20日动量因子策略参数",
            criteria=["测试不同持仓数", "测试不同调仓频率", "选择最优参数"],
        )
        criteria = goal_store.list_criteria(goal.goal_id)

        best_sharpe = -999
        best_params = None
        for top_n in [5, 10, 20]:
            for freq in [5, 10, 20]:
                sharpe = np.random.randn() * 0.5 + 1.0
                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = {"top_n": top_n, "freq": freq}

        evidence1 = EvidenceInput(
            text=f"最优参数: top_n={best_params['top_n']}, freq={best_params['freq']}, Sharpe={best_sharpe:.2f}",
            criterion_id=criteria[0].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence1, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-iteration",
        )

        evidence2 = EvidenceInput(
            text="5日调仓Sharpe=1.2, 10日Sharpe=0.9, 20日Sharpe=0.7",
            criterion_id=criteria[1].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence2, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-iteration",
        )

        evidence3 = EvidenceInput(
            text=f"选择: {best_params}",
            criterion_id=criteria[2].criterion_id,
        )
        goal_store.append_evidence(
            evidence=evidence3, goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, session_id="e2e-iteration",
        )

        covered, count = goal_progress_tuple(goal_store.get_current_snapshot("e2e-iteration"))
        assert covered == 3
        assert count == 3


# ── E2E: Multi-Goal Session ─────────────────────────────────────────


class TestMultiGoalSessionE2E:
    def test_sequential_goals(self, goal_store: GoalStore):
        goal1 = goal_store.replace_goal(
            session_id="e2e-multi", objective="分析动量因子IC", criteria=["IC分析"],
        )
        criteria1 = goal_store.list_criteria(goal1.goal_id)
        evidence = EvidenceInput(text="IC分析完成", criterion_id=criteria1[0].criterion_id)
        goal_store.append_evidence(
            evidence=evidence, goal_id=goal1.goal_id,
            expected_goal_id=goal1.goal_id, session_id="e2e-multi",
        )
        goal_store.complete_lite(
            goal_id=goal1.goal_id,
            session_id="e2e-multi",
            expected_goal_id=goal1.goal_id,
        )

        goal2 = goal_store.replace_goal(
            session_id="e2e-multi", objective="创建策略", criteria=["设计", "回测"],
        )
        current = goal_store.get_current_goal("e2e-multi")
        assert current.goal_id == goal2.goal_id

        all_goals = goal_store.list_goals("e2e-multi")
        completed = [g for g in all_goals if g.status == GoalStatus.COMPLETE]
        active = [g for g in all_goals if g.status == GoalStatus.ACTIVE]
        assert len(completed) == 1
        assert len(active) == 1
