"""Phase A tier 1 quick-win tests:

- `portfolio` subparser wired into main CLI (Phase A-1)
- `_spawn_agent("critic", ...)` returns expected JSON (Phase A-3)
- `FALLBACK_CHAINS` only references registered loaders (Phase A-4)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ============================================================
# Phase A-1: portfolio CLI wiring
# ============================================================

class TestPortfolioCliWiring:
    """`portfolio` subparser 应已被接入 cli.py:main 解析器。"""

    def test_portfolio_subparser_available(self, capsys):
        """`quantnodes-research portfolio --help` 应不报错。"""
        import sys

        from strategy_research.interfaces.cli import main
        sys.argv = ["quantnodes-research", "portfolio", "--help"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0  # --help exits cleanly

    def test_portfolio_subcommands_available(self):
        """portfolio run / list / show / correlate 四个子命令都在。"""
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        # 直接调用 add_portfolio_subparsers
        from strategy_research.apps.portfolio.cli import add_portfolio_subparsers
        add_portfolio_subparsers(subparsers)

        # 解析每个子命令, 提供必需参数
        arg_map = {
            "run": ["portfolio", "run", "--config", "/tmp/cfg.yaml", "--output-dir", "/tmp/out"],
            "list": ["portfolio", "list", "--strategy-dir", "/tmp"],
            "show": ["portfolio", "show", "/tmp/result"],
            "correlate": ["portfolio", "correlate", "--strategy-dir", "/tmp"],
        }
        for cmd, argv in arg_map.items():
            args = parser.parse_args(argv)
            assert args.command == "portfolio"
            assert args.portfolio_action == cmd


# ============================================================
# Phase A-3: critic agent stub
# ============================================================

class TestCriticAgent:
    """`_spawn_agent('critic', ...)` 应返回 reviewer 风格 JSON。"""

    def _call(self, agent_name: str, round_num: int = 0):
        from strategy_research.interfaces.cli import _spawn_agent
        return _spawn_agent(
            agent_name=agent_name,
            workspace_path=Path("/tmp"),
            strategy_name="test_strat",
            current_state={"total_runs": round_num},
            previous_outputs=[],
        )

    def test_critic_returns_approved_field(self):
        raw = self._call("critic", round_num=1)
        data = json.loads(raw)
        assert "approved" in data
        assert isinstance(data["approved"], bool)

    def test_critic_returns_review_dimensions(self):
        raw = self._call("critic", round_num=1)
        data = json.loads(raw)
        assert "review_dimensions" in data
        dims = data["review_dimensions"]
        # 4 个评审维度 (risk / attribution / diagnostics / statistics)
        assert set(dims.keys()) == {"risk", "attribution", "diagnostics", "statistics"}
        for v in dims.values():
            assert v in ("pass", "fail")

    def test_critic_returns_concerns_list(self):
        raw = self._call("critic", round_num=0)
        data = json.loads(raw)
        assert "concerns" in data
        assert isinstance(data["concerns"], list)

    def test_critic_improving_behavior_approves_later(self):
        """AUTORESEARCH_BEHAVIOR=improving + round>=2 应 approve."""
        old = os.environ.get("AUTORESEARCH_BEHAVIOR")
        os.environ["AUTORESEARCH_BEHAVIOR"] = "improving"
        try:
            raw_round0 = self._call("critic", round_num=0)
            raw_round3 = self._call("critic", round_num=3)
            assert json.loads(raw_round0)["approved"] is False
            assert json.loads(raw_round3)["approved"] is True
        finally:
            if old is None:
                os.environ.pop("AUTORESEARCH_BEHAVIOR", None)
            else:
                os.environ["AUTORESEARCH_BEHAVIOR"] = old

    def test_unknown_agent_returns_error(self):
        """保留旧行为: 未知 agent 返回错误 JSON。"""
        raw = self._call("totally_made_up_agent")
        data = json.loads(raw)
        assert "error" in data


# ============================================================
# Phase A-4: FALLBACK_CHAINS 清理
# ============================================================

class TestFallbackChainsCleanup:
    """FALLBACK_CHAINS 不应包含未注册的 loader (mootdx/baostock/yahoo/futu/okx/ccxt/stooq/sina/tiingo/fmp/finnhub/alphavantage/edgar)。"""

    def test_chains_no_unregistered_loaders(self):
        from strategy_research.apps.data_source import FALLBACK_CHAINS

        # 已注册的 loader
        from strategy_research.apps.data_source.registry import _ensure_registered
        _ensure_registered()

        # 通过 import 触发 loader 注册
        from strategy_research.apps.data_source import registry as _reg_module
        registered = set(_reg_module.LOADER_REGISTRY.keys())

        # 仅 local 在 chains 里, 但 local 是 no-network 标志
        unregistered_disallowed = {
            "mootdx", "baostock", "yahoo", "futu", "okx", "ccxt",
            "stooq", "sina", "tiingo", "fmp", "finnhub", "alphavantage",
            "edgar", "sec_edgar",
        }

        for market, chain in FALLBACK_CHAINS.items():
            for src in chain:
                assert src not in unregistered_disallowed, (
                    f"FALLBACK_CHAINS[{market!r}] 引用未注册的 loader {src!r}. "
                    f"Registered: {registered}"
                )

    def test_all_chain_loaders_are_registered_or_local(self):
        from strategy_research.apps.data_source import FALLBACK_CHAINS
        from strategy_research.apps.data_source.registry import (
            LOADER_REGISTRY,
            _ensure_registered,
        )
        _ensure_registered()

        # 触发所有 loader 模块的导入 (通过 data_source.__init__ 的 _discover_loaders)
        from strategy_research.apps import data_source as _ds  # noqa: F401

        registered = set(LOADER_REGISTRY.keys())
        for market, chain in FALLBACK_CHAINS.items():
            for src in chain:
                # local 是 fallback 终点, 但 LOADER_REGISTRY 可能不含 (因为 is_available=False)
                if src == "local":
                    continue
                assert src in registered, (
                    f"FALLBACK_CHAINS[{market!r}] 引用未在 LOADER_REGISTRY 中注册的 loader {src!r}"
                )
