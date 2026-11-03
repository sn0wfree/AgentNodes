"""Tests for apps/backtest.py — 12 函数 + 边界.

借鉴自 vibe-trading 测试风格. 使用 tmp_path + monkeypatch 隔离.
"""
from __future__ import annotations

import json

from strategy_research.apps.backtest import (
    create_run_dir,
    evaluate_experiment,
    get_best_experiment,
    get_experiment_history,
    get_next_run_name,
    parse_run_log,
    update_results_tsv,
)

# ============================================================
# 1. parse_run_log — 8 个 metric patterns + 缺失文件
# ============================================================

def test_parse_run_log_handles_all_metrics(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "calmar: 1.5\n"
        "sharpe: 0.8\n"
        "max_dd: -0.15\n"
        "ann_return: 0.12\n"
        "ann_vol: 0.18\n"
        "sortino: 1.1\n"
        "turnover: 2.5\n"
        "win_rate: 0.55\n",
        encoding="utf-8",
    )
    metrics = parse_run_log(log)
    assert metrics == {
        "calmar": 1.5,
        "sharpe": 0.8,
        "max_dd": -0.15,
        "ann_return": 0.12,
        "ann_vol": 0.18,
        "sortino": 1.1,
        "turnover": 2.5,
        "win_rate": 0.55,
    }


def test_parse_run_log_missing_file_returns_empty(tmp_path):
    metrics = parse_run_log(tmp_path / "nonexistent.log")
    assert metrics == {}


def test_parse_run_log_ignores_non_matching_lines(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "INFO: starting\n"
        "calmar: 2.0\n"
        "DEBUG: foo\n"
        "ann_return: 0.10\n",
        encoding="utf-8",
    )
    metrics = parse_run_log(log)
    assert metrics == {"calmar": 2.0, "ann_return": 0.10}


def test_parse_run_log_case_insensitive(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("Calmar: 1.0\nSHARPE: 0.5\n", encoding="utf-8")
    metrics = parse_run_log(log)
    assert metrics == {"calmar": 1.0, "sharpe": 0.5}


def test_parse_run_log_empty_file(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    metrics = parse_run_log(log)
    assert metrics == {}


def test_parse_run_log_extracts_trades_int(tmp_path):
    """trades: 12 (整数形式) → metrics['trades'] == 12.0"""
    log = tmp_path / "run.log"
    log.write_text(
        "calmar: 1.0\nsharpe: 0.5\nmax_dd: -0.10\ntrades: 12\n",
        encoding="utf-8",
    )
    metrics = parse_run_log(log)
    assert metrics["trades"] == 12.0
    assert isinstance(metrics["trades"], float)


def test_parse_run_log_extracts_trades_float(tmp_path):
    """trades: 24.0 (浮点形式) → metrics['trades'] == 24.0"""
    log = tmp_path / "run.log"
    log.write_text("trades: 24.0\n", encoding="utf-8")
    metrics = parse_run_log(log)
    assert metrics["trades"] == 24.0


# ============================================================
# 2. get_next_run_name — 顺序递增
# ============================================================

def test_get_next_run_name_empty_dir_returns_0001(tmp_path):
    """无 runs/ 目录或 runs/ 为空 → run_0001."""
    runs = tmp_path / "runs"
    runs.mkdir()
    assert get_next_run_name(tmp_path) == "run_0001"


def test_get_next_run_name_no_runs_dir(tmp_path):
    """runs/ 不存在 → run_0001."""
    assert get_next_run_name(tmp_path) == "run_0001"


def test_get_next_run_name_increments(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "run_0005").mkdir()
    (runs / "run_0010").mkdir()
    (runs / "run_0007").mkdir()
    assert get_next_run_name(tmp_path) == "run_0011"


def test_get_next_run_name_ignores_non_run_dirs(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "run_0003").mkdir()
    (runs / "extra_random_dir").mkdir()  # 应被忽略
    (runs / "another").mkdir()
    assert get_next_run_name(tmp_path) == "run_0004"


def test_get_next_run_name_ignores_invalid_run_format(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "run_0001").mkdir()
    (runs / "run_XX").mkdir()  # 名字不合法
    (runs / "run_abc").mkdir()
    assert get_next_run_name(tmp_path) == "run_0002"


# ============================================================
# 3. create_run_dir — 幂等
# ============================================================

def test_create_run_dir_creates_directory(tmp_path):
    run_dir = create_run_dir(tmp_path, "run_0001")
    assert run_dir.exists()
    assert run_dir.is_dir()
    assert run_dir.name == "run_0001"


def test_create_run_dir_idempotent(tmp_path):
    """两次创建同名 → 不报错."""
    run_dir1 = create_run_dir(tmp_path, "run_0001")
    run_dir2 = create_run_dir(tmp_path, "run_0001")
    assert run_dir1 == run_dir2
    assert run_dir1.exists()


def test_create_run_dir_creates_parent_dirs(tmp_path):
    """runs/ 不存在 → 自动创建."""
    run_dir = create_run_dir(tmp_path, "run_0001")
    assert run_dir.parent.exists()
    assert run_dir.parent.name == "runs"


# ============================================================
# 4. save_run_snapshot — copy strategy + config
# ============================================================

def test_save_run_snapshot_copies_strategy_py(tmp_path):
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "strategy.py").write_text("# strategy code", encoding="utf-8")
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()

    from strategy_research.apps.backtest import save_run_snapshot
    save_run_snapshot(strategy_dir, run_dir)

    assert (run_dir / "strategy.py").read_text(encoding="utf-8") == "# strategy code"


def test_save_run_snapshot_copies_config_yaml(tmp_path):
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "config.yaml").write_text("key: val", encoding="utf-8")
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()

    from strategy_research.apps.backtest import save_run_snapshot
    save_run_snapshot(strategy_dir, run_dir)

    assert (run_dir / "config.yaml").read_text(encoding="utf-8") == "key: val"


