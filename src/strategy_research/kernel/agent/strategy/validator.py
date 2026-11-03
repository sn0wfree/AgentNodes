"""ValidatorStrategy (P1-3) — claim-heavy validation mode.

Built for tasks that should converge quickly to a verdict: small
``max_iterations`` (5), tight ``no_progress_window`` (2), and an explicit
``claim_validation_ran`` flag in ``ctx.metadata`` so downstream consumers
know the strategy asked the model to verify its claims.

Composition:
- ReAct base, but ``LoopConfig`` is tightened.
- ``finalization`` is replaced with ``ClaimValidationFinalizationStep``
  so the post-loop hook is guaranteed to fire.
"""

from __future__ import annotations

from typing import Optional

from .custom_steps import ClaimValidationFinalizationStep
from .factory import CustomStrategy, ReActStrategyFactory
from .loop_strategy import LoopConfig, LoopStrategy

__all__ = ["ValidatorStrategy", "ValidatorStrategyFactory"]


class ValidatorStrategy(CustomStrategy):
    """Low-iteration / claim-validation / strict-progress validator."""

    def __init__(self, config: Optional[LoopConfig] = None):
        base = ReActStrategyFactory.create(
            config or LoopConfig(
                max_iterations=5,
                no_progress_window=2,
            ),
        )
        super().__init__(
            name="validator",
            description="Low-iteration (5) validator; strict progress window (2); claim_validation on exit.",
            base_strategy=base,
            config=base.config,
            finalization=ClaimValidationFinalizationStep(),
        )


class ValidatorStrategyFactory:
    @staticmethod
    def create(config: Optional[LoopConfig] = None) -> LoopStrategy:
        return ValidatorStrategy(config=config)
