"""Tests for goal/scoreboard.py — AEGIS Lever Scoreboard."""
from strategy_research.apps.goal.scoreboard import (
    FATIGUE_WINDOW,
    VALID_LEVERS,
    LeverScoreboard,
)


class TestLeverScoreboard:
    def test_init(self):
        sb = LeverScoreboard()
        assert len(sb.get_scoreboard()) == len(VALID_LEVERS)

    def test_update_single_round(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 1, 1)
        best = sb.get_best_lever()
        assert best == "action"

    def test_posterior_after_one_round(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped", "sharpe": "still_F"}, "accepted", 1, 1)
        stats = sb._stats["action"]
        assert stats.attempts == 1
        assert stats.accepted == 1
        assert stats.precision_hits == 1
        assert stats.precision_total == 2
        # 1 hit + 1 still_F = posterior 0.5 (neutral)
        assert stats.posterior_mean == 0.5

    def test_posterior_with_reverted(self):
        sb = LeverScoreboard()
        sb.update(["optimize"], {"calmar": "regressed"}, "reverted", 1, 1)
        stats = sb._stats["configuration"]
        assert stats.reverted == 1
        assert stats.posterior_mean < 0.5

    def test_best_lever_empty(self):
        sb = LeverScoreboard()
        assert sb.get_best_lever() is None

    def test_multiple_levers(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 1, 1)
        sb.update(["optimize"], {"calmar": "regressed"}, "reverted", 2, 2)
        best = sb.get_best_lever()
        assert best == "action"

    def test_lever_name_normalization(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 1, 1)
        assert sb._stats["action"].attempts == 1

        sb.update(["optimize"], {"calmar": "flipped"}, "accepted", 2, 2)
        assert sb._stats["configuration"].attempts == 1

        sb.update(["remove"], {"calmar": "flipped"}, "accepted", 3, 3)
        assert sb._stats["control"].attempts == 1

    def test_no_fatigue_initially(self):
        sb = LeverScoreboard()
        assert not sb.is_lever_fatigued("action")

    def test_fatigue_detection(self):
        sb = LeverScoreboard()
        # 4 rounds with no precision gain
        for i in range(1, FATIGUE_WINDOW + 2):
            sb.update(["integrate"], {"calmar": "still_F"}, "reverted", i, i)
        assert sb.is_lever_fatigued("action")

    def test_no_fatigue_with_improvement(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 1, 1)
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 2, 2)
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 3, 3)
        assert not sb.is_lever_fatigued("action")

    def test_build_scoreboard_context(self):
        sb = LeverScoreboard()
        sb.update(["integrate"], {"calmar": "flipped"}, "accepted", 1, 1)
        ctx = sb.build_scoreboard_context()
        assert "<lever-scoreboard>" in ctx
        assert "action" in ctx
        assert "posterior=" in ctx

    def test_build_scoreboard_context_empty(self):
        sb = LeverScoreboard()
        ctx = sb.build_scoreboard_context()
        assert "未使用" in ctx
