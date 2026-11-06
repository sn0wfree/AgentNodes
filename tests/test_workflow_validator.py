from agentnodes.kernel.workflow.validator import AgentValidator, ValidationResult


class TestValidationResult:
    def test_valid_by_default(self):
        result = ValidationResult()
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):
        result = ValidationResult()
        result.add_error("something wrong")
        assert result.valid is False
        assert "something wrong" in result.errors

    def test_add_warning(self):
        result = ValidationResult()
        result.add_warning("heads up")
        assert result.valid is True
        assert "heads up" in result.warnings


class TestValidatorRegistry:
    """批次 4d：业务规则改为注册制，kernel 默认无规则。"""

    def test_kernel_has_no_business_validators_by_default(self):
        """kernel 的校验器注册表**默认空**——与插件注册表同口径。

        这是「删掉 apps/ 也能跑」的一部分：kernel 不知道
        「因子分析输出该有哪些字段」。
        """
        AgentValidator.clear_output_validators()
        assert AgentValidator.registered_agents() == []

    def test_unregistered_agent_only_gets_generic_checks(self):
        """未注册的业务 agent 只走通用检查（空输出 / 基础字段）。"""
        AgentValidator.clear_output_validators()
        v = AgentValidator()
        # 无 action / hypothesis → 两条基础 warning，无 error
        result = v.validate("some_business_agent", {"foo": 1})
        assert result.valid is True
        assert len(result.warnings) == 2

    def test_register_then_rules_apply(self):
        """注册后规则才生效——证明是机制驱动，不是硬编码。"""
        AgentValidator.clear_output_validators()

        def _rule(output, result):
            if output.get("x", 0) > 10:
                result.add_error("x too big")

        AgentValidator.register_output_validator("my_agent", _rule)
        try:
            v = AgentValidator()
            assert v.validate("my_agent", {"x": 20}).valid is False
            assert v.validate("my_agent", {"x": 5}).valid is True
        finally:
            AgentValidator.clear_output_validators()

    def test_register_is_idempotent(self):
        AgentValidator.clear_output_validators()
        AgentValidator.register_output_validator("a", lambda o, r: None)
        AgentValidator.register_output_validator("a", lambda o, r: None)
        assert AgentValidator.registered_agents() == ["a"]
        AgentValidator.clear_output_validators()


class TestQuantValidators:
    """量化校验器本身（已迁 apps/agent_plugins/quant_validators.py）。

    **装配前显式注册**——与生产路径一致（装配根调用 ``register_all()``）。
    """

    def setup_method(self):
        from agentnodes.apps.agent_plugins.quant_validators import register_all

        AgentValidator.clear_output_validators()
        register_all()
        self.validator = AgentValidator()

    def teardown_method(self):
        AgentValidator.clear_output_validators()

    def test_all_nine_registered(self):
        from agentnodes.apps.agent_plugins.quant_validators import (
            QUANT_VALIDATED_AGENTS,
        )

        assert set(AgentValidator.registered_agents()) == set(
            QUANT_VALIDATED_AGENTS
        )
        assert len(QUANT_VALIDATED_AGENTS) == 9


class TestAgentValidator:
    def setup_method(self):
        # 业务校验器需先注册（批次 4d：kernel 默认无业务规则）
        from agentnodes.apps.agent_plugins.quant_validators import register_all

        AgentValidator.clear_output_validators()
        register_all()
        self.validator = AgentValidator()

    def teardown_method(self):
        AgentValidator.clear_output_validators()

    def test_validate_empty_output(self):
        result = self.validator.validate("agent", {})
        assert result.valid is False
        assert "Empty output" in result.errors

    def test_validate_none_output(self):
        result = self.validator.validate("agent", None)
        assert result.valid is False

    def test_validate_researcher_valid(self):
        output = {"action": "tweak_factor", "factor_direction": "positive"}
        result = self.validator.validate("researcher", output)
        assert result.valid is True

    def test_validate_researcher_invalid_action(self):
        output = {"action": "invalid_action"}
        result = self.validator.validate("researcher", output)
        assert result.valid is False
        assert any("Invalid action" in e for e in result.errors)

    def test_validate_researcher_invalid_direction(self):
        output = {"action": "tweak_factor", "factor_direction": "sideways"}
        result = self.validator.validate("researcher", output)
        assert result.valid is False
        assert any("Invalid factor_direction" in e for e in result.errors)

    def test_validate_factor_analyst_valid(self):
        output = {"ic_mean": 0.05, "ir_mean": 1.2}
        result = self.validator.validate("factor_analyst", output)
        assert result.valid is True

    def test_validate_factor_analyst_ic_out_of_range(self):
        output = {"ic_mean": 1.5}
        result = self.validator.validate("factor_analyst", output)
        assert result.valid is False
        assert any("IC mean out of range" in e for e in result.errors)

    def test_validate_factor_analyst_ir_high(self):
        output = {"ir_mean": 15.0}
        result = self.validator.validate("factor_analyst", output)
        assert result.valid is True
        assert any("IR mean unusually high" in w for w in result.warnings)

    def test_validate_risk_controller_valid(self):
        output = {"verdict": "pass", "max_drawdown": -0.15}
        result = self.validator.validate("risk_controller", output)
        assert result.valid is True

    def test_validate_risk_controller_invalid_verdict(self):
        output = {"verdict": "maybe"}
        result = self.validator.validate("risk_controller", output)
        assert result.valid is False
        assert any("Invalid verdict" in e for e in result.errors)

    def test_validate_risk_controller_positive_max_dd(self):
        output = {"max_drawdown": 0.1}
        result = self.validator.validate("risk_controller", output)
        assert result.valid is False
        assert any("should be negative" in e for e in result.errors)

    def test_validate_portfolio_weights_sum(self):
        output = {"weights": {"a": 0.5, "b": 0.5}}
        result = self.validator.validate("portfolio_construction", output)
        assert result.valid is True

    def test_validate_portfolio_weights_mismatch(self):
        output = {"weights": {"a": 0.3, "b": 0.3}}
        result = self.validator.validate("portfolio_construction", output)
        assert any("sum to" in w for w in result.warnings)

    def test_validate_backtest_missing_metrics(self):
        output = {"metrics": {"sharpe": 0.5}}
        result = self.validator.validate("backtest_diagnostics", output)
        assert any("Missing metric" in w for w in result.warnings)

    def test_validate_unknown_agent(self):
        output = {"action": "test"}
        result = self.validator.validate("unknown_agent", output)
        assert result.valid is True

    def test_validate_data_quality(self):
        output = {"completeness": 0.95}
        result = self.validator.validate("data_quality", output)
        assert result.valid is True

    def test_validate_data_quality_out_of_range(self):
        output = {"completeness": 1.5}
        result = self.validator.validate("data_quality", output)
        assert result.valid is False
        assert any("completeness out of range" in e for e in result.errors)

    def test_validate_anti_overfit_valid(self):
        output = {"methods_passed": 4, "methods_total": 6}
        result = self.validator.validate("anti_overfit_analyst", output)
        assert result.valid is True

    def test_validate_anti_overfit_exceeds_total(self):
        output = {"methods_passed": 7, "methods_total": 6}
        result = self.validator.validate("anti_overfit_analyst", output)
        assert result.valid is False
        assert any("cannot exceed" in e for e in result.errors)
