"""LeverScoreboard 单元测试（goal 域：杠杆效果 Beta 后验 + 时间衰减 + 疲劳检测）。

模块此前 0% 覆盖。纯逻辑、无 IO，直接注入数据即可覆盖全部路径。
"""
from __future__ import annotations

import pytest

from agentnodes.apps.goal.scoreboard import (
    DECAY,
    LeverScoreboard,
    LeverStats,
    _map_researcher_action_to_lever,
    _map_strategist_action_to_lever,
)


class TestLeverMapping:
    def test_strategist_mapping(self):
        assert _map_strategist_action_to_lever("integrate") == "action"
        assert _map_strategist_action_to_lever("optimize") == "configuration"
        assert _map_strategist_action_to_lever("remove") == "control"
        assert _map_strategist_action_to_lever("whatever") == "action"

    def test_researcher_mapping(self):
        assert _map_researcher_action_to_lever("search_external") == "action"
        assert _map_researcher_action_to_lever("discover_local") == "instruction"
        assert _map_researcher_action_to_lever("optimize_param") == "configuration"
        assert _map_researcher_action_to_lever("remove_factor") == "control"
        assert _map_researcher_action_to_lever("unknown") == "instruction"


class TestUpdateMath:
    def test_accepted_counters_and_precision(self):
        sb = LeverScoreboard()
        sb.update(
            levers=["integrate"],
            attribution={"calmar": "flipped", "sharpe": "still_F"},
            gating_outcome="accepted",
            current_round=5, round_num=5,
        )
        stats = next(s for s in sb.get_scoreboard() if s.lever == "action")
        assert stats.attempts == 1 and stats.accepted == 1 and stats.reverted == 0
        assert stats.precision_hits == 1 and stats.precision_total == 2
        assert stats.raw_precision == 0.5

    def test_reverted_counter(self):
        sb = LeverScoreboard()
        sb.update(levers=["remove"], attribution={},
                  gating_outcome="reverted", current_round=1, round_num=1)
        stats = next(s for s in sb.get_scoreboard() if s.lever == "control")
        assert stats.reverted == 1 and stats.accepted == 0

    def test_side_effects_count_as_misses(self):
        """regressed 计 side_effects：raw_precision 分母包含它。"""
        sb = LeverScoreboard()
        sb.update(
            levers=["optimize"],
            attribution={"a": "flipped", "b": "regressed", "c": "still_F"},
            gating_outcome="accepted", current_round=1, round_num=1,
        )
        stats = next(s for s in sb.get_scoreboard() if s.lever == "configuration")
        assert stats.side_effects == 1
        # attributed = 非 absent 全部 = 3（flipped/regressed/still_F 都算已归因）
        # raw_precision = 1 hit / (3 attributed + 1 side_effect) = 1/4
        assert stats.raw_precision == pytest.approx(0.25)
        # weighted: hits=1, misses=(3-1)+1=3 → effective_n=4
        assert stats.effective_n == pytest.approx(4.0)

    def test_absent_outcomes_not_attributed(self):
        sb = LeverScoreboard()
        sb.update(levers=["integrate"], attribution={"x": "absent"},
                  gating_outcome="accepted", current_round=1, round_num=1)
        stats = next(s for s in sb.get_scoreboard() if s.lever == "action")
        assert stats.precision_total == 0

    def test_time_decay_weights(self):
        """current_round > round_num → 权重 <1（越旧越轻）。"""
        sb = LeverScoreboard()
        sb.update(levers=["integrate"], attribution={"a": "flipped"},
                  gating_outcome="accepted", current_round=10, round_num=8)
        stats = next(s for s in sb.get_scoreboard() if s.lever == "action")
        assert stats.weighted_hits == pytest.approx(DECAY ** 1)  # 10-8-1=1


class TestPosteriorAndBest:
    def test_cold_start_posterior_is_half(self):
        s = LeverStats(lever="action")
        assert s.posterior_mean == 0.5

    def test_posterior_shifts_with_hits(self):
        s = LeverStats(lever="action", weighted_hits=9.0, weighted_misses=1.0)
        # Beta(1+9, 1+1) → 10/12
        assert s.posterior_mean == pytest.approx(10 / 12)

    def test_get_best_lever(self):
        sb = LeverScoreboard()
        sb.update(levers=["integrate"], attribution={"a": "flipped"},
                  gating_outcome="accepted", current_round=1, round_num=1)
        assert sb.get_best_lever() == "action"

    def test_get_best_lever_none_when_no_data(self):
        assert LeverScoreboard().get_best_lever() is None


class TestFatigue:
    def test_short_history_never_fatigued(self):
        sb = LeverScoreboard()
        for _ in range(2):
            sb.update(levers=["integrate"], attribution={"a": "still_F"},
                      gating_outcome="accepted", current_round=1, round_num=1)
        assert sb.is_lever_fatigued("action") is False

    def test_flat_low_precision_is_fatigued(self):
        sb = LeverScoreboard()
        for _ in range(3):
            sb.update(levers=["integrate"], attribution={"a": "still_F"},
                      gating_outcome="accepted", current_round=1, round_num=1)
        assert sb.is_lever_fatigued("action") is True

    def test_improving_precision_not_fatigued(self):
        sb = LeverScoreboard()
        # 3 轮：precision 0 → 0.5 → 1.0（每步增益 ≥ 0.01）
        sb.update(levers=["integrate"], attribution={"a": "still_F"},
                  gating_outcome="accepted", current_round=1, round_num=1)
        sb.update(levers=["integrate"], attribution={"a": "flipped"},
                  gating_outcome="accepted", current_round=2, round_num=2)
        sb.update(levers=["integrate"], attribution={"a": "flipped"},
                  gating_outcome="accepted", current_round=3, round_num=3)
        assert sb.is_lever_fatigued("action") is False

    def test_perfect_precision_never_fatigued(self):
        sb = LeverScoreboard()
        for _ in range(4):
            sb.update(levers=["integrate"], attribution={"a": "flipped"},
                      gating_outcome="accepted", current_round=1, round_num=1)
        assert sb.is_lever_fatigued("action") is False

    def test_unknown_lever_maps_to_instruction(self):
        assert LeverScoreboard().is_lever_fatigued("nonsense") is False


class TestContextRendering:
    def test_context_lists_all_levers_and_best(self):
        sb = LeverScoreboard()
        sb.update(levers=["integrate"], attribution={"a": "flipped"},
                  gating_outcome="accepted", current_round=1, round_num=1)
        ctx = sb.build_scoreboard_context()
        assert ctx.startswith("<lever-scoreboard>")
        assert ctx.endswith("</lever-scoreboard>")
        assert "action" in ctx and "未使用" in ctx        # 无数据杠杆如实标注
        assert "建议优先: action" in ctx

    def test_fatigued_lever_gets_stale_tag(self):
        sb = LeverScoreboard()
        for _ in range(3):
            sb.update(levers=["integrate"], attribution={"a": "still_F"},
                      gating_outcome="accepted", current_round=1, round_num=1)
        ctx = sb.build_scoreboard_context()
        assert "[stale]" in ctx
