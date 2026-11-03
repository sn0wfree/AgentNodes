"""Tests for P3-C Hypothesis enhancements: status machine + relationship graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.hypothesis import (
    VALID_TRANSITIONS,
    Hypothesis,
    HypothesisRegistry,
)


@pytest.fixture
def registry(tmp_path: Path) -> HypothesisRegistry:
    return HypothesisRegistry(path=tmp_path / "hypotheses_p3c.json")


# ============================================================
# VALID_TRANSITIONS
# ============================================================


class TestValidTransitions:
    def test_exploring_to_testing(self):
        assert "testing" in VALID_TRANSITIONS["exploring"]

    def test_exploring_to_rejected(self):
        assert "rejected" in VALID_TRANSITIONS["exploring"]

    def test_testing_to_validated(self):
        assert "validated" in VALID_TRANSITIONS["testing"]

    def test_testing_to_rejected(self):
        assert "rejected" in VALID_TRANSITIONS["testing"]

    def test_testing_to_exploring(self):
        assert "exploring" in VALID_TRANSITIONS["testing"]

    def test_validated_to_monitoring(self):
        assert "monitoring" in VALID_TRANSITIONS["validated"]

    def test_validated_to_testing_not_allowed(self):
        assert "testing" not in VALID_TRANSITIONS["validated"]

    def test_monitoring_to_testing(self):
        assert "testing" in VALID_TRANSITIONS["monitoring"]

    def test_monitoring_to_rejected(self):
        assert "rejected" in VALID_TRANSITIONS["monitoring"]

    def test_rejected_is_terminal(self):
        assert VALID_TRANSITIONS["rejected"] == set()


class TestUpdateEnforcesTransitions:
    def test_legal_exploring_to_testing(self, registry: HypothesisRegistry):
        h = registry.create(title="t1", thesis="thesis 1", status="exploring")
        updated = registry.update(h.hypothesis_id, status="testing")
        assert updated is not None
        assert updated.status == "testing"

    def test_illegal_exploring_to_validated_raises(self, registry: HypothesisRegistry):
        h = registry.create(title="t2", thesis="thesis 2", status="exploring")
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            registry.update(h.hypothesis_id, status="validated")

    def test_illegal_testing_to_monitoring_raises(self, registry: HypothesisRegistry):
        h = registry.create(title="t3", thesis="thesis 3", status="testing")
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            registry.update(h.hypothesis_id, status="monitoring")

    def test_illegal_validated_to_rejected_raises(self, registry: HypothesisRegistry):
        h = registry.create(
            title="t4", thesis="thesis 4", status="testing",
        )
        registry.update(h.hypothesis_id, status="validated")
        # validated can only go to monitoring
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            registry.update(h.hypothesis_id, status="rejected")

    def test_rejected_is_terminal(self, registry: HypothesisRegistry):
        h = registry.create(
            title="t5", thesis="thesis 5", status="testing",
        )
        registry.update(h.hypothesis_id, status="rejected")
        # rejected → anything should fail
        with pytest.raises(ValueError, match="invalid hypothesis transition"):
            registry.update(h.hypothesis_id, status="exploring")

    def test_same_status_update_allowed(self, registry: HypothesisRegistry):
        """Updating to the same status should not trigger transition validation."""
        h = registry.create(title="t6", thesis="thesis 6", status="exploring")
        # Same status — no transition needed
        updated = registry.update(h.hypothesis_id, status="exploring")
        assert updated is not None
        assert updated.status == "exploring"


# ============================================================
# derive() — relationship graph
# ============================================================


class TestDerive:
    def test_derive_creates_child(self, registry: HypothesisRegistry):
        parent = registry.create(title="parent", thesis="parent thesis")
        child = registry.derive(
            parent_id=parent.hypothesis_id,
            title="child",
            thesis="child thesis",
        )
        assert child.parent_hypothesis_id == parent.hypothesis_id
        assert child.status == "exploring"

    def test_derive_inherits_parent_universe(self, registry: HypothesisRegistry):
        parent = registry.create(
            title="parent", thesis="p",
            universe="a_share", data_sources=["tushare"],
        )
        child = registry.derive(
            parent_id=parent.hypothesis_id,
            title="child", thesis="c",
        )
        assert child.universe == "a_share"
        assert child.data_sources == ["tushare"]

    def test_derive_inherits_signal_definition(self, registry: HypothesisRegistry):
        parent = registry.create(
            title="p", thesis="p", signal_definition="ts_return(close, 20)",
        )
        child = registry.derive(
            parent_id=parent.hypothesis_id, title="c", thesis="c",
        )
        assert child.signal_definition == "ts_return(close, 20)"

    def test_derive_unknown_parent_raises(self, registry: HypothesisRegistry):
        with pytest.raises(KeyError, match="hypothesis not found"):
            registry.derive(
                parent_id="hyp_nonexistent",
                title="c", thesis="c",
            )


# ============================================================
# link() / unlink() — bidirectional related
# ============================================================


class TestLinkUnlink:
    def test_link_bidirectional(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        registry.link(h1.hypothesis_id, h2.hypothesis_id)
        reloaded_h1 = registry.get(h1.hypothesis_id)
        reloaded_h2 = registry.get(h2.hypothesis_id)
        assert reloaded_h1 is not None
        assert reloaded_h2 is not None
        assert h2.hypothesis_id in reloaded_h1.related_ids
        assert h1.hypothesis_id in reloaded_h2.related_ids

    def test_link_idempotent(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        registry.link(h1.hypothesis_id, h2.hypothesis_id)
        registry.link(h1.hypothesis_id, h2.hypothesis_id)
        reloaded = registry.get(h1.hypothesis_id)
        assert reloaded is not None
        assert reloaded.related_ids.count(h2.hypothesis_id) == 1

    def test_unlink_removes_bidirectional(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        registry.link(h1.hypothesis_id, h2.hypothesis_id)
        registry.unlink(h1.hypothesis_id, h2.hypothesis_id)
        reloaded_h1 = registry.get(h1.hypothesis_id)
        reloaded_h2 = registry.get(h2.hypothesis_id)
        assert reloaded_h1 is not None
        assert reloaded_h2 is not None
        assert h2.hypothesis_id not in reloaded_h1.related_ids
        assert h1.hypothesis_id not in reloaded_h2.related_ids


# ============================================================
# contradicts() — one-way
# ============================================================


class TestContradicts:
    def test_contradicts_appends(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        registry.contradicts(h1.hypothesis_id, h2.hypothesis_id, notes="mismatch")
        reloaded = registry.get(h1.hypothesis_id)
        assert reloaded is not None
        assert h2.hypothesis_id in reloaded.contradicts_ids

    def test_contradicts_appends_invalidation_notes(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        registry.contradicts(h1.hypothesis_id, h2.hypothesis_id, notes="incompatible")
        reloaded = registry.get(h1.hypothesis_id)
        assert reloaded is not None
        assert "incompatible" in reloaded.invalidation_notes
        assert h2.hypothesis_id in reloaded.invalidation_notes


# ============================================================
# link_goal()
# ============================================================


class TestLinkGoal:
    def test_link_goal_sets_field(self, registry: HypothesisRegistry):
        h = registry.create(title="h", thesis="t")
        registry.link_goal(h.hypothesis_id, "goal_abc")
        reloaded = registry.get(h.hypothesis_id)
        assert reloaded is not None
        assert reloaded.goal_id == "goal_abc"

    def test_link_goal_overwrites(self, registry: HypothesisRegistry):
        h = registry.create(title="h", thesis="t")
        registry.link_goal(h.hypothesis_id, "goal_1")
        registry.link_goal(h.hypothesis_id, "goal_2")
        reloaded = registry.get(h.hypothesis_id)
        assert reloaded is not None
        assert reloaded.goal_id == "goal_2"

    def test_list_by_goal(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        h3 = registry.create(title="h3", thesis="t3")
        registry.link_goal(h1.hypothesis_id, "goal_xyz")
        registry.link_goal(h2.hypothesis_id, "goal_xyz")
        registry.link_goal(h3.hypothesis_id, "goal_other")
        result = registry.list_by_goal("goal_xyz")
        assert len(result) == 2
        assert {h.hypothesis_id for h in result} == {h1.hypothesis_id, h2.hypothesis_id}


# ============================================================
# list_children()
# ============================================================


class TestListChildren:
    def test_list_children_returns_only_children(self, registry: HypothesisRegistry):
        parent = registry.create(title="p", thesis="t")
        child1 = registry.derive(parent_id=parent.hypothesis_id, title="c1", thesis="t1")
        child2 = registry.derive(parent_id=parent.hypothesis_id, title="c2", thesis="t2")
        unrelated = registry.create(title="u", thesis="u")
        children = registry.list_children(parent.hypothesis_id)
        assert len(children) == 2
        assert {c.hypothesis_id for c in children} == {child1.hypothesis_id, child2.hypothesis_id}
        assert unrelated.hypothesis_id not in {c.hypothesis_id for c in children}


# ============================================================
# list_contradictions()
# ============================================================


class TestListContradictions:
    def test_list_contradictions_returns_full_objects(self, registry: HypothesisRegistry):
        h1 = registry.create(title="h1", thesis="t1")
        h2 = registry.create(title="h2", thesis="t2")
        h3 = registry.create(title="h3", thesis="t3")
        registry.contradicts(h1.hypothesis_id, h2.hypothesis_id)
        registry.contradicts(h1.hypothesis_id, h3.hypothesis_id)
        result = registry.list_contradictions(h1.hypothesis_id)
        assert len(result) == 2
        assert {h.hypothesis_id for h in result} == {h2.hypothesis_id, h3.hypothesis_id}


# ============================================================
# Backward compat: from_dict handles missing new fields
# ============================================================


class TestFromDictBackwardCompat:
    def test_from_dict_legacy_no_relationship_fields(self):
        """Legacy JSON without new fields should parse with defaults."""
        legacy = {
            "hypothesis_id": "hyp_legacy",
            "title": "legacy",
            "thesis": "old thesis",
            "status": "exploring",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        hyp = Hypothesis.from_dict(legacy)
        assert hyp.parent_hypothesis_id is None
        assert hyp.related_ids == []
        assert hyp.contradicts_ids == []
        assert hyp.goal_id is None

    def test_from_dict_with_relationship_fields(self):
        full = {
            "hypothesis_id": "hyp_full",
            "title": "full",
            "thesis": "thesis",
            "status": "exploring",
            "parent_hypothesis_id": "hyp_parent",
            "related_ids": ["hyp_a", "hyp_b"],
            "contradicts_ids": ["hyp_x"],
            "goal_id": "goal_1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        hyp = Hypothesis.from_dict(full)
        assert hyp.parent_hypothesis_id == "hyp_parent"
        assert hyp.related_ids == ["hyp_a", "hyp_b"]
        assert hyp.contradicts_ids == ["hyp_x"]
        assert hyp.goal_id == "goal_1"


# ============================================================
# update() with relationship fields
# ============================================================


class TestUpdateRelationshipFields:
    def test_update_parent_hypothesis_id(self, registry: HypothesisRegistry):
        h = registry.create(title="h", thesis="t")
        updated = registry.update(h.hypothesis_id, parent_hypothesis_id="hyp_parent")
        assert updated is not None
        assert updated.parent_hypothesis_id == "hyp_parent"

    def test_update_clear_parent_hypothesis_id(self, registry: HypothesisRegistry):
        h = registry.create(
            title="h", thesis="t", parent_hypothesis_id="hyp_parent",
        )
        updated = registry.update(h.hypothesis_id, parent_hypothesis_id="")
        assert updated is not None
        assert updated.parent_hypothesis_id is None

    def test_update_goal_id(self, registry: HypothesisRegistry):
        h = registry.create(title="h", thesis="t")
        updated = registry.update(h.hypothesis_id, goal_id="goal_xyz")
        assert updated is not None
        assert updated.goal_id == "goal_xyz"
