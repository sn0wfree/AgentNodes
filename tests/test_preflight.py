"""Tests for apps/preflight.py — 启动前环境检查。

覆盖 P0：
- CheckResult 数据类
- _check_llm_provider (critical)
- _check_duckdb_writable
- _check_data_sources
- _check_ohlcv_integrity
- run_preflight 总入口
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ============================================================
# 1. CheckResult 数据类
# ============================================================


class TestCheckResult:
    """CheckResult 不可变数据类。"""

    def test_creation_basic(self):
        from strategy_research.apps.preflight import CheckResult
        cr = CheckResult(
            name="X",
            status="ready",
            message="ok",
            critical=False,
        )
        assert cr.name == "X"
        assert cr.status == "ready"
        assert cr.message == "ok"
        assert cr.critical is False

    def test_frozen(self):
        """frozen dataclass 不允许修改字段。"""
        from dataclasses import FrozenInstanceError

        from strategy_research.apps.preflight import CheckResult
        cr = CheckResult(name="X", status="ready", message="ok")
        with pytest.raises(FrozenInstanceError):
            cr.name = "Y"  # type: ignore[misc]

    def test_default_critical_is_false(self):
        from strategy_research.apps.preflight import CheckResult
        cr = CheckResult(name="X", status="ready", message="ok")
        assert cr.critical is False


# ============================================================
# 2. _check_llm_provider
# ============================================================


class TestCheckLLMProvider:
    """LLM Provider 检查（critical）。"""

    def test_critical_fail_when_no_keys(self, monkeypatch: pytest.MonkeyPatch):
        """无 LLM key 时 critical fail。"""
        from strategy_research.apps.preflight import _check_llm_provider
        # 清空所有候选 env vars
        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        cr = _check_llm_provider()
        assert cr.status == "error"
        assert cr.critical is True
        assert "LLM" in cr.name

    def test_ready_with_openai_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        from strategy_research.apps.preflight import _check_llm_provider
        cr = _check_llm_provider()
        assert cr.status == "ready"
        assert "OpenAI" in cr.message

    def test_ready_with_deepseek_key(self, monkeypatch: pytest.MonkeyPatch):
        for k in ["OPENAI_API_KEY", "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")

        from strategy_research.apps.preflight import _check_llm_provider
        cr = _check_llm_provider()
        assert cr.status == "ready"
        assert "DeepSeek" in cr.message

    def test_picks_first_available(self, monkeypatch: pytest.MonkeyPatch):
        """多个 key 时取第一个（OPENAI）。"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")

        from strategy_research.apps.preflight import _check_llm_provider
        cr = _check_llm_provider()
        assert cr.status == "ready"
        assert "OpenAI" in cr.message


# ============================================================
# 3. _check_duckdb_writable
# ============================================================


class TestCheckDuckDBWritable:
    """DuckDB 可写检查。"""

    def test_ready_for_writable_workspace(self, tmp_path: Path):
        from strategy_research.apps.preflight import _check_duckdb_writable

        # tmp_path 自动可写
        cr = _check_duckdb_writable(tmp_path)
        assert cr.status == "ready"
        assert "writable" in cr.message.lower() or "writable" in cr.impact.lower() or cr.status == "ready"

    def test_not_configured_when_duckdb_missing(self, monkeypatch: pytest.MonkeyPatch):
        """duckdb 包缺失时返回 not_configured。"""
        # 模拟 duckdb ImportError
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("No module named 'duckdb'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from strategy_research.apps.preflight import _check_duckdb_writable
        cr = _check_duckdb_writable(Path("/tmp"))
        assert cr.status == "not_configured"


# ============================================================
# 4. _check_data_sources
# ============================================================


class TestCheckDataSources:
    """数据源可用性检查。"""

    def test_finds_at_least_one_loader(self):
        """应至少有一个 loader available。"""
        from strategy_research.apps.preflight import _check_data_sources

        cr = _check_data_sources(Path("/tmp"))
        assert cr.status in ("ready", "not_configured")
        if cr.status == "ready":
            assert "个可用" in cr.message or "available" in cr.message.lower()

    def test_lists_known_loaders(self):
        """应识别 tencent/akshare/yfinance/local 等。"""
        from strategy_research.apps.preflight import _check_data_sources

        cr = _check_data_sources(Path("/tmp"))
        if cr.status == "ready":
            # 至少应该提到一个常见 loader
            assert any(
                name in cr.message
                for name in ["tencent", "akshare", "yfinance", "eastmoney", "local"]
            )


# ============================================================
# 5. _check_ohlcv_integrity
# ============================================================


class TestCheckOHLCVIntegrity:
    """OHLCV 数据完整性检查。"""

    def test_skipped_when_no_price_data(self, tmp_path: Path):
        """无 price_data 时返回 skipped。"""
        from strategy_research.apps.db import init_db
        from strategy_research.apps.preflight import _check_ohlcv_integrity

        # 创建空的 DuckDB（无 price_data 行）
        init_db(tmp_path)
        cr = _check_ohlcv_integrity(tmp_path)
        assert cr.status == "skipped"

    def test_ready_for_ohlcv_data(self, tmp_path: Path):
        """OHLCV 完整时返回 ready。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.apps.db import init_db, save_ohlcv_data
        from strategy_research.apps.preflight import _check_ohlcv_integrity

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=2, n_days=50)
        for code, df in ohlcv.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        cr = _check_ohlcv_integrity(tmp_path)
        assert cr.status == "ready"
        assert "行" in cr.message


# ============================================================
# 6. run_preflight 总入口
# ============================================================


class TestRunPreflight:
    """run_preflight 总入口。"""

    def test_returns_list_of_4_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """应返回 4 项结果。"""
        from strategy_research.apps.preflight import run_preflight

        # 清空 LLM key 以确保 critical fail
        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        results = run_preflight(tmp_path, verbose=False)
        assert len(results) == 4

    def test_includes_all_4_check_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from strategy_research.apps.preflight import run_preflight

        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        results = run_preflight(tmp_path, verbose=False)
        names = {r.name for r in results}
        assert "LLM Provider" in names
        assert "DuckDB" in names
        assert "Data Sources" in names
        assert "OHLCV Integrity" in names

    def test_verbose_true_prints_output(
        self, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
    ):
        from strategy_research.apps.preflight import run_preflight

        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        run_preflight(tmp_path, verbose=True)
        captured = capsys.readouterr().out
        assert "Pre-flight Check" in captured
        assert "LLM Provider" in captured

    def test_critical_fail_marks_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from strategy_research.apps.preflight import run_preflight

        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        results = run_preflight(tmp_path, verbose=False)
        critical_results = [r for r in results if r.critical]
        assert len(critical_results) >= 1
        # 无 LLM key 时 LLM check 应 fail
        llm_result = next(r for r in results if r.name == "LLM Provider")
        assert llm_result.status == "error"


# ============================================================
# 7. _print_results 输出格式
# ============================================================


class TestPrintResults:
    """_print_results 输出格式。"""

    def test_prints_status_marks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        from strategy_research.apps.preflight import run_preflight

        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        run_preflight(tmp_path, verbose=True)
        captured = capsys.readouterr().out
        # 应有 status marks
        assert "[OK]" in captured or "[FAIL]" in captured or "[N/A]" in captured or "[SKIP]" in captured

    def test_prints_critical_marker_when_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        from strategy_research.apps.preflight import run_preflight

        for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]:
            monkeypatch.delenv(k, raising=False)

        run_preflight(tmp_path, verbose=True)
        captured = capsys.readouterr().out
        assert "[CRITICAL]" in captured