def test_save_run_snapshot_handles_missing_files(tmp_path):
    """strategy.py 和 config.yaml 都不存在 → 不报错."""
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()

    from strategy_research.apps.backtest import save_run_snapshot
    save_run_snapshot(strategy_dir, run_dir)  # 不应抛异常

    assert not (run_dir / "strategy.py").exists()
    assert not (run_dir / "config.yaml").exists()


# ============================================================
# 5. save_run_metrics — JSON 格式
# ============================================================

def test_save_run_metrics_writes_valid_json(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    metrics = {"calmar": 1.5, "sharpe": 0.8}

    from strategy_research.apps.backtest import save_run_metrics
    save_run_metrics(run_dir, metrics)

    loaded = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert loaded == metrics


def test_save_run_metrics_preserves_unicode(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    metrics = {"description": "测试中文", "name": "策略α"}

    from strategy_research.apps.backtest import save_run_metrics
    save_run_metrics(run_dir, metrics)

    raw = (run_dir / "metrics.json").read_text(encoding="utf-8")
    assert "测试中文" in raw  # unicode 保留
    assert "策略α" in raw


# ============================================================
# 6. update_results_tsv — header + append + round-trip
# ============================================================

def test_update_results_tsv_creates_header(tmp_path):
    strategy_dir = tmp_path / "demo_strategy"
    strategy_dir.mkdir()
    update_results_tsv(
        strategy_dir, "run_0001",
        {"calmar": 1.5, "sharpe": 0.8, "status": "pending", "description": "test"},
    )
    text = (strategy_dir / "runs" / "results.tsv").read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert len(lines) == 2  # header + 1 row
    assert lines[0].startswith("run\tcommit\taction\tcalmar")
    assert lines[1].startswith("run_0001")


def test_update_results_tsv_appends_runs(tmp_path):
    strategy_dir = tmp_path / "demo_strategy"
    strategy_dir.mkdir()
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 1.5, "sharpe": 0.8})
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 2.0, "sharpe": 0.9})
    text = (strategy_dir / "runs" / "results.tsv").read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "run_0001" in lines[1]
    assert "run_0002" in lines[2]


def test_update_results_tsv_roundtrip(tmp_path):
    """TSV 可回读."""
    strategy_dir = tmp_path / "demo_strategy"
    strategy_dir.mkdir()
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 1.5, "sharpe": 0.8, "status": "keep"})
    results_path = strategy_dir / "runs" / "results.tsv"
    assert results_path.exists()
    raw = results_path.read_text(encoding="utf-8")
    assert "run_0001" in raw
    assert "1.5" in raw
    assert "0.8" in raw
    assert "keep" in raw


def test_update_results_tsv_uses_defaults(tmp_path):
    """缺字段时用 default 值."""
    strategy_dir = tmp_path / "demo_strategy"
    strategy_dir.mkdir()
    update_results_tsv(strategy_dir, "run_0001", {})
    text = (strategy_dir / "runs" / "results.tsv").read_text(encoding="utf-8")
    assert "\t0.0\t" in text  # calmar/sharpe 缺省值 0.0
    assert "\tpending\t" in text


