"""M5: HypothesisRegistry SQLite-backing tests (design §14, P2).

P2: the legacy JSON-file backend was removed — HypothesisRegistry is a
thin facade over HypothesisStore. These tests pin the registry's public
behavior (KeyError semantics, list ordering, search scoring — OR token
semantics, relationship graph behavior).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from strategy_research.apps.hypothesis import Hypothesis, HypothesisRegistry


@pytest.fixture
def sql_reg(tmp_path: Path) -> HypothesisRegistry:
    return HypothesisRegistry(db_path=tmp_path / "hyps.db")


# ─── CRUD ───────────────────────────────────────────────────────────


class TestCrudConsistency:
    def test_create_and_get(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="alpha momentum", thesis="momentum works")
        assert isinstance(h, Hypothesis)
        assert h.hypothesis_id.startswith("hyp_")
        assert sql_reg.get(h.hypothesis_id) is not None
        assert sql_reg.get("hyp_nope") is None

    def test_validation_matches_json(self, sql_reg: HypothesisRegistry):
        with pytest.raises(ValueError, match="title is required"):
            sql_reg.create(title="  ", thesis="t")
        with pytest.raises(ValueError, match="thesis is required"):
            sql_reg.create(title="t", thesis="")
        with pytest.raises(ValueError, match="unknown hypothesis status"):
            sql_reg.create(title="t", thesis="y", status="bogus")

    def test_update_matches_json(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="t", thesis="x")
        updated = sql_reg.update(h.hypothesis_id, thesis="y", status="testing")
        assert updated.thesis == "y"
        assert updated.status == "testing"
        # exploring → validated is illegal in both modes
        h2 = sql_reg.create(title="t2", thesis="x2")
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            sql_reg.update(h2.hypothesis_id, status="validated")
    def test_update_unknown_id_raises_keyerror(self, sql_reg: HypothesisRegistry):
        with pytest.raises(KeyError, match="not found"):
            sql_reg.update("hyp_nope", status="testing")

    def test_list_full_and_created_at_ordered(self, sql_reg: HypothesisRegistry):
        h1 = sql_reg.create(title="first", thesis="t1")
        h2 = sql_reg.create(title="second", thesis="t2")
        all_hyps = sql_reg.list()
        assert len(all_hyps) == 2
        # JSON mode orders by created_at asc → ids in creation order
        assert all_hyps[0].hypothesis_id == h1.hypothesis_id
        assert all_hyps[1].hypothesis_id == h2.hypothesis_id

    def test_update_unknown_fields_only(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="t", thesis="x")
        sql_reg.update(h.hypothesis_id, thesis="new")
        loaded = sql_reg.get(h.hypothesis_id)
        assert loaded.thesis == "new"
        assert loaded.title == "t"


# ─── search semantics (OR token scoring, shared with JSON mode) ─────


class TestSearchConsistency:
    def test_query_token_or_semantics(self, sql_reg: HypothesisRegistry):
        """One token match suffices (JSON scoring semantics), incl. CJK."""
        sql_reg.create(title="Momentum AAPL", thesis="large caps")
        sql_reg.create(title="Value investing", thesis="low P/E")
        assert len(sql_reg.search(query="momentum")) == 1
        assert len(sql_reg.search(query="momentum value")) == 2  # OR semantics

    def test_cjk_search(self, sql_reg: HypothesisRegistry):
        sql_reg.create(title="动量因子研究", thesis="量价背离信号")
        sql_reg.create(title="价值投资", thesis="低市盈率")
        hits = sql_reg.search(query="动量")
        assert len(hits) == 1
        assert hits[0].title == "动量因子研究"

    def test_status_filter(self, sql_reg: HypothesisRegistry):
        h1 = sql_reg.create(title="a", thesis="x", status="exploring")
        sql_reg.create(title="b", thesis="y", status="testing")
        results = sql_reg.search(status="testing")
        assert len(results) == 1
        assert results[0].hypothesis_id != h1.hypothesis_id

    def test_combined_filter(self, sql_reg: HypothesisRegistry):
        sql_reg.create(title="Momentum", thesis="x", status="testing")
        sql_reg.create(title="Momentum", thesis="y", status="rejected")
        sql_reg.create(title="Value", thesis="z", status="testing")
        results = sql_reg.search(query="momentum", status="testing")
        assert len(results) == 1
        assert results[0].thesis == "x"

    def test_invalid_status_raises(self, sql_reg: HypothesisRegistry):
        with pytest.raises(ValueError, match="unknown"):
            sql_reg.search(status="bogus")

    def test_limit_bounds(self, sql_reg: HypothesisRegistry):
        for i in range(5):
            sql_reg.create(title=f"t{i}", thesis=f"thesis {i}")
        assert len(sql_reg.search(limit=3)) == 3
        assert len(sql_reg.search(limit=0)) == 1
        assert len(sql_reg.search(limit=999)) == 5


# ─── link_backtest ──────────────────────────────────────────────────


class TestLinkBacktestConsistency:
    def test_link_backtest(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="t", thesis="x")
        updated = sql_reg.link_backtest(
            h.hypothesis_id, run_card_path="/p/card.json",
            metrics={"sharpe": 0.85}, notes="wf",
        )
        assert len(updated.run_cards) == 1
        assert updated.run_cards[0]["metrics"]["sharpe"] == 0.85
        # appends
        sql_reg.link_backtest(h.hypothesis_id, backtest_run_dir="/r/1")
        assert len(sql_reg.get(h.hypothesis_id).run_cards) == 2

    def test_requires_path(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="t", thesis="x")
        with pytest.raises(ValueError, match="required"):
            sql_reg.link_backtest(h.hypothesis_id)

    def test_unknown_id_raises_keyerror(self, sql_reg: HypothesisRegistry):
        with pytest.raises(KeyError, match="not found"):
            sql_reg.link_backtest("hyp_nope", run_card_path="/p/x")


# ─── relationship graph ─────────────────────────────────────────────


class TestGraphConsistency:
    def test_derive_inherits(self, sql_reg: HypothesisRegistry):
        parent = sql_reg.create(
            title="p", thesis="pt", universe="a_share",
            data_sources=["tushare"], skills=["momentum"],
            signal_definition="mom_20_60",
        )
        child = sql_reg.derive(
            parent_id=parent.hypothesis_id, title="c", thesis="ct",
        )
        assert child.parent_hypothesis_id == parent.hypothesis_id
        assert child.universe == "a_share"
        assert child.data_sources == ["tushare"]
        assert child.signal_definition == "mom_20_60"

    def test_derive_unknown_parent_raises(self, sql_reg: HypothesisRegistry):
        with pytest.raises(KeyError, match="parent"):
            sql_reg.derive(parent_id="hyp_nope", title="c", thesis="t")

    def test_link_unlink_bidirectional(self, sql_reg: HypothesisRegistry):
        h1 = sql_reg.create(title="h1", thesis="t1")
        h2 = sql_reg.create(title="h2", thesis="t2")
        sql_reg.link(h1.hypothesis_id, h2.hypothesis_id)
        reloaded = sql_reg.get(h1.hypothesis_id)
        assert h2.hypothesis_id in reloaded.related_ids
        assert h1.hypothesis_id in sql_reg.get(h2.hypothesis_id).related_ids
        # idempotent
        sql_reg.link(h1.hypothesis_id, h2.hypothesis_id)
        assert sql_reg.get(h1.hypothesis_id).related_ids.count(h2.hypothesis_id) == 1
        sql_reg.unlink(h1.hypothesis_id, h2.hypothesis_id)
        assert h2.hypothesis_id not in sql_reg.get(h1.hypothesis_id).related_ids

    def test_link_unknown_raises_keyerror(self, sql_reg: HypothesisRegistry):
        h1 = sql_reg.create(title="h1", thesis="t1")
        with pytest.raises(KeyError, match="not found"):
            sql_reg.link(h1.hypothesis_id, "hyp_nope")

    def test_contradicts_and_list(self, sql_reg: HypothesisRegistry):
        h1 = sql_reg.create(title="h1", thesis="t1")
        h2 = sql_reg.create(title="h2", thesis="t2")
        sql_reg.contradicts(h1.hypothesis_id, h2.hypothesis_id, notes="opposite")
        reloaded = sql_reg.get(h1.hypothesis_id)
        assert h2.hypothesis_id in reloaded.contradicts_ids
        assert "opposite" in reloaded.invalidation_notes
        listed = sql_reg.list_contradictions(h1.hypothesis_id)
        assert [h.hypothesis_id for h in listed] == [h2.hypothesis_id]

    def test_link_goal_and_list_by_goal(self, sql_reg: HypothesisRegistry):
        h = sql_reg.create(title="h", thesis="t")
        sql_reg.link_goal(h.hypothesis_id, "goal_abc")
        assert sql_reg.get(h.hypothesis_id).goal_id == "goal_abc"
        listed = sql_reg.list_by_goal("goal_abc")
        assert len(listed) == 1
        assert listed[0].hypothesis_id == h.hypothesis_id

    def test_list_children(self, sql_reg: HypothesisRegistry):
        parent = sql_reg.create(title="p", thesis="t")
        c1 = sql_reg.derive(parent_id=parent.hypothesis_id, title="c1", thesis="t")
        c2 = sql_reg.derive(parent_id=parent.hypothesis_id, title="c2", thesis="t")
        sql_reg.create(title="u", thesis="u")
        children = sql_reg.list_children(parent.hypothesis_id)
        assert {c.hypothesis_id for c in children} == {c1.hypothesis_id, c2.hypothesis_id}


# ─── storage-path resolution ───────────────────────────────────


class TestStoragePathResolution:
    def test_db_env_var(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(
            "QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH",
            str(tmp_path / "env.db"),
        )
        reg = HypothesisRegistry()
        assert reg._store is not None
        h = reg.create(title="env", thesis="t")
        assert reg.get(h.hypothesis_id) is not None
        assert (tmp_path / "env.db").exists()

    def test_legacy_env_alias(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(
            "QUANTNODES_RESEARCH_HYPOTHESES_PATH",
            str(tmp_path / "legacy.db"),
        )
        reg = HypothesisRegistry()
        assert reg.path == tmp_path / "legacy.db"
        reg.create(title="l", thesis="t")
        assert (tmp_path / "legacy.db").exists()

    def test_create_app_still_works(self, monkeypatch):
        monkeypatch.delenv("HYPOTHESIS_USE_SQLITE", raising=False)
        from strategy_research.api.app import create_app
        app = create_app()
        assert app is not None


# ─── concurrency through the registry (SQLite branch) ───────────────


class TestRegistryConcurrency:
    def test_parallel_create_no_losses(self, tmp_path: Path):
        reg = HypothesisRegistry(db_path=tmp_path / "conc.db")
        n_workers = 20
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(n_workers)

        def _create(idx: int):
            barrier.wait(timeout=5)
            try:
                hyp = reg.create(title=f"conc_{idx}", thesis=f"thesis {idx}")
                results.append(hyp.hypothesis_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_create, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        assert len(results) == n_workers
        assert len(set(results)) == n_workers
        # all persisted
        assert len(reg.list()) == n_workers
