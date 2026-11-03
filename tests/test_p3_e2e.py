"""P3 E2E tests — full integration of Goal + Hypothesis + Validation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.foundation.llm.openai_client import LLMConfig
from strategy_research.apps.validation import run_validation
from strategy_research.apps.validation.trade_input import TradeInput


@pytest.fixture
def cfg() -> LLMConfig:
    return LLMConfig(api_key="sk-test", base_url="https://example.com/v1", model="m")


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture(autouse=True)
def isolated_subsystems(tmp_path, monkeypatch):
    goals_db = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(goals_db))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))


# ─── Goal → Hypothesis → Validation full lifecycle ───────────────────────


class TestGoalHypothesisValidationLifecycle:
    def test_full_e2e(self, cfg, registry, tmp_path, monkeypatch):
        """Start a goal, run AgentLoop (auto-creates hypothesis), validate."""
        from strategy_research.apps.goal import GoalStore
        store = GoalStore()
        store.replace_goal(session_id='sess_e2e', objective='Validate momentum hypothesis via Monte Carlo', criteria=['Define universe', 'Run backtest', 'Validate'])

        # Mock the LLM to return stop immediately
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100

        loop = AgentLoop(
            config=cfg, registry=registry,
            session_id="sess_e2e", strategy_name="momentum_e2e",
            max_iterations=1,
        )
        monkeypatch.setattr(loop.client, "chat", lambda *a, **k: mock_response)
        loop.run("validate this momentum strategy")

        # 1. Goal exists
        assert store.get_current_goal("sess_e2e") is not None

        # 2. Hypothesis was auto-created
        from strategy_research.apps.hypothesis import HypothesisRegistry
        h_list = HypothesisRegistry().list()
        assert len(h_list) == 1
        hyp = h_list[0]
        assert hyp.signal_definition == "momentum_e2e"

        # 3. Run validation on a synthetic NAV
        nav = pd.Series(
            [100_000.0 * (1.001 ** i) for i in range(60)],
            index=pd.date_range("2024-01-01", periods=60, freq="D"),
        )
        trades = [
            TradeInput(
                symbol="AAPL", direction=1,
                entry_price=100.0, exit_price=101.0,
                entry_time=__import__("datetime").datetime(2024, 1, 1),
                exit_time=__import__("datetime").datetime(2024, 1, 2),
                size=1.0, pnl=1.0, pnl_pct=0.01,
            )
            for _ in range(20)
        ]
        results = run_validation(
            config={"validation": {"monte_carlo": True, "bootstrap": True, "walk_forward": True}},
            equity_curve=nav, trades=trades,
            initial_capital=100_000.0,
        )
        assert "monte_carlo" in results
        assert "bootstrap" in results
        assert "walk_forward" in results

        # 4. Link validation to hypothesis
        from strategy_research.apps.hypothesis import HypothesisRegistry
        reg = HypothesisRegistry()
        reg.link_backtest(
            hyp.hypothesis_id,
            run_card_path=str(tmp_path / "validation.json"),
            metrics={
                "sharpe": results["monte_carlo"]["actual_sharpe"],
                "p_value": results["monte_carlo"]["p_value_sharpe"],
            },
            notes="validated by E2E test",
        )
        hyp2 = reg.get(hyp.hypothesis_id)
        assert len(hyp2.run_cards) == 1

    def test_goal_evidence_audit_complete_cycle(self, tmp_path):
        """Full goal lifecycle: start → evidence → audit → complete."""
        import hashlib

        from strategy_research.apps.goal import AuditRow, EvidenceInput, GoalStatus, GoalStore

        store = GoalStore()
        goal = store.replace_goal(
            session_id="sess_lc",
            objective="Complete lifecycle test",
            criteria=["step 1", "step 2"],
        )

        # Add evidence (verified via artifacts)
        criteria = store.list_criteria(goal.goal_id)
        ev_ids = []
        for i, crit in enumerate(criteria):
            artifact = tmp_path / f"data_{i}.csv"
            artifact.write_text("x")
            digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
            ev = store.append_evidence(
                session_id="sess_lc",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(
                    text="e", criterion_id=crit.criterion_id,
                    artifact_path=str(artifact), artifact_hash=digest,
                ),
            )
            ev_ids.append(ev.evidence_id)

        # Audit + complete
        audit_rows = [
            AuditRow(
                criterion_id=crit.criterion_id,
                result="satisfied",
                evidence_ids=[ev_ids[i]],
                notes="verified",
            )
            for i, crit in enumerate(criteria)
        ]
        completed = store.update_status(
            session_id="sess_lc",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            status=GoalStatus.COMPLETE,
            audit=audit_rows,
            recap="all verified",
        )
        assert completed.status is GoalStatus.COMPLETE
        assert completed.completed_at is not None


# ─── Validation result JSON-safe ────────────────────────────────────────


class TestValidationIntegration:
    def test_validation_writes_json_file(self, tmp_path):
        """End-to-end: validate + write JSON + reload."""
        nav = pd.Series(
            [100_000.0 + i * 100.0 for i in range(60)],
            index=pd.date_range("2024-01-01", periods=60, freq="D"),
        )
        config = {"validation": {"monte_carlo": True}}
        results = run_validation(
            config=config,
            equity_curve=nav,
            trades=[TradeInput(
                symbol="x", direction=1,
                entry_price=100.0, exit_price=101.0,
                entry_time=__import__("datetime").datetime(2024, 1, 1),
                exit_time=__import__("datetime").datetime(2024, 1, 2),
                size=1.0, pnl=1.0, pnl_pct=0.01,
            ) for _ in range(10)],
            initial_capital=100_000.0,
        )
        # Round-trip via JSON
        out_path = tmp_path / "validation.json"
        out_path.write_text(json.dumps(results, allow_nan=False))
        loaded = json.loads(out_path.read_text())
        assert "monte_carlo" in loaded
        assert loaded["market"] == "a_share"


# ─── Goal context flows into agent user message ─────────────────────────


class TestGoalContextFlowsToAgent:
    def test_goal_objective_in_user_message(self, cfg, registry, monkeypatch):
        """The goal objective should appear in the user message after AgentLoop injects it."""
        from strategy_research.apps.goal import GoalStore
        GoalStore().replace_goal(
            session_id="sess_ctx_flow",
            objective="FIND_THIS_IN_USER_MSG",
            criteria=["a"],
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 50

        loop = AgentLoop(
            config=cfg, registry=registry,
            session_id="sess_ctx_flow", strategy_name="ctx_test",
            max_iterations=1,
        )
        monkeypatch.setattr(loop.client, "chat", lambda *a, **k: mock_response)
        result = loop.run("the task")

        user_msgs = [m for m in result.messages if m["role"] == "user"]
        # The task text + goal context are both in the first user message
        assert any("FIND_THIS_IN_USER_MSG" in m["content"] for m in user_msgs)
        assert any("the task" in m["content"] for m in user_msgs)
