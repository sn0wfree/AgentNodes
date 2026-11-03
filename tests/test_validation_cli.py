"""Tests for core.validation.cli (P3-d)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from strategy_research.apps.validation.cli import (
    add_validate_subparsers,
    cmd_validate_run,
)


def _make_args(**kwargs) -> argparse.Namespace:
    base = dict(
        run_dir=None,
        market="a_share",
        monte_carlo=False,
        n_simulations=1000,
        bootstrap=False,
        n_bootstrap=1000,
        walk_forward=False,
        n_windows=5,
        seed=42,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_validate_subparsers(sub)
    return parser


def _setup_run_dir(tmp_path: Path, ann_return: float = 0.15, sharpe: float = 0.85) -> Path:
    """Create a fake run_dir with metrics.json."""
    run_dir = tmp_path / "strategies" / "mom" / "runs" / "run_0001"
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "sharpe": sharpe,
        "calmar": 0.6,
        "max_dd": -0.15,
        "ann_return": ann_return,
        "ann_vol": 0.18,
        "sortino": 1.1,
        "turnover": 0.5,
    }
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_dir


# ─── validate-run CLI ───────────────────────────────────────────────────


class TestValidateRun:
    def test_missing_dir(self, tmp_path, capsys):
        rc = cmd_validate_run(_make_args(run_dir=str(tmp_path / "missing")))
        assert rc == 1
        out = capsys.readouterr()
        assert "not found" in out.err

    def test_basic_monte_carlo(self, tmp_path, capsys):
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(
            run_dir=str(run_dir), monte_carlo=True, n_simulations=50,
        )
        rc = cmd_validate_run(args)
        assert rc == 0
        # validation.json written
        out_path = run_dir / "validation.json"
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert "monte_carlo" in data
        assert data["market"] == "a_share"

    def test_basic_bootstrap(self, tmp_path, capsys):
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(
            run_dir=str(run_dir), bootstrap=True, n_bootstrap=50,
        )
        rc = cmd_validate_run(args)
        assert rc == 0
        data = json.loads((run_dir / "validation.json").read_text())
        assert "bootstrap" in data

    def test_basic_walk_forward(self, tmp_path, capsys):
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(
            run_dir=str(run_dir), walk_forward=True, n_windows=3,
        )
        rc = cmd_validate_run(args)
        assert rc == 0
        data = json.loads((run_dir / "validation.json").read_text())
        assert "walk_forward" in data
        assert data["walk_forward"]["n_windows"] == 3

    def test_all_three(self, tmp_path):
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(
            run_dir=str(run_dir),
            monte_carlo=True, n_simulations=20,
            bootstrap=True, n_bootstrap=20,
            walk_forward=True, n_windows=3,
        )
        rc = cmd_validate_run(args)
        assert rc == 0
        data = json.loads((run_dir / "validation.json").read_text())
        assert all(k in data for k in ("monte_carlo", "bootstrap", "walk_forward"))

    def test_no_validation_specified(self, tmp_path):
        """No MC/bootstrap/WF → only metadata returned."""
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(run_dir=str(run_dir))
        rc = cmd_validate_run(args)
        assert rc == 0
        data = json.loads((run_dir / "validation.json").read_text())
        assert data["market"] == "a_share"
        assert "monte_carlo" not in data

    def test_market_type_applied(self, tmp_path, capsys):
        """Market type is correctly applied to validation results."""
        run_dir = _setup_run_dir(tmp_path)
        args = _make_args(
            run_dir=str(run_dir), market="crypto",
            monte_carlo=True, n_simulations=10,
        )
        rc = cmd_validate_run(args)
        assert rc == 0
        data = json.loads((run_dir / "validation.json").read_text())
        assert data["market"] == "crypto"
        assert data["bars_per_year"] == 365  # CRYPTO has 365 bars/year

    def test_seed_reproducible(self, tmp_path):
        """Same seed + config → same Monte Carlo p_value."""
        run_dir_a = _setup_run_dir(tmp_path / "a")
        run_dir_b = _setup_run_dir(tmp_path / "b")
        args_a = _make_args(
            run_dir=str(run_dir_a), monte_carlo=True,
            n_simulations=30, seed=42,
        )
        args_b = _make_args(
            run_dir=str(run_dir_b), monte_carlo=True,
            n_simulations=30, seed=42,
        )
        cmd_validate_run(args_a)
        cmd_validate_run(args_b)
        da = json.loads((run_dir_a / "validation.json").read_text())
        db = json.loads((run_dir_b / "validation.json").read_text())
        assert da["monte_carlo"]["p_value_sharpe"] == db["monte_carlo"]["p_value_sharpe"]

    def test_corrupt_metrics_falls_back_to_synthetic(self, tmp_path, capsys):
        """When DuckDB NAV is missing AND metrics.json is unparseable,
        validation cannot run and returns rc=1 (this is by design)."""
        run_dir = tmp_path / "strategies" / "mom" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (run_dir / "metrics.json").write_text("{not json", encoding="utf-8")
        args = _make_args(run_dir=str(run_dir), bootstrap=True, n_bootstrap=20)
        rc = cmd_validate_run(args)
        assert rc == 1
        out = capsys.readouterr()
        assert "could not load equity curve" in out.err

    def test_no_metrics_no_run(self, tmp_path, capsys):
        """When run_dir has no metrics.json AND no DuckDB NAV, validation fails."""
        run_dir = tmp_path / "strategies" / "mom" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        args = _make_args(run_dir=str(run_dir), bootstrap=True, n_bootstrap=20)
        rc = cmd_validate_run(args)
        assert rc == 1

    def test_empty_metrics_uses_zero_drift(self, tmp_path, capsys):
        """Empty metrics dict → synthetic curve with 0 drift is still valid."""
        run_dir = tmp_path / "strategies" / "mom" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (run_dir / "metrics.json").write_text("{}", encoding="utf-8")
        args = _make_args(run_dir=str(run_dir), bootstrap=True, n_bootstrap=20)
        rc = cmd_validate_run(args)
        assert rc == 0
        out = capsys.readouterr()
        assert "synthetic" in out.err


# ─── argparse wiring ────────────────────────────────────────────────────


class TestArgparseWiring:
    def test_basic_args(self):
        parser = _make_parser()
        args = parser.parse_args([
            "validate-run", "/tmp/runs/run_0001",
            "--monte-carlo", "--bootstrap",
            "--market", "us_equity",
            "--n-simulations", "200",
        ])
        assert args.command == "validate-run"
        assert args.run_dir == "/tmp/runs/run_0001"
        assert args.monte_carlo is True
        assert args.bootstrap is True
        assert args.market == "us_equity"
        assert args.n_simulations == 200
        assert args.walk_forward is False