# ============================================================
# 7. run_strategy — subprocess 边界
# ============================================================

def test_run_strategy_missing_file(tmp_path):
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    from strategy_research.apps.backtest import run_strategy
    success, output = run_strategy(strategy_dir)
    assert success is False
    assert "不存在" in output or "not found" in output.lower()


def test_run_strategy_successful_execution(tmp_path):
    """创建一个会打印指标的策略脚本."""
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    strategy_file = strategy_dir / "strategy.py"
    strategy_file.write_text(
        "print('calmar: 1.5')\n"
        "print('sharpe: 0.8')\n",
        encoding="utf-8",
    )
    from strategy_research.apps.backtest import run_strategy
    success, output = run_strategy(strategy_dir)
    assert success is True
    assert "calmar: 1.5" in output
    assert "sharpe: 0.8" in output


def test_run_strategy_timeout(monkeypatch, tmp_path):
    """日志停滞（进程存活但无输出）→ kill + 优雅返回（停滞语义）."""
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "strategy.py").write_text(
        "print('start', flush=True); import time; time.sleep(600)",
        encoding="utf-8",
    )
    from strategy_research.apps.backtest import run_strategy
    log_path = tmp_path / "run.log"
    success, output = run_strategy(strategy_dir, timeout=1, log_path=log_path)
    assert success is False
    assert "停滞" in output or "stalled" in output
    assert "start" in output  # 卡死前已产出的日志保留


def test_run_strategy_log_progress_runs_long(monkeypatch, tmp_path):
    """持续写日志的长策略（超过停滞窗口）→ 正常跑完，不被误杀."""
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "strategy.py").write_text(
        "import time\n"
        "for i in range(6):\n"
        "    print(f'step {i}', flush=True)\n"
        "    time.sleep(0.3)\n"
        "print('done', flush=True)\n",
        encoding="utf-8",
    )
    from strategy_research.apps.backtest import run_strategy
    log_path = tmp_path / "run.log"
    # 总时长 1.8s > 停滞窗口 0.5s，但日志持续推进 → 完成
    success, output = run_strategy(strategy_dir, timeout=0.5, log_path=log_path)
    assert success is True
    assert "done" in output


def test_run_strategy_general_exception(monkeypatch, tmp_path):
    """后台启动抛通用异常 → 优雅返回."""
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "strategy.py").write_text("print(1)", encoding="utf-8")

    def fake_run_bg(*args, **kwargs):
        raise OSError("simulated I/O error")

    from strategy_research.foundation.utils import bg_proc
    monkeypatch.setattr(bg_proc, "run_bg", fake_run_bg)
    from strategy_research.apps.backtest import run_strategy
    success, output = run_strategy(strategy_dir)
    assert success is False
    assert "失败" in output or "OSError" in output


# ============================================================
# 8. run_backtest_script — 完整流程需要 mock duckdb & git
# ============================================================

def test_run_backtest_script_missing_strategy_dir(tmp_path):
    """strategy_dir 不存在 → 返回错误 dict, 不抛异常."""
    from strategy_research.apps.backtest import run_backtest_script
    result = run_backtest_script(tmp_path, "nonexistent_strategy")
    assert result["success"] is False
    assert result["run"] == ""
    assert "策略目录不存在" in result["error"]


def test_run_backtest_script_successful_run(monkeypatch, tmp_path):
    """完整链路: setup strategy dir → mock duckdb/git → 验证 artifacts."""
    # 1. strategy dir
    strategy_dir = tmp_path / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text(
        "print('calmar: 1.0')\nprint('sharpe: 0.5')\n",
        encoding="utf-8",
    )

    # 2. mock DuckDB save
    from strategy_research.apps import db as db_module

    def fake_save_backtest_result(**kwargs):
        return None

    monkeypatch.setattr(db_module, "save_backtest_result", fake_save_backtest_result)
    monkeypatch.setattr(
        "strategy_research.apps.backtest.save_backtest_result",
        fake_save_backtest_result,
    )

    # 3. mock git_get_hash
    from strategy_research.apps import backtest as bt_module
    monkeypatch.setattr(bt_module, "git_get_hash", lambda *a, **kw: "abc1234")

    # 4. 执行
    result = bt_module.run_backtest_script(tmp_path, "demo", action="test")

    # 5. 验证
    assert result["success"] is True
    assert result["run"] == "run_0001"
    assert result["metrics"]["calmar"] == 1.0
    assert result["metrics"]["sharpe"] == 0.5
    assert result["metrics"]["commit"] == "abc1234"

    run_dir = strategy_dir / "runs" / "run_0001"
    assert run_dir.exists()
    assert (run_dir / "strategy.py").exists()
    assert (run_dir / "run.log").exists()
    assert (run_dir / "metrics.json").exists()
    results_tsv = strategy_dir / "runs" / "results.tsv"
    assert results_tsv.exists()
    # run_card.{json,md} 也应生成
    assert (run_dir / "run_card.json").exists(), "run_card.json 应被生成"
    assert (run_dir / "run_card.md").exists(), "run_card.md 应被生成"
    run_card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert run_card["strategy_hashes"].get("strategy.py"), "strategy hash 应被记录"


