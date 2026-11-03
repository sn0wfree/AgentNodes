"""Auto-create research hypotheses (P3-b integration helper).

When an AgentLoop starts a new session and has a strategy_name, it can
auto-create an `exploring` hypothesis if one does not yet exist for the
strategy in the given market universe. This wires the registry into the
agent loop without forcing every session to manually create hypotheses.
"""

from __future__ import annotations

from .registry import Hypothesis, HypothesisRegistry


class HypothesisAutoCreator:
    """Idempotently create an exploring hypothesis per (strategy, market) pair.

    Design decision (P3 user-confirmed):
        Trigger ONLY on first call from AgentLoop.run() with a non-empty
        session_id AND strategy_name. Skip silently when a hypothesis
        already exists for the same strategy + market.
    """

    def __init__(self, registry: HypothesisRegistry | None = None) -> None:
        self._registry = registry or HypothesisRegistry()

    @property
    def registry(self) -> HypothesisRegistry:
        return self._registry

    def maybe_auto_create(
        self,
        *,
        session_id: str,
        strategy_name: str,
        initial_thesis: str = "",
        market: str = "a_share",
        data_sources: list[str] | None = None,
        skills: list[str] | None = None,
    ) -> Hypothesis | None:
        """Create an exploring hypothesis if no matching one exists.

        Args:
            session_id: Agent session id (unused for matching; reserved for
                future per-session scoping).
            strategy_name: Used as signal_definition prefix for matching.
            initial_thesis: First-pass thesis text (truncated to 200 chars).
            market: Universe key (a_share / hk_equity / us_equity / ...).
            data_sources: Optional list of source providers.
            skills: Optional list of related skills.

        Returns:
            The newly created hypothesis, or None when a matching one
            already exists.
        """
        if not strategy_name or not strategy_name.strip():
            return None

        existing = [
            h for h in self._registry.list()
            if h.signal_definition.strip() == strategy_name.strip()
            and h.universe.strip() == market.strip()
        ]
        if existing:
            return None

        thesis = initial_thesis.strip()[:200] or (
            f"Initial thesis for {strategy_name} in {market} universe."
        )
        return self._registry.create(
            title=f"{strategy_name} initial thesis",
            thesis=thesis,
            status="exploring",
            universe=market,
            signal_definition=strategy_name,
            data_sources=data_sources or ["tushare", "akshare"],
            skills=skills or ["momentum", "factor_research"],
        )
