"""P0-2 E — DataStore Protocol + Registry tests.

Covers: Protocol runtime_checkable, DuckDBDataStore delegation, Registry
register/unregister, get_store with default + unknown name, and a
smoke test that save_ohlcv → load_price_data round-trips via the
Protocol surface (not the legacy ``db.py`` import path).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.storage import (
    DataStoreRegistry,
    get_store,
)
from strategy_research.foundation.storage.data_store import DataStore
from strategy_research.foundation.storage.duckdb_store import DuckDBDataStore


class TestDataStoreProtocol:
    def test_duckdb_store_satisfies_protocol(self):
        store = DuckDBDataStore()
        assert isinstance(store, DataStore)

    def test_protocol_has_documented_methods(self):
        names = {
            "init", "get_connection",
            "save_ohlcv", "load_price_data", "load_ohlcv_data",
            "save_factor_data", "load_factor_data",
            "register_factor", "list_factors", "remove_factor",
            "save_backtest_result", "list_backtest_results",
            "get_backtest_result",
            "save_weight_history", "load_weight_history",
            "save_nav_history", "load_nav_history",
            "cache_validation", "list_validation_cache",
            "get_last_import_date", "update_import_meta",
            "update_data_fingerprint", "get_data_fingerprint",
        }
        assert names.issubset(set(dir(DataStore)))


class TestRegistry:
    def test_default_is_duckdb(self):
        assert DataStoreRegistry.available() == ["duckdb"]
        store = get_store()
        assert isinstance(store, DuckDBDataStore)

    def test_get_store_caches_instance(self):
        a = get_store()
        b = get_store()
        assert a is b

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError) as ei:
            get_store("nope")
        assert "nope" in str(ei.value)
        assert "duckdb" in str(ei.value)

    def test_register_unregister(self):
        class FakeStore:
            pass

        DataStoreRegistry.register("fake", FakeStore)
        assert "fake" in DataStoreRegistry.available()
        assert isinstance(get_store("fake"), FakeStore)
        DataStoreRegistry.unregister("fake")
        assert "fake" not in DataStoreRegistry.available()

    def test_register_invalidates_instance_cache(self):
        class FirstStore:
            pass

        class SecondStore:
            pass

        DataStoreRegistry.register("swap", FirstStore)
        first = get_store("swap")
        assert isinstance(first, FirstStore)

        DataStoreRegistry.register("swap", SecondStore)
        # Re-registered → cache invalidated → returns the new class.
        second = get_store("swap")
        assert isinstance(second, SecondStore)
        DataStoreRegistry.unregister("swap")


class TestDuckDBDataStoreRoundTrip:
    def test_save_ohlcv_then_load_price_data(self, tmp_path):
        """Smoke: save_ohlcv via Protocol → load_price_data via Protocol."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        store: DataStore = get_store()

        df = pd.DataFrame(
            {
                "open": [1.0, 2.0],
                "high": [1.5, 2.5],
                "low": [0.5, 1.5],
                "close": [1.2, 2.1],
                "volume": [100, 200],
            },
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        df.index.name = "date"

        store.init(workspace)
        rows = store.save_ohlcv(
            workspace, {"XSHG_000001": df}, strategy_name="t1",
        )
        assert rows == 2

        loaded = store.load_price_data(workspace, "t1")
        assert loaded.shape == (2, 1)
        assert loaded.columns.tolist() == ["XSHG_000001"]
        assert loaded.iloc[-1, 0] == pytest.approx(2.1)

    def test_save_load_factor_registry(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        store: DataStore = get_store()
        store.init(workspace)

        assert store.register_factor(
            workspace,
            strategy_name="t1",
            factor_name="mom_20",
            formula="close.pct_change(20)",
            tags=["momentum"],
        )
        factors = store.list_factors(workspace, strategy_name="t1")
        names = {f.get("factor_name") for f in factors}
        assert "mom_20" in names

        tag_filtered = store.list_factors(
            workspace, strategy_name="t1", tags=["momentum"],
        )
        assert any(f.get("factor_name") == "mom_20" for f in tag_filtered)
