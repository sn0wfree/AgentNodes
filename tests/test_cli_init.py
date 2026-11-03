"""Tests for cmd_evaluate.

v0.5.0 dropped the workspace-scaffold ``cli.cmd_init`` helper plus the
``_render_template`` template-substitution helper. The previously
co-located ``TestRenderTemplate`` and ``TestCmdInit`` classes were
removed; this file now exercises only ``cmd_evaluate`` (which still
exists and still requires a populated workspace fixture).

Workspace fixture for these tests is constructed manually with the
same files the old ``cmd_init`` produced:
* ``config.yaml``
* ``strategies/<name>/strategy.py`` + ``prepare.py``
* ``strategies/<name>/runs/``
* DuckDB seeded via ``init_db`` + ``save_ohlcv_to_db``
  (formerly ``import_dataframe``, removed 2026-08-05 along with
  ``save_price_data`` to eliminate close-only → fake-OHLCV pollution).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

# ============================================================
# 1. cmd_evaluate 端到端
# ============================================================


def _build_workspace(workspace_path: Path, strategy_name: str = "eval_strat") -> Path:
    """手工构造最小可用 workspace (替代 v0.5.0 删除的 cmd_init).

    Mirror of ``tests/test_autoresearch.py::_build_workspace`` so the
    cmd_evaluate integration test suite has a deterministic fixture.
    """
    from strategy_research.apps.data_import import generate_sample_ohlcv_data
    from strategy_research.core.db import init_db, save_ohlcv_to_db

    workspace_path.mkdir(exist_ok=True)

    config_yaml = (
        "workspace:\n"
        f"  name: {workspace_path.name}\n"
        f"  default_strategy: {strategy_name}\n"
        "strategies:\n"
        f"  - name: {strategy_name}\n"
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
    (workspace_path / "config.yaml").write_text(config_yaml, encoding="utf-8")

    strategy_dir = workspace_path / "strategies" / strategy_name
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
        text = text.replace("{strategy_name}", strategy_name)
        text = text.replace("{goal_metric}", "calmar")
        dst.write_text(text, encoding="utf-8")

    init_db(workspace_path)
    ohlcv_map = generate_sample_ohlcv_data(
        n_assets=10, n_days=504, start_date="2022-01-01"
    )
    save_ohlcv_to_db(workspace_path, ohlcv_map, strategy_name)

    return workspace_path


class TestCmdEvaluate:
    """cmd_evaluate 端到端。"""

    def test_evaluate_runs_and_writes_metrics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """cmd_evaluate 应跑一次回测并写 run_XXXX。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "ws"
        strategy_name = "eval_strat"
        _build_workspace(workspace, strategy_name)

        args = argparse.Namespace(
            path=str(workspace),
            strategy=strategy_name,
            description="unit test",
            timeout=60,
        )
        rc = cmd_evaluate(args)
        assert rc == 0

        runs_dir = workspace / "strategies" / strategy_name / "runs"
        run_dirs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_"))
        assert "run_0001" in run_dirs

    def test_evaluate_returns_metrics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        """cmd_evaluate 输出应包含 8 项 metrics。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "ws"
        strategy_name = "eval_strat"
        _build_workspace(workspace, strategy_name)

        args = argparse.Namespace(
            path=str(workspace),
            strategy=strategy_name,
            description="",
            timeout=60,
        )
        cmd_evaluate(args)

        captured = capsys.readouterr().out
        for metric in ["Calmar", "Sharpe", "MaxDD", "AnnRet", "AnnVol", "Sortino", "Turnover"]:
            assert metric in captured, f"{metric} not in output"

    def test_evaluate_fails_if_workspace_invalid(self, tmp_path: Path):
        """workspace 无效应返回 1。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "nonexistent"
        args = argparse.Namespace(
            path=str(workspace),
            strategy=None,
            description="",
            timeout=60,
        )
        rc = cmd_evaluate(args)
        assert rc == 1
