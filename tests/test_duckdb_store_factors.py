"""Unit tests for DuckDBDataStore.register_factor / list_factors protocol path.

Covers the store-level adapter: lineage kwargs passthrough, tag folding
into legacy ``source``, and list_factors tag filtering.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps import db as _db
from strategy_research.foundation.storage.duckdb_store import DuckDBDataStore


@pytest.fixture
def store() -> DuckDBDataStore:
    return DuckDBDataStore()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    assert _db.init_db(tmp_path)
    return tmp_path


class TestRegisterFactorLineage:
    def test_basic_register(self, store, ws):
        ok = store.register_factor(
            ws,
            strategy_name="s",
            factor_name="mom",
            formula="ts_return(close, 20)",
        )
        assert ok is True
        rows = store.list_factors(ws, strategy_name="s")
        assert rows and rows[0]["factor_name"] == "mom"

    def test_info_channel_and_parent_stored(self, store, ws):
        store.register_factor(
            ws,
            strategy_name="s",
            factor_name="mom",
            formula="ts_return(close, 20)",
            info_channel="momentum",
            parent_hypothesis_id="h-1",
        )
        row = store.list_factors(ws, strategy_name="s")[0]
        assert row.get("info_channel") == "momentum"
        assert row.get("parent_hypothesis_id") == "h-1"

    def test_complexity_autocomputed(self, store, ws):
        store.register_factor(
            ws,
            strategy_name="s",
            factor_name="mom",
            formula="ts_return(close, 20)",
        )
        row = store.list_factors(ws, strategy_name="s")[0]
        assert row.get("operator_count") == 1
        assert row.get("complexity_score") is not None

    def test_bad_formula_fails_closed(self, store, ws):
        ok = store.register_factor(
            ws,
            strategy_name="s",
            factor_name="bad",
            formula="ts_mean(close, ) $",
        )
        assert ok is False
        assert store.list_factors(ws, strategy_name="s") == []


class TestTagFolding:
    def test_tags_fold_into_source(self, store, ws):
        store.register_factor(
            ws,
            strategy_name="s",
            factor_name="f",
            formula="close",
            description="desc",
            tags=["t1", "t2"],
        )
        row = store.list_factors(ws, strategy_name="s")[0]
        assert "tags:t1,t2" in (row.get("source") or "")
        assert "desc" in (row.get("source") or "")

    def test_list_filters_by_tags(self, store, ws):
        store.register_factor(
            ws, strategy_name="s", factor_name="a", formula="close", tags=["keep"]
        )
        store.register_factor(
            ws, strategy_name="s", factor_name="b", formula="open", tags=["drop"]
        )
        keep = store.list_factors(ws, strategy_name="s", tags=["keep"])
        assert [r["factor_name"] for r in keep] == ["a"]

    def test_list_without_tags_returns_all(self, store, ws):
        store.register_factor(ws, strategy_name="s", factor_name="a", formula="close", tags=["x"])
        store.register_factor(ws, strategy_name="s", factor_name="b", formula="open")
        assert len(store.list_factors(ws, strategy_name="s")) == 2

    def test_empty_tags_not_folded(self, store, ws):
        store.register_factor(ws, strategy_name="s", factor_name="a", formula="close", tags=[])
        row = store.list_factors(ws, strategy_name="s")[0]
        assert "tags:" not in (row.get("source") or "")
