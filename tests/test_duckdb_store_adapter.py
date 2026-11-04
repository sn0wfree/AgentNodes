"""A-class: DuckDBDataStore facade must speak core.db's real signature."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from strategy_research.apps.duckdb_store import DuckDBDataStore


def test_save_factor_data_positional_args(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    values = pd.DataFrame({"A": [1.0]}, index=pd.to_datetime(["2024-01-01"]))
    with patch(
        "strategy_research.apps.duckdb_store._db.save_factor_data",
        return_value=True,
    ) as mock_save:
        assert store.save_factor_data(
            tmp_path, strategy_name="s", factor_name="f", values=values
        )
        args, kwargs = mock_save.call_args
        assert args[0] == tmp_path
        assert args[1] == "s"
        assert args[2] == "f"
        assert args[3] is values
        assert not kwargs


def test_save_backtest_result_unpacks_dict(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    result = {"calmar": 1.5, "sharpe": 2.0, "status": "ok"}
    with patch(
        "strategy_research.apps.duckdb_store._db.save_backtest_result",
        return_value=True,
    ) as mock_save:
        store.save_backtest_result(
            tmp_path, run_id="r1", strategy_name="s", result=result
        )
        args, kwargs = mock_save.call_args
        assert args[1] == "s"
        assert args[2] == "r1"
        assert kwargs["calmar"] == 1.5
        assert kwargs["sharpe"] == 2.0
        assert kwargs["status"] == "ok"
        assert "result" not in kwargs
        assert "run_id" not in kwargs


def test_cache_validation_unpacks_metrics(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    metrics = {"ic_mean": 0.05, "ir": 1.2, "is_valid": True}
    with patch(
        "strategy_research.apps.duckdb_store._db.cache_validation",
        return_value=True,
    ) as mock_cache:
        store.cache_validation(
            tmp_path, strategy_name="s", factor_name="f", metrics=metrics, ttl_seconds=1
        )
        args, kwargs = mock_cache.call_args
        assert args[1] == "s"
        assert args[2] == "f"
        assert kwargs["ic_mean"] == 0.05
        assert kwargs["ir"] == 1.2
        assert kwargs["is_valid"] is True
        assert "metrics" not in kwargs
        assert "ttl_seconds" not in kwargs


def test_save_weight_history_dataframe_to_legacy_list(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    weights = pd.DataFrame(
        {"A": [0.6, 0.5], "B": [0.4, 0.5]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )
    with patch(
        "strategy_research.apps.duckdb_store._db.save_weight_history",
        return_value=True,
    ) as mock_save:
        store.save_weight_history(
            tmp_path, run_id="r1", weights=weights, strategy_name="s"
        )
        args, _ = mock_save.call_args
        assert args[1] == "s"
        assert args[2] == "r1"
        history = args[3]
        assert len(history) == 2
        assert history[0][1] == {"A": 0.6, "B": 0.4}


def test_update_import_meta_loops_codes(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    with patch(
        "strategy_research.apps.duckdb_store._db.update_import_meta",
        return_value=True,
    ) as mock_up:
        store.update_import_meta(
            tmp_path,
            codes=["A", "B"],
            import_date=1700000000.0,
            source="ak",
            strategy_name="s",
        )
        assert mock_up.call_count == 2
        assert mock_up.call_args_list[0].args[2] == "A"
        assert mock_up.call_args_list[1].args[2] == "B"
        assert mock_up.call_args_list[0].args[3] == "1700000000.0"


def test_update_data_fingerprint_serializes_dict(tmp_path: Path) -> None:
    store = DuckDBDataStore()
    with patch(
        "strategy_research.apps.duckdb_store._db.update_data_fingerprint",
        return_value=True,
    ) as mock_fp:
        store.update_data_fingerprint(
            tmp_path, strategy_name="s", fingerprint={"rows": 10}
        )
        args, kwargs = mock_fp.call_args
        assert args[1] == "s"
        assert args[2] == "ohlcv"
        assert "rows" in str(args[3])
        assert kwargs.get("row_count") == 0 or args[4] == 0
