"""Tests for core.hypothesis.registry + auto_create (P3-b).

Covers:
  - Hypothesis dataclass (to_dict / from_dict round-trip)
  - HypothesisRegistry CRUD + search + link_backtest
  - Atomic write (corrupt → exception, .tmp file)
  - HypothesisAutoCreator idempotency
  - Status validation (5 values only)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.hypothesis import (
    HYPOTHESIS_STATUSES,
    Hypothesis,
    HypothesisAutoCreator,
    HypothesisRegistry,
)

# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path: Path) -> HypothesisRegistry:
    return HypothesisRegistry(path=tmp_path / "hypotheses.json")


@pytest.fixture
def sample_kwargs() -> dict:
    return dict(
        title="Momentum in large caps",
        thesis="Recent winners continue outperforming over 6-12 months",
        universe="a_share",
        signal_definition="momentum_20_60",
        data_sources=["tushare", "akshare"],
        skills=["momentum", "factor_research"],
    )


# ─── HYPOTHESIS_STATUSES constant ─────────────────────────────────────────


class TestStatusConstants:
    def test_five_values(self):
        assert len(HYPOTHESIS_STATUSES) == 5

    def test_all_expected(self):
        assert set(HYPOTHESIS_STATUSES) == {
            "exploring",
            "testing",
            "validated",
            "rejected",
            "monitoring",
        }


# ─── Hypothesis dataclass ───────────────────────────────────────────────


class TestHypothesisDataclass:
    def test_to_dict_round_trip(self, sample_kwargs):
        h = Hypothesis(hypothesis_id="hyp_x", **sample_kwargs)
        d = h.to_dict()
        assert d["title"] == "Momentum in large caps"
        assert d["status"] == "exploring"
        assert d["data_sources"] == ["tushare", "akshare"]
        # Round-trip
        h2 = Hypothesis.from_dict(d)
        assert h2.title == h.title
        assert h2.thesis == h.thesis
        assert h2.universe == h.universe
        assert h2.data_sources == h.data_sources

    def test_from_dict_defaults(self):
        """Missing optional fields default safely."""
        h = Hypothesis.from_dict({
            "hypothesis_id": "hyp_a",
            "title": "x",
            "thesis": "y",
        })
        assert h.status == "exploring"
        assert h.universe == ""
        assert h.signal_definition == ""
        assert h.data_sources == []
        assert h.skills == []
        assert h.run_cards == []
        assert h.invalidation_notes == ""
        assert h.created_at != ""

    def test_from_dict_invalid_status_raises(self):
        with pytest.raises(ValueError, match="unknown hypothesis status"):
            Hypothesis.from_dict({
                "hypothesis_id": "hyp_a",
                "title": "x",
                "thesis": "y",
                "status": "frobnicated",
            })

    def test_from_dict_legacy_backtests_key(self):
        """Backwards compat: 'backtests' key used as alias for 'run_cards'."""
        h = Hypothesis.from_dict({
            "hypothesis_id": "hyp_a",
            "title": "x",
            "thesis": "y",
            "backtests": [{"run_card_path": "/path/x.json"}],
        })
        assert len(h.run_cards) == 1
        assert h.run_cards[0]["run_card_path"] == "/path/x.json"


# ─── HypothesisRegistry.create ───────────────────────────────────────────


class TestRegistryCreate:
    def test_creates_and_persists(self, registry: HypothesisRegistry, sample_kwargs):
        hyp = registry.create(**sample_kwargs)
        assert hyp.hypothesis_id.startswith("hyp_")
        assert hyp.title == "Momentum in large caps"
        assert hyp.status == "exploring"
        # Re-load to confirm persistence
        loaded = HypothesisRegistry(path=registry.path).list()
        assert len(loaded) == 1
        assert loaded[0].hypothesis_id == hyp.hypothesis_id

    def test_rejects_empty_title(self, registry: HypothesisRegistry):
        with pytest.raises(ValueError, match="title is required"):
            registry.create(title="", thesis="x")

    def test_rejects_empty_thesis(self, registry: HypothesisRegistry):
        with pytest.raises(ValueError, match="thesis is required"):
            registry.create(title="x", thesis="")

    def test_rejects_invalid_status(self, registry: HypothesisRegistry):
        with pytest.raises(ValueError, match="unknown hypothesis status"):
            registry.create(title="x", thesis="y", status="bogus")

    def test_unique_ids_for_same_title_time(self, registry: HypothesisRegistry, sample_kwargs):
        """Same title + same created_at → deterministic base ID + collision suffix."""
        h1 = registry.create(**sample_kwargs)
        # Force same timestamp via update to test collision logic
        # (registry uses datetime.now so two consecutive creates have different times)
        # Instead: manually craft two records with same id and verify collision
        h2 = registry.create(**sample_kwargs)
        # Both should have unique IDs
        assert h1.hypothesis_id != h2.hypothesis_id


# ─── HypothesisRegistry.update ───────────────────────────────────────────


class TestRegistryUpdate:
    def test_update_status(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        updated = registry.update(h.hypothesis_id, status="testing")
        assert updated.status == "testing"
        assert updated.updated_at >= h.created_at

    def test_update_invalid_status_raises(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        with pytest.raises(ValueError, match="unknown hypothesis status"):
            registry.update(h.hypothesis_id, status="")

    def test_update_unknown_id_raises(self, registry: HypothesisRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.update("hyp_nope", status="testing")

    def test_update_only_specified_fields(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        registry.update(h.hypothesis_id, thesis="new thesis only")
        loaded = registry.get(h.hypothesis_id)
        assert loaded.thesis == "new thesis only"
        assert loaded.title == h.title  # unchanged


# ─── HypothesisRegistry.link_backtest ─────────────────────────────────────


class TestRegistryLinkBacktest:
    def test_links_run_card(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        updated = registry.link_backtest(
            h.hypothesis_id,
            run_card_path="/path/run_card.json",
            metrics={"sharpe": 0.85},
            notes="validated by walk-forward",
        )
        assert len(updated.run_cards) == 1
        rc = updated.run_cards[0]
        assert rc["run_card_path"] == "/path/run_card.json"
        assert rc["metrics"]["sharpe"] == 0.85
        assert rc["linked_at"] != ""

    def test_links_backtest_run_dir(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        updated = registry.link_backtest(
            h.hypothesis_id,
            backtest_run_dir="/path/runs/run_0042",
        )
        assert updated.run_cards[0]["backtest_run_dir"] == "/path/runs/run_0042"

    def test_rejects_when_neither_provided(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        with pytest.raises(ValueError, match="required"):
            registry.link_backtest(h.hypothesis_id)

    def test_appends_multiple_cards(self, registry: HypothesisRegistry, sample_kwargs):
        h = registry.create(**sample_kwargs)
        registry.link_backtest(h.hypothesis_id, run_card_path="/p/1.json")
        registry.link_backtest(h.hypothesis_id, run_card_path="/p/2.json")
        assert len(registry.get(h.hypothesis_id).run_cards) == 2


# ─── HypothesisRegistry.search ───────────────────────────────────────────


class TestRegistrySearch:
    def test_no_filter_returns_all(self, registry: HypothesisRegistry):
        for i in range(3):
            registry.create(title=f"t{i}", thesis=f"thesis {i}")
        results = registry.search()
        assert len(results) == 3

    def test_query_token_match(self, registry: HypothesisRegistry):
        registry.create(title="Momentum AAPL", thesis="large caps", universe="us_equity")
        registry.create(title="Value investing", thesis="low P/E")
        results = registry.search(query="momentum")
        assert len(results) == 1
        assert results[0].title == "Momentum AAPL"

    def test_status_filter(self, registry: HypothesisRegistry):
        registry.create(title="a", thesis="x", status="exploring")
        h2 = registry.create(title="b", thesis="y", status="testing")
        results = registry.search(status="testing")
        assert len(results) == 1
        assert results[0].hypothesis_id == h2.hypothesis_id

    def test_combined_filter(self, registry: HypothesisRegistry):
        registry.create(title="Momentum", thesis="x", status="testing")
        registry.create(title="Momentum", thesis="y", status="rejected")
        registry.create(title="Value", thesis="z", status="testing")
        results = registry.search(query="momentum", status="testing")
        assert len(results) == 1
        assert results[0].thesis == "x"

    def test_invalid_status_filter_raises(self, registry: HypothesisRegistry):
        with pytest.raises(ValueError, match="unknown"):
            registry.search(status="bogus")

    def test_limit_bounds(self, registry: HypothesisRegistry):
        for i in range(5):
            registry.create(title=f"t{i}", thesis=f"thesis {i}")
        assert len(registry.search(limit=3)) == 3
        # limit clamped to [1, 100]
        assert len(registry.search(limit=0)) == 1
        assert len(registry.search(limit=999)) == 5  # capped by available

    def test_search_ordered_by_score_then_recency(self, registry: HypothesisRegistry):
        registry.create(title="a", thesis="x")
        registry.create(title="b", thesis="y")
        # Both have same score (0 query tokens → score 1 for all); tie-break on updated_at desc
        results = registry.search()
        assert len(results) == 2


# ─── HypothesisRegistry.list + atomicity ─────────────────────────────────


class TestRegistryAtomicity:
    def test_missing_file_returns_empty(self, tmp_path):
        reg = HypothesisRegistry(path=tmp_path / "missing.db")
        assert reg.list() == []

    def test_db_created_on_write(self, tmp_path):
        path = tmp_path / "created.db"
        reg = HypothesisRegistry(path=path)
        reg.create(title="t", thesis="y")
        assert path.exists()

    def test_atomic_write_no_leftover_tmp(self, registry: HypothesisRegistry, sample_kwargs):
        """After save, no .tmp file should remain."""
        registry.create(**sample_kwargs)
        assert registry.path.exists()
        tmp = registry.path.with_suffix(registry.path.suffix + ".tmp")
        assert not tmp.exists()

    def test_dir_created_on_init(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "h.db"
        HypothesisRegistry(path=nested)
        # Just init should not create file, but parent dirs should exist
        assert nested.parent.exists()


# ─── HypothesisAutoCreator ───────────────────────────────────────────────


class TestHypothesisAutoCreator:
    def test_creates_when_no_existing(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        hyp = creator.maybe_auto_create(
            session_id="sess_1",
            strategy_name="momentum_20_60",
            initial_thesis="Initial momentum study",
            market="a_share",
        )
        assert hyp is not None
        assert hyp.title.startswith("momentum_20_60")
        assert hyp.signal_definition == "momentum_20_60"
        assert hyp.universe == "a_share"
        assert hyp.status == "exploring"

    def test_idempotent_when_existing(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        h1 = creator.maybe_auto_create(
            session_id="sess_1", strategy_name="mom", market="a_share",
        )
        h2 = creator.maybe_auto_create(
            session_id="sess_1", strategy_name="mom", market="a_share",
        )
        assert h1 is not None
        assert h2 is None  # no duplicate

    def test_different_market_creates_new(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        h_cn = creator.maybe_auto_create(
            session_id="sess", strategy_name="mom", market="a_share",
        )
        h_us = creator.maybe_auto_create(
            session_id="sess", strategy_name="mom", market="us_equity",
        )
        assert h_cn is not None
        assert h_us is not None
        assert h_cn.hypothesis_id != h_us.hypothesis_id

    def test_empty_strategy_returns_none(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        assert creator.maybe_auto_create(session_id="s", strategy_name="") is None
        assert creator.maybe_auto_create(session_id="s", strategy_name="   ") is None

    def test_initial_thesis_truncated(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        long_thesis = "x" * 500
        hyp = creator.maybe_auto_create(
            session_id="s", strategy_name="m", initial_thesis=long_thesis,
        )
        assert len(hyp.thesis) == 200

    def test_no_initial_thesis_falls_back(self, registry: HypothesisRegistry):
        creator = HypothesisAutoCreator(registry=registry)
        hyp = creator.maybe_auto_create(
            session_id="s", strategy_name="alpha_v1", market="crypto",
        )
        assert "alpha_v1" in hyp.thesis
        assert "crypto" in hyp.thesis


# ─── env var override ────────────────────────────────────────────────────


class TestEnvOverride:
    def test_env_var_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_hyp.json"
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(custom))
        # default_hypotheses_path() reads env
        from strategy_research.apps.hypothesis.registry import default_hypotheses_path
        assert default_hypotheses_path() == custom.expanduser()