# ============================================================
# 9. run_backtest_from_yaml — 边界
# ============================================================

def test_run_backtest_from_yaml_missing_yaml(tmp_path):
    """yaml 不存在 → 返回错误 dict."""
    strategy_dir = tmp_path / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)

    from strategy_research.apps.backtest import run_backtest_from_yaml
    result = run_backtest_from_yaml(tmp_path, "demo", yaml_path=tmp_path / "missing.yaml")
    assert result["success"] is False
    assert "不存在" in result["error"]


def test_run_backtest_from_yaml_default_yaml_path(tmp_path):
    """未指定 yaml_path → 用默认 workspace/strategies/<name>/config.yaml."""
    strategy_dir = tmp_path / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    # 不创建 config.yaml

    from strategy_research.apps.backtest import run_backtest_from_yaml
    result = run_backtest_from_yaml(tmp_path, "demo")
    assert result["success"] is False
    assert "不存在" in result["error"]


# ============================================================
# 10. evaluate_experiment — 状态更新
# ============================================================

def test_evaluate_experiment_missing_dir(tmp_path):
    """run_dir 不存在 → 返回 False."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    success = evaluate_experiment(workspace, "demo", "run_0001")
    assert success is False


def test_evaluate_experiment_missing_metrics(tmp_path):
    """metrics.json 不存在 → 返回 False."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    strategy_dir = workspace / "strategies" / "demo" / "runs" / "run_0001"
    strategy_dir.mkdir(parents=True)
    success = evaluate_experiment(workspace, "demo", "run_0001")
    assert success is False


def test_evaluate_experiment_keep_status(monkeypatch, tmp_path):
    """metrics.json 存在 → 更新 status='keep'."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    strategy_dir = workspace / "strategies" / "demo"
    run_dir = strategy_dir / "runs" / "run_0001"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"calmar": 1.0, "status": "pending"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (strategy_dir / "runs").mkdir(exist_ok=True)
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 1.0, "status": "pending"})

    from strategy_research.apps import backtest as bt_module
    monkeypatch.setattr(bt_module, "git_commit", lambda *a, **kw: True)

    from strategy_research.apps import db as db_module

    class FakeConn:
        def execute(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(db_module, "get_connection", lambda *a, **kw: FakeConn())

    success = evaluate_experiment(workspace, "demo", "run_0001", status="keep")
    assert success is True

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "keep"


# ============================================================
# 11. get_experiment_history — TSV 解析
# ============================================================

def test_get_experiment_history_empty(tmp_path):
    """无 results.tsv → []."""
    workspace = tmp_path
    history = get_experiment_history(workspace, "demo")
    assert history == []


def test_get_experiment_history_header_only(tmp_path):
    """只有 header → []."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    (strategy_dir / "runs").mkdir(parents=True)
    (strategy_dir / "runs" / "results.tsv").write_text(
        "run\tcommit\tcalmar\tstatus\n",
        encoding="utf-8",
    )
    history = get_experiment_history(workspace, "demo")
    assert history == []


def test_get_experiment_history_with_runs(tmp_path):
    """多行数据 → 解析回."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 1.0, "status": "keep"})
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 2.0, "status": "pending"})

    history = get_experiment_history(workspace, "demo")
    assert len(history) == 2
    assert history[0]["run"] == "run_0001"
    assert history[1]["run"] == "run_0002"
    assert history[0]["calmar"] == "1.0"


def test_get_experiment_history_limit(tmp_path):
    """limit 参数生效."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    for i in range(1, 6):
        update_results_tsv(strategy_dir, f"run_{i:04d}", {"calmar": float(i)})

    history = get_experiment_history(workspace, "demo", limit=3)
    assert len(history) == 3
    assert history[0]["run"] == "run_0001"
    assert history[2]["run"] == "run_0003"


