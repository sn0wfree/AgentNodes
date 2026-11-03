from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class AgentValidator:
    def validate(self, agent_name: str, output: dict) -> ValidationResult:
        result = ValidationResult()

        if not output:
            result.add_error("Empty output")
            return result

        self._validate_base_fields(output, result)

        validator = self._get_validator(agent_name)
        if validator:
            validator(output, result)

        return result

    def _validate_base_fields(self, output: dict, result: ValidationResult) -> None:
        if "action" not in output:
            result.add_warning("Missing 'action' field")

        if "hypothesis" not in output:
            result.add_warning("Missing 'hypothesis' field")

    def _get_validator(self, agent_name: str):
        validators = {
            "researcher": self._validate_researcher,
            "factor_analyst": self._validate_factor_analyst,
            "strategist": self._validate_strategist,
            "risk_controller": self._validate_risk_controller,
            "anti_overfit_analyst": self._validate_anti_overfit,
            "data_quality": self._validate_data_quality,
            "portfolio_construction": self._validate_portfolio,
            "attribution_analyst": self._validate_attribution,
            "backtest_diagnostics": self._validate_backtest,
        }
        return validators.get(agent_name)

    def _validate_researcher(self, output: dict, result: ValidationResult) -> None:
        action = output.get("action", "")
        valid_actions = {
            "tweak_factor",
            "add_factor",
            "remove_factor",
            "adjust_weights",
            "keep_current",
        }
        if action and action not in valid_actions:
            result.add_error(f"Invalid action: {action}")

        direction = output.get("factor_direction", "")
        if direction and direction not in {"positive", "negative"}:
            result.add_error(f"Invalid factor_direction: {direction}")

    def _validate_factor_analyst(self, output: dict, result: ValidationResult) -> None:
        ic = output.get("ic_mean")
        if ic is not None:
            if not -1 <= ic <= 1:
                result.add_error(f"IC mean out of range: {ic}")

        ir = output.get("ir_mean")
        if ir is not None:
            if abs(ir) > 10:
                result.add_warning(f"IR mean unusually high: {ir}")

    def _validate_strategist(self, output: dict, result: ValidationResult) -> None:
        changes = output.get("changes", {})
        if not changes:
            result.add_warning("No strategy changes proposed")

    def _validate_risk_controller(self, output: dict, result: ValidationResult) -> None:
        verdict = output.get("verdict", "")
        if verdict and verdict not in {"pass", "fail", "warn"}:
            result.add_error(f"Invalid verdict: {verdict}")

        max_dd = output.get("max_drawdown")
        if max_dd is not None:
            if max_dd > 0:
                result.add_error("max_drawdown should be negative")
            if max_dd < -0.5:
                result.add_warning(f"max_drawdown very high: {max_dd}")

    def _validate_anti_overfit(self, output: dict, result: ValidationResult) -> None:
        methods_passed = output.get("methods_passed", 0)
        methods_total = output.get("methods_total", 0)
        if methods_total > 0 and methods_passed > methods_total:
            result.add_error("methods_passed cannot exceed methods_total")

    def _validate_data_quality(self, output: dict, result: ValidationResult) -> None:
        completeness = output.get("completeness")
        if completeness is not None:
            if not 0 <= completeness <= 1:
                result.add_error(f"completeness out of range: {completeness}")

    def _validate_portfolio(self, output: dict, result: ValidationResult) -> None:
        weights = output.get("weights", {})
        if weights:
            total = sum(weights.values())
            if abs(total - 1.0) > 0.01:
                result.add_warning(f"Weights sum to {total}, expected ~1.0")

    def _validate_attribution(self, output: dict, result: ValidationResult) -> None:
        sources = output.get("sources", {})
        if not sources:
            result.add_warning("No attribution sources provided")

    def _validate_backtest(self, output: dict, result: ValidationResult) -> None:
        metrics = output.get("metrics", {})
        required = ["sharpe", "calmar", "max_dd"]
        for key in required:
            if key not in metrics:
                result.add_warning(f"Missing metric: {key}")
