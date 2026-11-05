import pytest

from agentnodes.kernel.workflow import (
    AgentRegistry,
    AgentStatus,
    topological_layers,
    validate_dag,
)
from agentnodes.kernel.workflow.executors import StubExecutor
from agentnodes.kernel.workflow.validator import AgentValidator

# P8 cleanup: WorkflowController / ControllerConfig / PromptBuilder removed
_WorkflowController = None  # type: ignore[assignment,misc]
_ControllerConfig = None  # type: ignore[assignment,misc]
_PromptBuilder = None  # type: ignore[assignment,misc]


class FailingExecutor:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        raise RuntimeError(f"{self._name} failed")


class TestWorkflowE2E:
    def test_validator_integration(self):
        validator = AgentValidator()

        output = {"action": "tweak_factor", "factor_direction": "positive"}
        result = validator.validate("researcher", output)
        assert result.valid is True

        output = {"action": "invalid"}
        result = validator.validate("researcher", output)
        assert result.valid is False

    def test_dag_layers_computation(self):
        # deps convention: {node: [upstream_deps]}
        adj = {
            "researcher": [],
            "data_quality": ["researcher"],
            "factor_analyst": ["researcher"],
            "strategist": ["data_quality", "factor_analyst"],
            "portfolio_construction": ["strategist"],
            "risk_controller": ["portfolio_construction"],
            "attribution_analyst": ["risk_controller"],
            "anti_overfit_analyst": ["attribution_analyst"],
            "backtest_diagnostics": ["anti_overfit_analyst"],
        }
        layers = topological_layers(adj)
        assert layers[0] == ["researcher"]
        assert sorted(layers[1]) == ["data_quality", "factor_analyst"]
        assert layers[2] == ["strategist"]
        assert layers[3] == ["portfolio_construction"]
        assert layers[4] == ["risk_controller"]
        assert layers[5] == ["attribution_analyst"]
        assert layers[6] == ["anti_overfit_analyst"]
        assert layers[7] == ["backtest_diagnostics"]