# ============================================================
# 12. get_best_experiment — 取 max metric
# ============================================================

def test_get_best_experiment_no_history(tmp_path):
    workspace = tmp_path
    result = get_best_experiment(workspace, "demo")
    assert result is None


def test_get_best_experiment_no_keep(tmp_path):
    """无 status='keep' → None."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 2.0, "status": "pending"})
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 1.0, "status": "rejected"})
    result = get_best_experiment(workspace, "demo")
    assert result is None


def test_get_best_experiment_filters_keeps_and_max(tmp_path):
    """只考虑 keep, 取 max calmar."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 1.0, "status": "pending"})
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 2.5, "status": "keep"})
    update_results_tsv(strategy_dir, "run_0003", {"calmar": 1.8, "status": "keep"})

    result = get_best_experiment(workspace, "demo")
    assert result is not None
    assert result["run"] == "run_0002"
    assert result["calmar"] == "2.5"


def test_get_best_experiment_custom_metric(tmp_path):
    """goal_metric='sharpe' 用 sharpe 取 max."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    update_results_tsv(strategy_dir, "run_0001", {"calmar": 2.0, "sharpe": 0.5, "status": "keep"})
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 1.0, "sharpe": 1.2, "status": "keep"})

    result = get_best_experiment(workspace, "demo", goal_metric="sharpe")
    assert result is not None
    assert result["run"] == "run_0002"


def test_get_best_experiment_handles_invalid_metric_value(tmp_path):
    """metric 字段为非数字 → 用 0 fallback."""
    workspace = tmp_path
    strategy_dir = workspace / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    update_results_tsv(
        strategy_dir, "run_0001", {"calmar": "NOT_A_NUMBER", "status": "keep"},
    )
    update_results_tsv(strategy_dir, "run_0002", {"calmar": 1.0, "status": "keep"})
    result = get_best_experiment(workspace, "demo")
    assert result is not None
    assert result["run"] == "run_0002"  # run_0001 的 calmar fallback 为 0


# ============================================================
# Commit 1: NaN→null JSON, status success, factor_failures 落盘
# ============================================================

def test_save_run_metrics_cleans_nan_to_null(tmp_path):
    """NaN/Inf 必须序列化为合法 JSON (null)，而不是 NaN 字面量。"""

    from strategy_research.apps.backtest import save_run_metrics

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    metrics = {
        "ann_return": float("nan"),
        "sharpe": float("inf"),
        "ok": 1.5,
        "nested": {"calmar": float("nan"), "x": 1},
        "list": [float("nan"), 2],
    }
    save_run_metrics(run_dir, metrics)
    raw = (run_dir / "metrics.json").read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    loaded = json.loads(raw)
    assert loaded["ann_return"] is None
    assert loaded["sharpe"] is None
    assert loaded["ok"] == 1.5
    assert loaded["nested"] == {"calmar": None, "x": 1}
    assert loaded["list"] == [None, 2]


def test_factor_failures_summary_aggregates(tmp_path):
    from strategy_research.apps.backtest import _factor_failures_summary

    failures = [
        {"factor": "low_volatility", "asset": "600519.SH", "error": "e1", "occurrences": 2},
        {"factor": "low_volatility", "asset": "000858.SZ", "error": "e2", "occurrences": 1},
        {"factor": "momentum_20d", "asset": "600519.SH", "error": "e3", "occurrences": 1},
    ]
    summary = _factor_failures_summary(failures)
    assert len(summary) == 2
    by_name = {s["factor"]: s for s in summary}
    assert by_name["low_volatility"]["failed_assets"] == 2
    assert by_name["momentum_20d"]["failed_assets"] == 1
    assert by_name["low_volatility"]["first_error"] == "e1"


def test_factor_failures_summary_caps_at_5(tmp_path):
    from strategy_research.apps.backtest import _factor_failures_summary

    failures = [
        {"factor": f"f{i}", "asset": "A", "error": "x", "occurrences": 1}
        for i in range(8)
    ]
    summary = _factor_failures_summary(failures)
    assert len(summary) == 6  # 5 + "..." 聚合项
    assert summary[-1]["factor"] == "..."
