"""ValidatorRegistry — central registry for AgentValidator instances.

Wraps the existing ``AgentValidator`` from ``core/workflow/validator.py``
(which has 9 built-in agent-specific validators) with a name-indexed
registry so workflow YAML can reference validators by agent name.

Usage:
    from agentnodes.apps.goal.validator_registry import (
        ValidatorRegistry, register_default_validators,
    )
    register_default_validators()  # one-time bootstrap
    validator = ValidatorRegistry.get("researcher")
    result = validator.validate("researcher", output)
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AgentValidatorLike(Protocol):
    """Subset of AgentValidator used by the registry."""

    def validate(self, agent_name: str, output: dict) -> Any: ...


class ValidationResultLike(Protocol):
    """Subset of ValidationResult used by the registry."""
    @property
    def valid(self) -> bool: ...
    @property
    def errors(self) -> list[str]: ...


class ValidatorRegistry:
    """Registry mapping agent_name → AgentValidator instance."""

    _validators: dict[str, AgentValidatorLike] = {}

    @classmethod
    def register(cls, agent_name: str, validator: AgentValidatorLike) -> None:
        """Register a validator for a specific agent name."""
        cls._validators[agent_name] = validator

    @classmethod
    def get(cls, agent_name: str) -> AgentValidatorLike | None:
        """Look up the validator for an agent. Returns None if absent."""
        return cls._validators.get(agent_name)

    @classmethod
    def list_registered(cls) -> list[str]:
        """List all agent names with registered validators."""
        return list(cls._validators.keys())

    @classmethod
    def clear(cls) -> None:
        """Drop all registered validators (mostly for tests)."""
        cls._validators.clear()

    @classmethod
    def is_valid_result(cls, result: Any) -> bool:
        """Return True if a ValidationResult-like object is valid."""
        if result is None:
            return True
        return bool(getattr(result, "valid", True))


def register_default_validators() -> None:
    """Bootstrap: register one AgentValidator covering all 9 agents.

    The vibe-trading ``AgentValidator`` already has 9 agent-specific
    validators.  We register a single instance and let it dispatch
    internally by ``agent_name``.
    """
    try:
        from agentnodes.kernel.workflow.validator import AgentValidator
    except ImportError:
        logger.warning("Cannot import AgentValidator; skipping bootstrap")
        return

    default_validator = AgentValidator()
    for agent_name in [
        "researcher",
        "factor_analyst",
        "strategist",
        "risk_controller",
        "anti_overfit_analyst",
        "data_quality",
        "portfolio_construction",
        "attribution_analyst",
        "backtest_diagnostics",
    ]:
        ValidatorRegistry.register(agent_name, default_validator)
    logger.info(
        "Registered default AgentValidator for %d agents",
        ValidatorRegistry.list_registered().__len__(),
    )


__all__ = [
    "ValidatorRegistry",
    "register_default_validators",
]
