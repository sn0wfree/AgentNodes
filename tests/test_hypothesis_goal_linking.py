"""Tests for Hypothesis↔Goal linking and HypothesisStore JSON migration edge cases.

Focuses on:
- HypothesisRegistry.link_goal / list_by_goal (JSON mode)
- HypothesisStore.link_goal / list_by_goal (SQLite mode)
- Migration robustness: corrupt, non-list, mixed valid/invalid, duplicate IDs, missing fields
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.apps.hypothesis import HypothesisRegistry, HypothesisStore

# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def hyp_store(tmp_path: Path) -> HypothesisStore:
    return HypothesisStore(db_path=tmp_path / "hyp.db")


@pytest.fixture
def goal_store(tmp_path: Path) -> GoalStore:
    return GoalStore(db_path=tmp_path / "goal.db")


@pytest.fixture
def json_registry(tmp_path: Path) -> HypothesisRegistry:
    return HypothesisRegistry(path=tmp_path / "hyps.json")


# ============================================================
# HypothesisRegistry.link_goal (JSON mode)
# ============================================================


class TestRegistryLinkGoalJson:
    def test_link_sets_goal_id(self, json_registry):
        hyp = json_registry.create(title="t", thesis="th")
        json_registry.link_goal(hyp.hypothesis_id, "goal_abc")
        reloaded = json_registry.get(hyp.hypothesis_id)
        assert reloaded.goal_id == "goal_abc"

    def test_link_persists(self, tmp_path, json_registry):
        hyp = json_registry.create(title="t", thesis="th")
        json_registry.link_goal(hyp.hypothesis_id, "goal_xyz")

        # Fresh registry from same path
        reloaded = HypothesisRegistry(path=tmp_path / "hyps.json")
        assert reloaded.get(hyp.hypothesis_id).goal_id == "goal_xyz"

    def test_link_unknown_hypothesis_raises(self, json_registry):
        with pytest.raises(KeyError):
            json_registry.link_goal("hyp_nope", "goal_abc")

    def test_relink_overwrites(self, json_registry):
        hyp = json_registry.create(title="t", thesis="th")
        json_registry.link_goal(hyp.hypothesis_id, "goal_first")
        json_registry.link_goal(hyp.hypothesis_id, "goal_second")
        assert json_registry.get(hyp.hypothesis_id).goal_id == "goal_second"


# ============================================================
# HypothesisRegistry.list_by_goal (JSON mode)
# ============================================================


class TestRegistryListByGoalJson:
    def test_empty_goal_returns_empty(self, json_registry):
        assert json_registry.list_by_goal("goal_nothing") == []

    def test_filters_by_goal(self, json_registry):
        h1 = json_registry.create(title="h1", thesis="t")
        h2 = json_registry.create(title="h2", thesis="t")
        h3 = json_registry.create(title="h3", thesis="t")
        json_registry.link_goal(h1.hypothesis_id, "goal_x")
        json_registry.link_goal(h2.hypothesis_id, "goal_y")
        json_registry.link_goal(h3.hypothesis_id, "goal_x")

        x = {h.hypothesis_id for h in json_registry.list_by_goal("goal_x")}
        assert x == {h1.hypothesis_id, h3.hypothesis_id}
        assert json_registry.list_by_goal("goal_y")[0].hypothesis_id == h2.hypothesis_id


# ============================================================
# HypothesisStore.link_goal / list_by_goal (SQLite mode)
# ============================================================


class TestStoreLinkGoalSqlite:
    def test_link_sets_goal_id(self, hyp_store):
        h = hyp_store.create(title="t", thesis="th")
        hyp_store.link_goal(h.hypothesis_id, "goal_db")
        assert hyp_store.get(h.hypothesis_id).goal_id == "goal_db"

    def test_link_unknown_returns_none(self, hyp_store):
        """SQLite store.update() raises KeyError — unlink_goal swallows and returns None."""
        result = hyp_store.link_goal("hyp_nope", "goal_abc")
        assert result is None

    def test_list_by_goal_filters(self, hyp_store):
        h1 = hyp_store.create(title="h1", thesis="t")
        h2 = hyp_store.create(title="h2", thesis="t")
        hyp_store.link_goal(h1.hypothesis_id, "goal_a")
        hyp_store.link_goal(h2.hypothesis_id, "goal_b")

        ids_a = {h.hypothesis_id for h in hyp_store.list_by_goal("goal_a")}
        assert ids_a == {h1.hypothesis_id}

    def test_list_by_goal_empty(self, hyp_store):
        hyp_store.create(title="h", thesis="t")
        assert hyp_store.list_by_goal("goal_none") == []


# ============================================================
# Goal↔Hypothesis round-trip
# ============================================================


class TestGoalHypothesisRoundTrip:
    def test_link_and_query(self, hyp_store, goal_store):
        """Link a hypothesis to a real goal, then verify goal_id on hypothesis."""
        goal = goal_store.replace_goal(
            session_id="s1", objective="o", criteria=["c"],
        )
        h = hyp_store.create(title="linked", thesis="t")
        hyp_store.link_goal(h.hypothesis_id, goal.goal_id)

        # Hypothesis side
        assert hyp_store.get(h.hypothesis_id).goal_id == goal.goal_id

        # list_by_goal returns it
        linked = hyp_store.list_by_goal(goal.goal_id)
        assert linked[0].hypothesis_id == h.hypothesis_id

    def test_list_by_goal_only_returns_linked(self, hyp_store, goal_store):
        g1 = goal_store.replace_goal(session_id="s1", objective="g1", criteria=["c"])
        g2 = goal_store.replace_goal(session_id="s2", objective="g2", criteria=["c"])
        h_linked = hyp_store.create(title="linked", thesis="t")
        h_unlinked = hyp_store.create(title="unlinked", thesis="t")
        hyp_store.link_goal(h_linked.hypothesis_id, g1.goal_id)

        g1_hyps = {h.hypothesis_id for h in hyp_store.list_by_goal(g1.goal_id)}
        g2_hyps = {h.hypothesis_id for h in hyp_store.list_by_goal(g2.goal_id)}
        assert h_linked.hypothesis_id in g1_hyps
        assert h_unlinked.hypothesis_id not in g1_hyps
        assert h_unlinked.hypothesis_id not in g2_hyps


# ============================================================
# No JSON migration (v2 design §14.2: JSON left in place, never read)
# ============================================================


class TestNoMigrationEdgeCases:
    def test_empty_list_not_imported(self, tmp_path):
        """Legacy JSON next to the DB is never imported, even when empty."""
        json_path = tmp_path / "hypotheses.json"
        json_path.write_text("[]", encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert len(store.list()) == 0
        assert json_path.exists()
        assert not (tmp_path / "hypotheses.json.bak").exists()
        store.close()

    def test_top_level_object_ignored(self, tmp_path):
        """JSON containing a dict (not list) → ignored, not crashed."""
        json_path = tmp_path / "hypotheses.json"
        json_path.write_text('{"key": "value"}', encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert len(store.list()) == 0
        store.close()

    def test_top_level_string_ignored(self, tmp_path):
        """JSON containing a string at top level → ignored."""
        json_path = tmp_path / "hypotheses.json"
        json_path.write_text('"just a string"', encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert len(store.list()) == 0
        store.close()

    def test_valid_records_not_imported(self, tmp_path):
        """Valid records in the legacy JSON are NOT imported (design §14.2)."""
        json_path = tmp_path / "hypotheses.json"
        data = [
            {"hypothesis_id": "hyp_valid1", "title": "OK1", "thesis": "t",
             "status": "exploring", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"},
            "this is not a dict",
            42,
        ]
        json_path.write_text(json.dumps(data), encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert store.list() == []
        assert json_path.exists()  # left in place, untouched
        store.close()

    def test_duplicate_ids_irrelevant(self, tmp_path):
        """No import means duplicate IDs in the JSON have no effect."""
        json_path = tmp_path / "hypotheses.json"
        data = [
            {"hypothesis_id": "hyp_dup", "title": "First", "thesis": "t1",
             "status": "exploring", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"},
            {"hypothesis_id": "hyp_dup", "title": "Second", "thesis": "t2",
             "status": "testing", "created_at": "2026-01-02T00:00:00Z",
             "updated_at": "2026-01-02T00:00:00Z"},
        ]
        json_path.write_text(json.dumps(data), encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert store.get("hyp_dup") is None
        store.close()

    def test_fts_index_untouched(self, tmp_path):
        """No migration → FTS search only sees SQLite-created records."""
        json_path = tmp_path / "hypotheses.json"
        data = [
            {"hypothesis_id": "hyp_fts1", "title": "Momentum angle",
             "thesis": "The market has momentum", "status": "exploring",
             "universe": "a_share",
             "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"},
        ]
        json_path.write_text(json.dumps(data), encoding="utf-8")

        store = HypothesisStore(db_path=tmp_path / "h.db")
        assert store.search(query="momentum") == []
        store.close()

    def test_json_untouched_across_reinit(self, tmp_path):
        """Subsequent inits never touch the legacy JSON file."""
        json_path = tmp_path / "hypotheses.json"
        json_path.write_text('[]', encoding="utf-8")
        before = json_path.read_text(encoding="utf-8")

        s1 = HypothesisStore(db_path=tmp_path / "h.db")
        s1.create(title="via sqlite", thesis="t")
        s1.close()

        s2 = HypothesisStore(db_path=tmp_path / "h.db")
        assert len(s2.list()) == 1  # only the SQLite-created record
        assert json_path.read_text(encoding="utf-8") == before
        s2.close()
