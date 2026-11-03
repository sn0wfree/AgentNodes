"""Lever Scoreboard — tracks which modification types are most effective.

Uses Beta posterior estimation with time decay.  Also detects lever fatigue
(consecutive rounds with diminishing returns).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Valid lever types (maps to strategist action / researcher action)
VALID_LEVERS = frozenset({"configuration", "control", "action", "instruction"})

# Beta prior and decay
DECAY = 0.9
FATIGUE_WINDOW = 3
FATIGUE_MIN_GAIN = 0.01  # precision gain per round below this = fatigue


def _map_strategist_action_to_lever(action: str) -> str:
    """Map strategist action string to a lever category."""
    mapping = {
        "integrate": "action",
        "optimize": "configuration",
        "remove": "control",
    }
    return mapping.get(action, "action")


def _map_researcher_action_to_lever(action: str) -> str:
    """Map researcher action string to a lever category."""
    mapping = {
        "search_external": "action",
        "discover_local": "instruction",
        "optimize_param": "configuration",
        "remove_factor": "control",
    }
    return mapping.get(action, "instruction")


@dataclass
class LeverStats:
    """Per-lever statistics with Beta posterior."""

    lever: str
    attempts: int = 0
    accepted: int = 0
    reverted: int = 0
    precision_hits: int = 0
    precision_total: int = 0
    side_effects: int = 0
    weighted_hits: float = 0.0
    weighted_misses: float = 0.0
    _history: list[float] = field(default_factory=list)  # precision per attempt

    @property
    def raw_precision(self) -> float:
        total = self.precision_total + self.side_effects
        return self.precision_hits / total if total > 0 else 0.0

    @property
    def posterior_mean(self) -> float:
        """Beta(1+wh, 1+wm) posterior mean."""
        wh = self.weighted_hits
        wm = self.weighted_misses
        n_eff = wh + wm
        if n_eff > 0:
            return (1.0 + wh) / (2.0 + wh + wm)
        return 0.5

    @property
    def effective_n(self) -> float:
        return self.weighted_hits + self.weighted_misses


class LeverScoreboard:
    """Track effectiveness of each lever type across rounds.

    Usage::

        scoreboard = LeverScoreboard()
        scoreboard.update(
            levers=["integrate"],
            attribution={"calmar": "flipped", "sharpe": "still_F"},
            gating_outcome="accepted",
            current_round=5,
            round_num=5,
        )
        scoreboard.get_best_lever()  # "action"
    """

    def __init__(self) -> None:
        self._stats: dict[str, LeverStats] = {
            lev: LeverStats(lever=lev) for lev in VALID_LEVERS
        }
        self._history: dict[str, list[float]] = {
            lev: [] for lev in VALID_LEVERS
        }

    def update(
        self,
        levers: list[str],
        attribution: dict[str, str],
        gating_outcome: str,
        current_round: int,
        round_num: int,
    ) -> None:
        """Update scoreboard with a round's results."""
        weight = DECAY ** max(0, current_round - round_num - 1)

        for lever in levers:
            # Normalize lever name
            if lever in ("integrate",):
                norm = "action"
            elif lever in ("optimize",):
                norm = "configuration"
            elif lever in ("remove",):
                norm = "control"
            elif lever in VALID_LEVERS:
                norm = lever
            else:
                norm = "instruction"

            stats = self._stats[norm]
            stats.attempts += 1

            if gating_outcome == "accepted":
                stats.accepted += 1
            elif gating_outcome == "reverted":
                stats.reverted += 1

            hits = 0
            attributed = 0
            side_effects = 0

            for outcome in attribution.values():
                if outcome == "absent":
                    continue
                attributed += 1
                if outcome == "flipped":
                    hits += 1
                elif outcome == "regressed":
                    side_effects += 1

            stats.precision_hits += hits
            stats.precision_total += attributed
            stats.side_effects += side_effects

            misses = (attributed - hits) + side_effects
            stats.weighted_hits += weight * hits
            stats.weighted_misses += weight * misses

            # Track precision history for fatigue detection
            prec = stats.raw_precision
            self._history[norm].append(prec)

    def get_scoreboard(self) -> list[LeverStats]:
        """Return stats for all lever types."""
        return [self._stats[lev] for lev in sorted(VALID_LEVERS)]

    def get_best_lever(self) -> str | None:
        """Return the lever with highest posterior mean."""
        best = None
        best_posterior = -1.0
        for stats in self._stats.values():
            if stats.effective_n > 0 and stats.posterior_mean > best_posterior:
                best_posterior = stats.posterior_mean
                best = stats.lever
        return best

    def is_lever_fatigued(self, lever: str) -> bool:
        """Check if a lever has shown diminishing returns for 3+ rounds.

        Only triggers when precision is below 0.7 AND not improving.
        Perfect precision (1.0) is never fatigued.
        """
        norm = lever if lever in VALID_LEVERS else "instruction"
        hist = self._history.get(norm, [])
        if len(hist) < FATIGUE_WINDOW:
            return False
        recent = hist[-FATIGUE_WINDOW:]
        # If current precision is already high, not fatigued
        if recent[-1] >= 0.7:
            return False
        # Each consecutive pair should show gain > FATIGUE_MIN_GAIN
        deltas = [b - a for a, b in zip(recent, recent[1:])]
        return all(d < FATIGUE_MIN_GAIN for d in deltas)

    def build_scoreboard_context(self) -> str:
        """Build a Markdown context string for the researcher prompt."""
        lines = ["<lever-scoreboard>", "杠杆效果评分："]
        for stats in self.get_scoreboard():
            if stats.attempts == 0:
                lines.append(f"  {stats.lever}: 未使用")
                continue
            tag = "[stale]" if self.is_lever_fatigued(stats.lever) else ""
            lines.append(
                f"  {stats.lever}{tag}: "
                f"posterior={stats.posterior_mean:.2f} "
                f"(attempts={stats.attempts}, "
                f"accepted={stats.accepted}, reverted={stats.reverted})"
            )
        best = self.get_best_lever()
        if best:
            lines.append(f"  建议优先: {best}")
        lines.append("</lever-scoreboard>")
        return "\n".join(lines)
