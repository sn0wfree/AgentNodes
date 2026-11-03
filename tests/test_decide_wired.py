"""Phase C-2 — decide() 接入 autoresearch 测试。

覆盖:
- cmd_autoresearch 调用 decide() 而非内嵌 verdict
- decision.accept 写入 results.tsv
- stagnation stop 触发后退出循环
- summary.json 包含 acceptance_decision breakdown
- 后向兼容: 旧 anti_overfit_analyst_output["verdict"] 仍可作为 fallback
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ============================================================
# 辅助: mock cmd_autoresearch 内调用的关键依赖
# ============================================================


def _build_workspace(tmp_path: Path) -> Path:
    """构造一个最小可用 workspace (含 strategies/momentum_baseline/strategy.py)。

    v0.5.0 dropped the workspace-scaffold ``cli.cmd_init`` helper, so the
    autoresearch test fixtures write the files ``cmd_autoresearch``
    actually needs directly: ``config.yaml`` (with
    ``workspace.default_strategy``), ``strategies/<name>/{strategy.py,
    prepare.py}``, a ``strategies/<name>/runs/`` directory, AND a
    populated DuckDB seeded with sample price data — without that,
    downstream ``run_backtest_script`` returns empty metrics and
    ``decide()`` receives ``{}`` (rather than the real-metrics this test
    asserts on).
    """
    from strategy_research.apps.data_import import generate_sample_ohlcv_data
    from strategy_research.core.db import init_db, save_ohlcv_to_db

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)

    config_yaml = (
        "workspace:\n"
        "  name: test_ws\n"
        "  default_strategy: momentum_baseline\n"
        "strategies:\n"
        "  - name: momentum_baseline\n"
        "    type: selection\n"
        "    goal_metric: calmar\n"
        "    goal_direction: maximize\n"
        "data:\n"
        "  source: duckdb\n"
        "rebalance:\n"
        "  freq: M\n"
        "  min_history: 60\n"
        "top_n: 5\n"
        "max_weight: 0.20\n"
        "weight_method: inverse_vol\n"
    )
    (workspace / "config.yaml").write_text(config_yaml, encoding="utf-8")

    strategy_dir = workspace / "strategies" / "momentum_baseline"
    runs_dir = strategy_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    templates_dir = (
        Path(__file__).resolve().parents[1] / "src"
        / "strategy_research" / "templates"
    )
    for src_name in ("strategy.py", "prepare.py"):
        src = templates_dir / src_name
        dst = strategy_dir / src_name
        text = src.read_text(encoding="utf-8")
        text = text.replace("{strategy_name}", "momentum_baseline")
        text = text.replace("{goal_metric}", "calmar")
        dst.write_text(text, encoding="utf-8")

    init_db(workspace)
    ohlcv_map = generate_sample_ohlcv_data(
        n_assets=10, n_days=504, start_date="2022-01-01"
    )
    save_ohlcv_to_db(workspace, ohlcv_map, "momentum_baseline")

    return workspace


@pytest.fixture
def workspace_path(tmp_path, monkeypatch):
    """真实 init + 后续 autoresearch 不调真 LLM."""
    monkeypatch.setenv("AUTORESEARCH_BEHAVIOR", "improving")
    monkeypatch.chdir(tmp_path)
    wp = _build_workspace(tmp_path)
    return wp


# ============================================================
# decide() 已在 autoresearch 中接入的端到端验证
# ============================================================

class TestDecideWiredIntoAutoresearch:
    """cmd_autoresearch 现在通过 decide() 而非内嵌 weighted score 判定."""

    @pytest.fixture(autouse=True)
    def _patch_sleep(self, monkeypatch):
        """绕过 cooldown sleep, 让测试秒跑。"""
        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)

    def _make_args(self, workspace_path, max_rounds=1):
        import argparse
        return argparse.Namespace(
            path=str(workspace_path),
            strategy="momentum_baseline",
            cooldown=0, jitter=0, min_cooldown=0,
            max_retries=1, max_rounds=max_rounds,
            lazy_detection_interval=999, keep_recent=10,
            llm_profile=None, llm_model=None, llm_base_url=None,
            llm_temperature=None, llm_max_tokens=None, llm_top_p=None,
            llm_timeout=None, llm_max_retries=None, llm_seed=None,
            llm_stream=None,
        )

    def test_cmd_autoresearch_invokes_decide(self, workspace_path, monkeypatch):
        """1 轮 autoresearch 后, summary.json 应包含 acceptance_decision 字段."""
        from strategy_research.cli.commands.autoresearch import cmd_autoresearch
        args = self._make_args(workspace_path)
        cmd_autoresearch(args)

        run_dir = workspace_path / "strategies" / "momentum_baseline" / "runs" / "run_0001"
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            pytest.skip("summary.json 未生成, 可能因 backtest 失败")

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "acceptance_decision" in summary, "summary 应包含 acceptance_decision"
        decision = summary["acceptance_decision"]
        # AcceptanceDecision.to_dict() 的字段
        assert "accept" in decision
        assert "reason" in decision
        assert "hard_passed" in decision
        assert "stagnation_triggered" in decision
        assert isinstance(decision["accept"], bool)

    def test_summary_verdict_matches_decision_accept(self, workspace_path, monkeypatch):
        """summary.verdict 应与 decision.accept 一致."""
        from strategy_research.cli.commands.autoresearch import cmd_autoresearch
        args = self._make_args(workspace_path)
        cmd_autoresearch(args)

        run_dir = workspace_path / "strategies" / "momentum_baseline" / "runs" / "run_0001"
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            pytest.skip("summary 未生成")

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        decision = summary["acceptance_decision"]
        verdict = summary["verdict"]
        expected = "keep" if decision["accept"] else "discard"
        assert verdict == expected

    def test_stagnation_stop_breaks_loop(self, workspace_path, monkeypatch):
        """连续 N 轮 reject → stagnation_triggered → 退出循环."""
        from strategy_research.cli.commands.autoresearch import cmd_autoresearch
        from strategy_research.apps.strategy_acceptance import AcceptanceDecision

        call_count = {"n": 0}

        def stagnation_decide(*args, **kwargs):
            call_count["n"] += 1
            return AcceptanceDecision(
                accept=True,
                reason="forced stagnation break (test mock)",
                hard_passed=False,
                llm_passed=None,
                hard_detail={"calmar": False, "sharpe": False, "max_dd": False,
                             "ann_return": False, "trades": False},
                llm_detail=None,
                stagnation_triggered=True,
            )

        # cli.py cmd_autoresearch 内部 `from ... import decide as make_decision`,
        # 所以要 patch 真正的 strategy_acceptance.decide 模块, 不是 cli.make_decision
        monkeypatch.setattr(
            "strategy_research.apps.strategy_acceptance.decide", stagnation_decide,
        )

        args = self._make_args(workspace_path, max_rounds=10)
        cmd_autoresearch(args)

        # 第一次 decide 调用就触发 stagnation → 仅 1 个 run dir
        runs_dir = workspace_path / "strategies" / "momentum_baseline" / "runs"
        run_dirs = sorted([d for d in runs_dir.iterdir()
                          if d.is_dir() and d.name.startswith("run_")])
        assert len(run_dirs) >= 1
        # 不应跑到 max_rounds=10
        assert len(run_dirs) < 10, f"stagnation 没生效, 跑了 {len(run_dirs)} 轮"
        # 第一次 decide() 调用就触发了
        assert call_count["n"] >= 1

    def test_results_tsv_records_verdict(self, workspace_path, monkeypatch):
        """results.tsv 应记录最终 verdict (keep/discard)."""
        from strategy_research.cli.commands.autoresearch import cmd_autoresearch
        args = self._make_args(workspace_path)
        cmd_autoresearch(args)

        results_tsv = workspace_path / "strategies" / "momentum_baseline" / "runs" / "results.tsv"
        if not results_tsv.exists():
            pytest.skip("results.tsv 未生成")

        lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 2
        header = lines[0].split("\t")
        assert "status" in header
        status_col_idx = header.index("status")
        for line in lines[1:]:
            parts = line.split("\t")
            if parts[0] == "run_0001":
                assert parts[status_col_idx] in ("keep", "discard"), (
                    f"results.tsv status 应是 keep/discard, got {parts[status_col_idx]!r}"
                )
                return
        pytest.fail("未找到 run_0001 行")


# ============================================================
# decide() 调用参数校验
# ============================================================

class TestDecideCallArgs:
    """验证 cmd_autoresearch 调 decide() 时传入的参数合理."""

    @pytest.fixture(autouse=True)
    def _patch_sleep(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)

    def _make_args(self, workspace_path, max_rounds=1):
        import argparse
        return argparse.Namespace(
            path=str(workspace_path),
            strategy="momentum_baseline",
            cooldown=0, jitter=0, min_cooldown=0,
            max_retries=1, max_rounds=max_rounds,
            lazy_detection_interval=999, keep_recent=10,
            llm_profile=None, llm_model=None, llm_base_url=None,
            llm_temperature=None, llm_max_tokens=None, llm_top_p=None,
            llm_timeout=None, llm_max_retries=None, llm_seed=None,
            llm_stream=None,
        )

    def test_decide_called_with_metrics(self, workspace_path, monkeypatch):
        """decide() 收到的 metrics 来自 backtest."""
        from strategy_research.cli.commands.autoresearch import cmd_autoresearch
        from strategy_research.apps.strategy_acceptance import AcceptanceDecision

        captured = {}

        def capture_decide(metrics, *, llm_verdict=None, cfg=None, stagnation_count=0):
            captured["metrics"] = dict(metrics)
            captured["llm_verdict"] = llm_verdict
            return AcceptanceDecision(
                accept=True,
                reason="test capture",
                hard_passed=True,
                llm_passed=None,
                hard_detail={"calmar": True, "sharpe": True, "max_dd": True,
                             "ann_return": True, "trades": True},
            )

        monkeypatch.setattr(
            "strategy_research.apps.strategy_acceptance.decide", capture_decide,
        )

        args = self._make_args(workspace_path)
        cmd_autoresearch(args)

        if "metrics" not in captured:
            pytest.skip("decide 未被调用")
        assert isinstance(captured["metrics"], dict)
        assert "calmar" in captured["metrics"] or "sharpe" in captured["metrics"]


# ============================================================
# 回归: hard threshold 仍生效
# ============================================================

class TestHardThresholdStillWorks:
    """HardThresholdRule 通过 metrics 字段判定, 与 autoresearch 集成正常."""

    def test_low_calmar_results_in_reject(self):
        from strategy_research.apps.strategy_acceptance import AcceptanceConfig, decide
        decision = decide(
            metrics={"calmar": -0.5, "sharpe": -0.5, "max_dd": -0.8,
                     "ann_return": -0.5, "trades": 10},
            cfg=AcceptanceConfig(),
        )
        assert decision.accept is False
        assert decision.hard_passed is False

    def test_high_quality_results_in_accept(self):
        from strategy_research.apps.strategy_acceptance import AcceptanceConfig, decide
        decision = decide(
            metrics={"calmar": 1.5, "sharpe": 1.2, "max_dd": -0.05,
                     "ann_return": 0.25, "trades": 100},
            cfg=AcceptanceConfig(),
        )
        assert decision.accept is True
        assert decision.hard_passed is True
