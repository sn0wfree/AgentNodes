"""Tests for P3-E HypothesisStore (SQLite + FTS5) and JSON→SQLite migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.apps.hypothesis import Hypothesis, HypothesisStore


@pytest.fixture
def store(tmp_path: Path) -> HypothesisStore:
    return HypothesisStore(db_path=tmp_path / "hypotheses.db")


# ============================================================
# CRUD
# ============================================================


class TestCRUD:
    def test_create_returns_hypothesis(self, store: HypothesisStore):
        h = store.create(title="alpha momentum", thesis="momentum works")
        assert isinstance(h, Hypothesis)
        assert h.hypothesis_id.startswith("hyp_")
        assert h.title == "alpha momentum"
        assert h.thesis == "momentum works"
        assert h.status == "exploring"

    def test_get_returns_created(self, store: HypothesisStore):
        h = store.create(title="t", thesis="thesis")
        retrieved = store.get(h.hypothesis_id)
        assert retrieved is not None
        assert retrieved.hypothesis_id == h.hypothesis_id
        assert retrieved.title == h.title

    def test_get_unknown_returns_none(self, store: HypothesisStore):
        assert store.get("hyp_nonexistent") is None

    def test_create_title_thesis_required(self, store: HypothesisStore):
        with pytest.raises(ValueError, match="title is required"):
            store.create(title="  ", thesis="t")
        with pytest.raises(ValueError, match="thesis is required"):
            store.create(title="t", thesis="")

    def test_list_default(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        results = store.list()
        assert len(results) == 2
        ids = {h.hypothesis_id for h in results}
        assert ids == {h1.hypothesis_id, h2.hypothesis_id}

    def test_list_filter_by_status(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1", status="exploring")
        h2 = store.create(title="h2", thesis="t2", status="exploring")
        h2 = store.update(h2.hypothesis_id, status="testing")
        assert h2 is not None

        exploring = store.list(status="exploring")
        testing = store.list(status="testing")
        assert len(exploring) == 1
        assert exploring[0].hypothesis_id == h1.hypothesis_id
        assert len(testing) == 1
        assert testing[0].hypothesis_id == h2.hypothesis_id

    def test_list_filter_by_goal(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1", goal_id="goal_abc")
        store.create(title='h2', thesis='t2', goal_id='goal_xyz')
        results = store.list(goal_id="goal_abc")
        assert len(results) == 1
        assert results[0].hypothesis_id == h1.hypothesis_id

    def test_list_filter_by_parent(self, store: HypothesisStore):
        parent = store.create(title="p", thesis="t")
        store.derive(parent_id=parent.hypothesis_id, title='c1', thesis='t')
        store.derive(parent_id=parent.hypothesis_id, title='c2', thesis='t')
        unrelated = store.create(title="u", thesis="u")
        children = store.list(parent_id=parent.hypothesis_id)
        assert len(children) == 2
        assert unrelated.hypothesis_id not in {c.hypothesis_id for c in children}

    def test_update_modifies_fields(self, store: HypothesisStore):
        h = store.create(title="t", thesis="orig")
        updated = store.update(
            h.hypothesis_id,
            title="new title",
            thesis="new thesis",
            universe="a_share",
        )
        assert updated is not None
        assert updated.title == "new title"
        assert updated.thesis == "new thesis"
        assert updated.universe == "a_share"

    def test_update_unknown_returns_none(self, store: HypothesisStore):
        result = store.update("hyp_nonexistent", title="new")
        assert result is None

    def test_update_enforces_transitions(self, store: HypothesisStore):
        h = store.create(title="t", thesis="thesis")
        # exploring → validated is illegal
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            store.update(h.hypothesis_id, status="validated")

    def test_delete_is_not_supported(self, store: HypothesisStore):
        """Store has no delete — relies on status transitions."""
        h = store.create(title="t", thesis="thesis")
        # Verify hypothesis still exists
        assert store.get(h.hypothesis_id) is not None


# ============================================================
# link_backtest
# ============================================================


class TestLinkBacktest:
    def test_link_backtest_appends(self, store: HypothesisStore):
        h = store.create(title="t", thesis="thesis")
        updated = store.link_backtest(
            h.hypothesis_id,
            run_card_path="/path/to/card.json",
            metrics={"sharpe": 1.5},
        )
        assert updated is not None
        assert len(updated.run_cards) == 1
        assert updated.run_cards[0]["metrics"]["sharpe"] == 1.5

    def test_link_backtest_requires_path(self, store: HypothesisStore):
        h = store.create(title="t", thesis="thesis")
        with pytest.raises(ValueError, match="run_card_path or backtest_run_dir is required"):
            store.link_backtest(h.hypothesis_id)


# ============================================================
# FTS5 search
# ============================================================


class TestSearch:
    def test_search_finds_by_title_token(self, store: HypothesisStore):
        store.create(title="alpha momentum strategy", thesis="t1")
        store.create(title="value investing", thesis="t2")
        results = store.search(query="momentum")
        assert len(results) == 1
        assert "momentum" in results[0].title.lower()

    def test_search_finds_by_thesis_token(self, store: HypothesisStore):
        store.create(title="h1", thesis="momentum factor returns are persistent")
        store.create(title="h2", thesis="value factor outperforms growth")
        results = store.search(query="momentum")
        assert len(results) == 1

    def test_search_no_query_returns_all(self, store: HypothesisStore):
        store.create(title="h1", thesis="t1")
        store.create(title="h2", thesis="t2")
        results = store.search(query="")
        assert len(results) == 2

    def test_search_with_status_filter(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        store.create(title="h2", thesis="t2")
        store.update(h1.hypothesis_id, status="testing")
        results = store.search(query="h", status="testing")
        assert len(results) == 1
        assert results[0].hypothesis_id == h1.hypothesis_id

    def test_search_limit(self, store: HypothesisStore):
        for i in range(10):
            store.create(title=f"alpha {i}", thesis=f"thesis {i}")
        results = store.search(query="alpha", limit=3)
        assert len(results) == 3


# ============================================================
# Relationship graph operations
# ============================================================


class TestRelationships:
    def test_derive_inherits_parents_data_sources(self, store: HypothesisStore):
        parent = store.create(
            title="p", thesis="pt",
            data_sources=["tushare", "akshare"],
        )
        child = store.derive(parent_id=parent.hypothesis_id, title="c", thesis="ct")
        assert child.data_sources == ["tushare", "akshare"]

    def test_derive_inherits_signal_definition(self, store: HypothesisStore):
        parent = store.create(
            title="p", thesis="pt",
            signal_definition="ts_return(close, 20)",
        )
        child = store.derive(
            parent_id=parent.hypothesis_id, title="c", thesis="ct",
        )
        assert child.signal_definition == "ts_return(close, 20)"

    def test_link_bidirectional(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        store.link(h1.hypothesis_id, h2.hypothesis_id)
        reloaded_h1 = store.get(h1.hypothesis_id)
        reloaded_h2 = store.get(h2.hypothesis_id)
        assert reloaded_h1 is not None
        assert reloaded_h2 is not None
        assert h2.hypothesis_id in reloaded_h1.related_ids
        assert h1.hypothesis_id in reloaded_h2.related_ids

    def test_link_idempotent(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        store.link(h1.hypothesis_id, h2.hypothesis_id)
        store.link(h1.hypothesis_id, h2.hypothesis_id)
        reloaded = store.get(h1.hypothesis_id)
        assert reloaded is not None
        assert reloaded.related_ids.count(h2.hypothesis_id) == 1

    def test_unlink_removes_bidirectional(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        store.link(h1.hypothesis_id, h2.hypothesis_id)
        store.unlink(h1.hypothesis_id, h2.hypothesis_id)
        reloaded_h1 = store.get(h1.hypothesis_id)
        reloaded_h2 = store.get(h2.hypothesis_id)
        assert reloaded_h1 is not None
        assert reloaded_h2 is not None
        assert h2.hypothesis_id not in reloaded_h1.related_ids
        assert h1.hypothesis_id not in reloaded_h2.related_ids

    def test_contradicts_appends(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        store.contradicts(h1.hypothesis_id, h2.hypothesis_id, notes="test")
        reloaded = store.get(h1.hypothesis_id)
        assert reloaded is not None
        assert h2.hypothesis_id in reloaded.contradicts_ids
        assert "test" in reloaded.invalidation_notes

    def test_link_goal(self, store: HypothesisStore):
        h = store.create(title="h", thesis="t")
        result = store.link_goal(h.hypothesis_id, "goal_abc")
        assert result is not None
        assert result.goal_id == "goal_abc"
        # list_by_goal returns it
        listed = store.list_by_goal("goal_abc")
        assert len(listed) == 1
        assert listed[0].hypothesis_id == h.hypothesis_id

    def test_list_children(self, store: HypothesisStore):
        parent = store.create(title="p", thesis="t")
        c1 = store.derive(parent_id=parent.hypothesis_id, title="c1", thesis="t")
        c2 = store.derive(parent_id=parent.hypothesis_id, title="c2", thesis="t")
        children = store.list_children(parent.hypothesis_id)
        assert len(children) == 2
        assert {c.hypothesis_id for c in children} == {c1.hypothesis_id, c2.hypothesis_id}

    def test_list_contradictions(self, store: HypothesisStore):
        h1 = store.create(title="h1", thesis="t1")
        h2 = store.create(title="h2", thesis="t2")
        store.contradicts(h1.hypothesis_id, h2.hypothesis_id)
        result = store.list_contradictions(h1.hypothesis_id)
        assert len(result) == 1
        assert result[0].hypothesis_id == h2.hypothesis_id


# ============================================================
# No JSON migration (v2 design §14.2: JSON left in place, never read)
# ============================================================


class TestNoJsonMigration:
    def test_json_file_not_imported(self, tmp_path: Path):
        """Legacy JSON next to the DB is left untouched, not imported."""
        json_path = tmp_path / "hypotheses.json"
        legacy = [
            {
                "hypothesis_id": "hyp_legacy_1",
                "title": "Legacy 1",
                "thesis": "Legacy thesis 1",
                "status": "exploring",
                "universe": "a_share",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        ]
        json_path.write_text(json.dumps(legacy), encoding="utf-8")

        db_path = tmp_path / "hypotheses.db"
        store = HypothesisStore(db_path=db_path)

        # JSON content is NOT imported into SQLite
        assert store.get("hyp_legacy_1") is None
        assert len(store.list()) == 0
        # JSON file is preserved in place (no rename, no .bak)
        assert json_path.exists()
        assert not (tmp_path / "hypotheses.json.bak").exists()

        store.close()

    def test_malformed_json_next_to_db_is_ignored(self, tmp_path: Path):
        """A malformed legacy JSON must not break SQLite init."""
        json_path = tmp_path / "hypotheses.json"
        json_path.write_text("{invalid json", encoding="utf-8")

        db_path = tmp_path / "hypotheses.db"
        store = HypothesisStore(db_path=db_path)
        assert len(store.list()) == 0
        store.close()


# ============================================================
# HypothesisRegistry SQLite mode (db_path)
# ============================================================


class TestRegistrySqliteMode:
    def test_registry_with_db_path_uses_sqlite(self, tmp_path: Path):
        """Passing db_path to HypothesisRegistry enables SQLite mode."""
        from strategy_research.apps.hypothesis import HypothesisRegistry
        db_path = tmp_path / "reg.db"
        reg = HypothesisRegistry(db_path=db_path)
        h = reg.create(title="via registry", thesis="t")
        assert h.hypothesis_id.startswith("hyp_")
        # Verify it's in SQLite
        assert reg.get(h.hypothesis_id) is not None

    def test_registry_default_still_uses_json(self, tmp_path: Path):
        """Without db_path, registry uses JSON file storage."""
        from strategy_research.apps.hypothesis import HypothesisRegistry
        json_path = tmp_path / "fallback.json"
        reg = HypothesisRegistry(path=json_path)
        reg.create(title='via json', thesis='t')
        assert json_path.exists()  # JSON file should be created


# ============================================================
# Persistence across reopens
# ============================================================


class TestPersistence:
    def test_data_persists_across_reopens(self, tmp_path: Path):
        db_path = tmp_path / "persist.db"
        store1 = HypothesisStore(db_path=db_path)
        h = store1.create(title="persistent", thesis="t")
        store1.close()

        store2 = HypothesisStore(db_path=db_path)
        retrieved = store2.get(h.hypothesis_id)
        assert retrieved is not None
        assert retrieved.title == "persistent"
        store2.close()

    def test_relationships_persist(self, tmp_path: Path):
        db_path = tmp_path / "persist_rel.db"
        store1 = HypothesisStore(db_path=db_path)
        h1 = store1.create(title="h1", thesis="t1")
        h2 = store1.create(title="h2", thesis="t2")
        store1.link(h1.hypothesis_id, h2.hypothesis_id)
        store1.close()

        store2 = HypothesisStore(db_path=db_path)
        reloaded = store2.get(h1.hypothesis_id)
        assert reloaded is not None
        assert h2.hypothesis_id in reloaded.related_ids
        store2.close()


# ============================================================
# close()
# ============================================================


class TestClose:
    def test_close_is_safe(self, store: HypothesisStore):
        store.close()
        # Double close should not raise
        store.close()
