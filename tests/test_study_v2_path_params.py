"""Study v2 M2 tests — engine path parameterization.

Covers:
- backtest.update_results_tsv round column (trailing) + results_tsv override
- run_backtest_script with custom strategy_dir / results_tsv / round_num
- run_backtest_from_yaml with custom strategy_dir / runs_dir / results_tsv
- read_current_state strategy_file / results_tsv dual sources
- _create_run_dir runs_dir override + per-dir max+1 numbering
- runner._update_results_tsv (round, run) composite matching
- ToolContext fields reach tools (write_file read_roots / write_roots)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.tools import ToolContext
from strategy_research.apps.autoresearch import (
    _create_run_dir,
    read_current_state,
)
from strategy_research.core.backtest import (
    run_backtest_from_yaml,
    run_backtest_script,
    update_results_tsv,
)
from strategy_research.core.study.runner import AutoresearchRunner


def _write_strategy(strat_dir: Path, name: str = "demo") -> None:
    strat_dir.mkdir(parents=True, exist_ok=True)
    (strat_dir / "strategy.py").write_text(
        "PARAMS = {}\nFACTOR_EXPRS = []\nFACTOR_WEIGHT_METHOD = 'equal'\n",
        encoding="utf-8",
    )
    (strat_dir / "config.yaml").write_text(
        "strategy_name: demo\nsymbols: []\n", encoding="utf-8",
    )


# ── update_results_tsv: round column ───────────────────────────────────


def test_update_results_tsv_round_column_trailing(tmp_path):
    strat = tmp_path / "strategies" / "demo"
    update_results_tsv(
        strat, "run_0001",
        {"calmar": 0.6, "sharpe": 0.3, "status": "success", "description": "d"},
        round_num=3,
    )
    lines = (strat / "runs" / "results.tsv").read_text(encoding="utf-8").split("\n")
    header = lines[0].split("\t")
    assert header[-1] == "round"          # trailing column
    assert len(header) == 14
    row = lines[1].split("\t")
    assert row[0] == "run_0001"
    assert row[3] == "0.6"                # calmar index unchanged
    assert row[11] == "success"           # status index unchanged
    assert row[-1] == "3"


def test_update_results_tsv_custom_location(tmp_path):
    tsv = tmp_path / "study_s1" / "results.tsv"
    update_results_tsv(tmp_path / "nope", "run_0001", {}, results_tsv=tsv)
    assert tsv.exists()
    assert "run_0001" in tsv.read_text(encoding="utf-8")


def test_update_results_tsv_legacy_no_round(tmp_path):
    strat = tmp_path / "strategies" / "demo"
    update_results_tsv(strat, "run_0001", {"calmar": 0.5})  # round_num=None
    row = (strat / "runs" / "results.tsv").read_text(encoding="utf-8").split("\n")[1]
    cells = row.split("\t")
    assert len(cells) == 14               # round column present, empty value
    assert cells[-1] == ""


# ── run_backtest_script: strategy_dir / results_tsv / round ────────────


def test_run_backtest_script_legacy_default(tmp_path):
    _write_strategy(tmp_path / "strategies" / "demo")
    result = run_backtest_script(tmp_path, "demo", action="test")
    assert result["success"] is True
    run_dir = tmp_path / "strategies" / "demo" / "runs" / result["run"]
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "run.log").exists()


def test_run_backtest_script_custom_layout(tmp_path):
    """Study layout: strategy.py lives in the run dir; results.tsv at the
    study root."""
    study_root = tmp_path / "study_s1"
    runs_dir = study_root / "rounds" / "round_0001"
    _write_strategy(runs_dir)  # strategy.py + config.yaml in the run dir
    tsv = study_root / "results.tsv"
    result = run_backtest_script(
        tmp_path, "demo",
        strategy_dir=runs_dir,
        results_tsv=tsv,
        round_num=4,
        run_dir=runs_dir / "run_0001",
    )
    assert result["success"] is True
    assert result["run"] == "run_0001"
    assert (runs_dir / "run_0001" / "metrics.json").exists()
    assert tsv.exists()
    row = tsv.read_text(encoding="utf-8").split("\n")[1].split("\t")
    assert row[-1] == "4"


# ── run_backtest_from_yaml: custom layout ──────────────────────────────


def test_run_backtest_from_yaml_custom_layout(tmp_path, monkeypatch):
    import types as _types


    study_root = tmp_path / "study_s2"
    rounds = study_root / "rounds" / "round_0002"
    runs_dir = rounds / "runs"
    _write_strategy(rounds)
    tsv = study_root / "results.tsv"

    fake = _types.SimpleNamespace(
        metrics={"calmar": 1.1, "sharpe": 0.5, "max_dd": -0.1},
        factor_failures=[],
        warnings=[],
        weights_history=None,
        nav_daily=None,
    )
    monkeypatch.setattr(
        "strategy_research.apps.config_runner.run_from_yaml",
        lambda yaml_path, ws: fake,
    )
    result = run_backtest_from_yaml(
        tmp_path, "demo",
        strategy_dir=rounds,
        runs_dir=runs_dir,
        results_tsv=tsv,
        round_num=2,
    )
    assert result["success"] is True
    run_dir = runs_dir / result["run"]
    assert run_dir.is_dir()
    assert (run_dir / "metrics.json").exists()
    assert tsv.exists()
    row = tsv.read_text(encoding="utf-8").split("\n")[1].split("\t")
    assert row[-1] == "2"


# ── read_current_state: dual sources ───────────────────────────────────


def test_read_current_state_dual_sources(tmp_path):
    strat = tmp_path / "strategies" / "demo"
    _write_strategy(strat)
    # legacy default
    state = read_current_state(tmp_path, "demo")
    assert "PARAMS" in state["strategy_py"]
    # dual overrides (study layout: strategy in run dir, tsv at study root)
    run_strategy = tmp_path / "study_s3" / "rounds" / "round_0001" / "strategy.py"
    run_strategy.parent.mkdir(parents=True)
    run_strategy.write_text("PARAMS = {'top_n': 5}\n", encoding="utf-8")
    tsv = tmp_path / "study_s3" / "results.tsv"
    tsv.write_text(
        "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\tann_return\tturnover\t"
        "factors_added\tfactors_removed\tparams_changed\tstatus\tdescription\tround\n"
        "run_0001\t\t\t0.9\t0.4\t-0.1\t0.2\t0\t0\t0\t0\tkeep\td\t1\n",
        encoding="utf-8",
    )
    state2 = read_current_state(
        tmp_path, "demo", strategy_file=run_strategy, results_tsv=tsv,
    )
    assert state2["strategy_py"] == "PARAMS = {'top_n': 5}\n"
    assert state2["best_calmar"] == pytest.approx(0.9)
    assert state2["total_runs"] == 1


# ── _create_run_dir: runs_dir override + max+1 ─────────────────────────


def test_create_run_dir_custom_runs_dir(tmp_path):
    rounds = tmp_path / "study_s4" / "rounds" / "round_0001"
    runs_dir, run_name, run_dir = _create_run_dir(tmp_path, "demo", runs_dir=rounds)
    assert runs_dir == rounds
    assert run_name == "run_0001"
    assert (run_dir / "agents").is_dir()
    # second call → run_0002 (per-dir numbering)
    _, run_name2, _ = _create_run_dir(tmp_path, "demo", runs_dir=rounds)
    assert run_name2 == "run_0002"
    # legacy default still works
    _write_strategy(tmp_path / "strategies" / "demo")
    ld, lname, _ = _create_run_dir(tmp_path, "demo")
    assert ld == (tmp_path / "strategies" / "demo" / "runs")
    assert lname == "run_0001"


# ── runner._update_results_tsv: composite match ────────────────────────


def test_runner_tsv_composite_match(tmp_path):
    tsv = tmp_path / "results.tsv"
    header = ("run\tcommit\taction\tcalmar\tsharpe\tmax_dd\tann_return\t"
              "turnover\tfactors_added\tfactors_removed\tparams_changed\t"
              "status\tdescription\tround\n")
    tsv.write_text(
        header
        + "run_0001\t\t\t0.5\t0.3\t-0.1\t0\t0\t0\t0\t0\tpending\td\t1\n"
        + "run_0001\t\t\t0.6\t0.4\t-0.1\t0\t0\t0\t0\t0\tpending\td\t2\n",
        encoding="utf-8",
    )
    AutoresearchRunner._update_results_tsv(
        tmp_path, "run_0001", "keep", round_num=2, results_tsv=tsv,
    )
    lines = tsv.read_text(encoding="utf-8").split("\n")
    assert lines[1].split("\t")[11] == "pending"  # round 1 untouched
    assert lines[2].split("\t")[11] == "keep"     # round 2 updated


# ── ToolContext roots reach tools ──────────────────────────────────────


def test_write_file_honors_custom_write_roots(tmp_path):
    reg = build_default_registry()
    tool = reg.get("write")
    study = tmp_path / "study"
    out = tool.invoke({
        "ctx": ToolContext(
            workspace=tmp_path, write_roots=("study",),
        ),
        "path": "study/s5/rounds/round_0001/run_0001/strategy.py",
        "content": "PARAMS = {}\n",
    })
    assert '"status": "ok"' in out
    assert (study / "s5" / "rounds" / "round_0001" / "run_0001" / "strategy.py").exists()
    # without the root, the same path is rejected (legacy behavior)
    out2 = tool.invoke({
        "ctx": ToolContext(workspace=tmp_path),
        "path": "study/s5/x.py",
        "content": "x = 1\n",
    })
    assert "error" in out2


def test_read_file_honors_custom_read_roots(tmp_path):
    reg = build_default_registry()
    tool = reg.get("read")
    target = tmp_path / "study" / "s6" / "manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    # custom read_roots override the default root set: study/ readable,
    # while a default root (data/) is no longer readable
    out = tool.invoke({
        "ctx": ToolContext(workspace=tmp_path, read_roots=("study",)),
        "path": "study/s6/manifest.json",
    })
    assert '"status": "ok"' in out
    out2 = tool.invoke({
        "ctx": ToolContext(workspace=tmp_path, read_roots=("study",)),
        "path": "data/foo.csv",
    })
    assert "error" in out2
