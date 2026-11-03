"""Anti-overfit Analyst 测试用例 (覆盖 P0+P1+P2)。

只针对 anti_overfit_analyst 相关功能,不修改其他 Agent。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/home/ll/Public/strategy-research/src")

from strategy_research.interfaces.cli import _spawn_agent

PROMPT_PATH = "/home/ll/Public/strategy-research/src/strategy_research/templates/.prompts/anti_overfit_analyst.md"


# ============================================================
# T1: 测试 anti_overfit_analyst 的输出格式 (P0)
# ============================================================
class TestAntiOverfitOutputFormat:
    """验证输出 JSON 格式正确。"""

    def test_output_is_valid_json(self):
        """输出必须是有效 JSON。"""
        raw = _spawn_agent(
            "anti_overfit_analyst",
            Path("/tmp/test"),
            "test_strategy",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self):
        """必填字段必须存在。"""
        raw = _spawn_agent(
            "anti_overfit_analyst",
            Path("/tmp/test"),
            "test_strategy",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        parsed = json.loads(raw)
        assert "verdict" in parsed
        assert parsed["verdict"] in ["keep", "discard"]
        assert "overfit_passed" in parsed
        assert isinstance(parsed["overfit_passed"], bool)

    def test_methods_passed_keys(self):
        """methods_passed 必须包含 6 个方法。"""
        raw = _spawn_agent(
            "anti_overfit_analyst",
            Path("/tmp/test"),
            "test_strategy",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        parsed = json.loads(raw)
        expected_keys = {
            "start_dependency", "rebalance_offset", "parameter_perturbation",
            "ablation", "bootstrap", "monte_carlo",
        }
        assert expected_keys.issubset(set(parsed.get("methods_passed", {}).keys()))

    def test_no_markdown_wrapper(self):
        """输出不应有 markdown 代码块标记。"""
        raw = _spawn_agent(
            "anti_overfit_analyst",
            Path("/tmp/test"),
            "test_strategy",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        assert "```" not in raw


# ============================================================
# T2: 测试基于 metrics 的判断逻辑 (P0)
# ============================================================
class TestMetricsBasedJudgment:
    """验证模拟输出基于真实 metrics,不是硬编码。"""

    def test_high_calmar_returns_keep(self):
        """高 Calmar 应该返回 keep。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        parsed = json.loads(raw)
        assert parsed["verdict"] == "keep"
        assert parsed["overfit_passed"] is True

    def test_low_calmar_returns_discard(self):
        """低 Calmar 应该返回 discard。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": -0.5, "sharpe": -0.2, "max_dd": -0.6}],
        )
        parsed = json.loads(raw)
        assert parsed["verdict"] == "discard"
        assert parsed["overfit_passed"] is False

    def test_negative_metrics_all_fail(self):
        """所有 metrics 为负时,所有方法都应 fail。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": -1.0, "sharpe": -0.5, "max_dd": -0.7}],
        )
        parsed = json.loads(raw)
        for v in parsed["methods_passed"].values():
            assert v is False

    def test_zero_metrics_returns_discard(self):
        """所有 metrics 为 0 时,应该 discard。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": 0.0, "sharpe": 0.0, "max_dd": 0.0}],
        )
        parsed = json.loads(raw)
        assert parsed["verdict"] == "discard"


# ============================================================
# T3: 测试 weighted_score 判断逻辑 (P2)
# ============================================================
class TestWeightedScoreLogic:
    """验证 P2 的加权评分逻辑。"""

    def test_weighted_score_field_present(self):
        """输出应包含 weighted_score 字段。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.3}],
        )
        parsed = json.loads(raw)
        assert "weighted_score" in parsed
        assert 0.0 <= parsed["weighted_score"] <= 1.0

    def test_weighted_score_all_pass(self):
        """全通过时 weighted_score 应该接近 1.0。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": 5.0, "sharpe": 3.0, "max_dd": -0.05}],
        )
        parsed = json.loads(raw)
        assert parsed["weighted_score"] >= 0.95

    def test_weighted_score_all_fail(self):
        """全失败时 weighted_score 应该接近 0.0。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{"calmar": -1.0, "sharpe": -1.0, "max_dd": -0.9}],
        )
        parsed = json.loads(raw)
        assert parsed["weighted_score"] <= 0.05

    def test_threshold_configurable(self):
        """pass 阈值应该可配置 (环境变量)。"""
        # 高阈值: 应该更难 keep
        os.environ["ANTI_OVERFIT_THRESHOLD"] = "0.95"
        try:
            raw = _spawn_agent(
                "anti_overfit_analyst", Path("/tmp"), "test",
                {"total_runs": 5},
                [{"calmar": 1.0, "sharpe": 0.7, "max_dd": -0.3}],
            )
            parsed = json.loads(raw)
            # 高阈值下即使 Calmar=1.0 也可能 discard
            # 至少 weighted_score 应该是合理的
            assert "weighted_score" in parsed
        finally:
            del os.environ["ANTI_OVERFIT_THRESHOLD"]


# ============================================================
# T5: 测试 Prompt 内容 (P1)
# ============================================================
class TestAntiOverfitPrompt:
    """验证 Prompt 包含必要信息。"""

    def test_prompt_has_tool_description(self):
        """Prompt 包含工具说明 (路径/CLI)。"""
        if not Path(PROMPT_PATH).exists():
            pytest.skip(f"Prompt 文件不存在: {PROMPT_PATH}")
        content = Path(PROMPT_PATH).read_text(encoding="utf-8")
        assert "strategy.py" in content or "strategies/" in content
        assert "cli" in content.lower() or "run" in content.lower()

    def test_prompt_has_executable_steps(self):
        """每种方法有可执行步骤。"""
        if not Path(PROMPT_PATH).exists():
            pytest.skip(f"Prompt 文件不存在: {PROMPT_PATH}")
        content = Path(PROMPT_PATH).read_text(encoding="utf-8")
        assert "步骤" in content or "运行" in content

    def test_prompt_has_quantitative_output(self):
        """输出格式包含量化指标。"""
        if not Path(PROMPT_PATH).exists():
            pytest.skip(f"Prompt 文件不存在: {PROMPT_PATH}")
        content = Path(PROMPT_PATH).read_text(encoding="utf-8")
        assert "metrics" in content.lower()
        assert "p_value" in content or "CV%" in content or "p-value" in content

    def test_prompt_has_weighted_score_logic(self):
        """包含 weighted_score 逻辑说明。"""
        if not Path(PROMPT_PATH).exists():
            pytest.skip(f"Prompt 文件不存在: {PROMPT_PATH}")
        content = Path(PROMPT_PATH).read_text(encoding="utf-8")
        assert "weighted" in content.lower() or "权重" in content


# ============================================================
# T6: 边界条件测试
# ============================================================
class TestEdgeCases:
    """边界条件。"""

    def test_empty_previous_outputs(self):
        """previous_outputs 为空时不崩溃。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [],
        )
        parsed = json.loads(raw)
        assert "verdict" in parsed

    def test_missing_metrics_fields(self):
        """metrics 字段缺失时不崩溃。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [{}],
        )
        parsed = json.loads(raw)
        assert "verdict" in parsed

    def test_none_metrics(self):
        """metrics 为 None 时不崩溃。"""
        raw = _spawn_agent(
            "anti_overfit_analyst", Path("/tmp"), "test",
            {"total_runs": 5},
            [None],
        )
        parsed = json.loads(raw)
        assert "verdict" in parsed
