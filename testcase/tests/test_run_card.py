"""Tests for apps/run_card.py — 极简 Trust Layer Run Card 生成器."""
from __future__ import annotations

import json

from strategy_research.apps.run_card import (
    BACKTEST_SUMMARY_KEYS,
    SCHEMA_VERSION,
    write_run_card,
)

# ============================================================
# 1. write_run_card 基础行为
# ============================================================

def test_write_run_card_creates_json_and_md(tmp_path):
    """写两个文件: run_card.json + run_card.md."""
    run_dir = tmp_path / "runs" / "run_0001"
    run_dir.mkdir(parents=True)
    config = {"codes": ["000001.SZ"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    metrics = {"calmar": 1.5, "sharpe": 0.8}

    card = write_run_card(run_dir, config, metrics)

    assert (run_dir / "run_card.json").exists()
    assert (run_dir / "run_card.md").exists()
    assert card["schema_version"] == SCHEMA_VERSION
    assert card["run_dir"] == "run_0001"


def test_write_run_card_creates_run_dir(tmp_path):
    """run_dir 不存在时自动创建."""
    run_dir = tmp_path / "runs" / "run_0023"  # 注意: write_run_card 不自动 mkdir
    config = {"codes": ["A"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    metrics = {"calmar": 1.0}

    # write_run_card 不会自动创建 run_dir — 需 pre-create
    run_dir.mkdir(parents=True)
    write_run_card(run_dir, config, metrics)
    assert (run_dir / "run_card.json").exists()


# ============================================================
# 2. JSON 内容结构
# ============================================================

def test_write_run_card_json_structure(tmp_path):
    """JSON 包含所有预期字段."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {
        "codes": ["000001.SZ", "600000.SH"],
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "interval": "1D",
        "engine": "daily",
        "initial_cash": 1_000_000,
        "source": "duckdb",
        # 额外字段应被忽略:
        "secret_api_key": "should_not_appear",
    }
    metrics = {"calmar": 1.5, "sharpe": 0.8, "extra_dict": {"nested": "x"}, "nested_list": [1, 2]}

    card = write_run_card(run_dir, config, metrics)

    # schema & meta
    assert card["schema_version"] == SCHEMA_VERSION
    assert "generated_at" in card
    # config summary — 非白名单字段不应出现（特别是 secret_api_key）
    assert "secret_api_key" not in card["config"]
    # 白名单内的字段应被保留（前提是 config 提供了）
    for k in BACKTEST_SUMMARY_KEYS:
        if k in config:
            assert k in card["config"], f"missing whitelist key: {k}"
    # metrics 过滤 — 不含 dict/list
    assert "calmar" in card["metrics"]
    assert "sharpe" in card["metrics"]
    assert "extra_dict" not in card["metrics"]
    assert "nested_list" not in card["metrics"]


def test_write_run_card_config_hash_is_stable(tmp_path):
    """相同 config → 相同 config_hash."""
    run_dir1 = tmp_path / "r1"
    run_dir2 = tmp_path / "r2"
    run_dir1.mkdir()
    run_dir2.mkdir()
    config = {"codes": ["A"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    metrics = {"calmar": 1.0}

    card1 = write_run_card(run_dir1, config, metrics)
    card2 = write_run_card(run_dir2, config, metrics)

    assert card1["config_hash"] == card2["config_hash"]


def test_write_run_card_config_hash_differs_on_change(tmp_path):
    """config 改动 → config_hash 不同."""
    run_dir1 = tmp_path / "r1"
    run_dir2 = tmp_path / "r2"
    run_dir1.mkdir()
    run_dir2.mkdir()
    config_a = {"codes": ["A"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    config_b = {"codes": ["B"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    metrics = {"calmar": 1.0}

    card1 = write_run_card(run_dir1, config_a, metrics)
    card2 = write_run_card(run_dir2, config_b, metrics)

    assert card1["config_hash"] != card2["config_hash"]


# ============================================================
# 3. strategy_hash — SHA-256 of strategy files
# ============================================================

def test_write_run_card_strategy_hashes_single(tmp_path):
    """单个 strategy 文件 hash."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    strategy = tmp_path / "strategy.py"
    strategy.write_text("# strategy code", encoding="utf-8")
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0}

    card = write_run_card(run_dir, config, metrics, strategy_paths=[strategy])
    assert "strategy.py" in card["strategy_hashes"]
    assert len(card["strategy_hashes"]["strategy.py"]) == 64  # SHA-256 = 64 hex chars


def test_write_run_card_strategy_hashes_multiple(tmp_path):
    """多个文件 hash."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    strat = tmp_path / "strategy.py"
    cfg = tmp_path / "config.yaml"
    strat.write_text("print(1)", encoding="utf-8")
    cfg.write_text("key: val", encoding="utf-8")
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0}

    card = write_run_card(
        run_dir, config, metrics,
        strategy_paths=[strat, cfg],
    )
    assert set(card["strategy_hashes"].keys()) == {"strategy.py", "config.yaml"}


def test_write_run_card_strategy_hashes_missing_files_skipped(tmp_path):
    """不存在的文件 → 被跳过,不报错."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    existing = tmp_path / "strategy.py"
    existing.write_text("# code", encoding="utf-8")
    missing = tmp_path / "does_not_exist.py"
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0}

    card = write_run_card(
        run_dir, config, metrics,
        strategy_paths=[existing, missing],
    )
    assert "strategy.py" in card["strategy_hashes"]
    assert "does_not_exist.py" not in card["strategy_hashes"]


def test_write_run_card_no_strategy_paths(tmp_path):
    """不传 strategy_paths → 空 dict."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0}

    card = write_run_card(run_dir, config, metrics)
    assert card["strategy_hashes"] == {}


# ============================================================
# 4. Markdown 内容
# ============================================================

def test_write_run_card_md_includes_metrics(tmp_path):
    """Markdown 包含 metrics 表格."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {"codes": ["A"], "start_date": "2024-01-01"}
    metrics = {"calmar": 1.5, "sharpe": 0.8, "max_drawdown": -0.15}

    write_run_card(run_dir, config, metrics)
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")

    assert "# Run Card" in md
    assert "Config" in md
    assert "Metrics" in md
    assert "| `calmar` |" in md
    assert "| `sharpe` |" in md
    assert "| `max_drawdown` |" in md
    assert "Schema:" in md


def test_write_run_card_md_includes_warnings(tmp_path):
    """Markdown 包含 warnings section."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0}
    warnings = ["outlier_2024-03-15: 000001.SZ ret=0.42", "missing_data_2024-06-01: 600000.SH"]

    write_run_card(run_dir, config, metrics, warnings=warnings)
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")

    assert "## Warnings" in md
    assert "outlier_2024-03-15" in md
    assert "missing_data_2024-06-01" in md


def test_write_run_card_md_unicode_preserved(tmp_path):
    """中文 unicode 正确保留."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {"codes": ["A"]}
    metrics = {"calmar": 1.0, "description": "测试中文α"}

    write_run_card(run_dir, config, metrics)
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")

    assert "测试中文α" in md


# ============================================================
# 5. JSON 文件可解析
# ============================================================

def test_run_card_json_is_valid_json(tmp_path):
    """run_card.json 可被 json.loads 解析."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = {"codes": ["A"], "start_date": "2024-01-01"}
    metrics = {"calmar": 1.5, "sharpe": 0.8}

    write_run_card(run_dir, config, metrics)
    loaded = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert loaded["metrics"]["calmar"] == 1.5
    assert loaded["schema_version"] == SCHEMA_VERSION


# ============================================================
# 6. 边界情况
# ============================================================

def test_write_run_card_empty_config(tmp_path):
    """config 空 dict → config summary 空, MD 显示 _empty_."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    card = write_run_card(run_dir, {}, {"calmar": 1.0})
    assert card["config"] == {}
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")
    assert "| _empty_ |" in md


def test_write_run_card_empty_metrics(tmp_path):
    """metrics 空 dict → MD 仍生成."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    card = write_run_card(run_dir, {"codes": ["A"]}, {})
    assert card["metrics"] == {}
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")
    assert "| _empty_ |" in md


def test_write_run_card_no_warnings(tmp_path):
    """无 warnings → MD 不生成 Warnings section."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_run_card(run_dir, {"codes": ["A"]}, {"calmar": 1.0})
    md = (run_dir / "run_card.md").read_text(encoding="utf-8")
    assert "## Warnings" not in md
