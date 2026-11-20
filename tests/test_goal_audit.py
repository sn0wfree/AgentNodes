# -*- coding: utf-8 -*-
"""M5（批 3c）：``apps/goal/audit`` 判据面单测——自 slash_goal 下沉的领域逻辑。

重点回归：``all_criteria_covered`` 修掉幻影键 ``all_covered``（无生产方，
原 CLI 非 lite 完成在有 criteria 时恒拒绝）。行为变更已声明：证据齐即可
完成。
"""
from __future__ import annotations

from agentnodes.apps.goal.audit import (
    all_criteria_covered,
    build_completion_audit_rows,
    resolve_criterion_ref,
)

_E = lambda eid, cid: {"evidence_id": eid, "criterion_id": cid}


def _snapshot(criteria, evidence):
    return {
        "goal": {"goal_id": "g1"},
        "criteria": criteria,
        "evidence": evidence,
        "evidence_count": len(evidence),
    }


class TestResolveCriterionRef:
    def test_one_based_index(self):
        criteria = [{"criterion_id": "c1"}, {"criterion_id": "c2"}]
        assert resolve_criterion_ref(criteria, "1") == "c1"
        assert resolve_criterion_ref(criteria, "2") == "c2"

    def test_exact_id_and_prefix(self):
        criteria = [{"criterion_id": "coverage.data"}, {"criterion_id": "coverage.model"}]
        assert resolve_criterion_ref(criteria, "coverage.model") == "coverage.model"
        # 前缀歧义 → 首个命中（与原实现一致）
        assert resolve_criterion_ref(criteria, "coverage") == "coverage.data"

    def test_integer_out_of_range_falls_through_to_prefix(self):
        criteria = [{"criterion_id": "99x"}]
        assert resolve_criterion_ref(criteria, "99") == "99x"

    def test_unknown_returns_none(self):
        assert resolve_criterion_ref([{"criterion_id": "c1"}], "ghost") is None
        assert resolve_criterion_ref([], "1") is None


class TestAllCriteriaCovered:
    def test_no_criteria_means_covered(self):
        assert all_criteria_covered(_snapshot([], [])) is True

    def test_all_covered_by_evidence(self):
        snap = _snapshot(
            [{"criterion_id": "c1"}, {"criterion_id": "c2"}],
            [_E("e1", "c1"), _E("e2", "c2")],
        )
        assert all_criteria_covered(snap) is True

    def test_uncovered_returns_false(self):
        snap = _snapshot(
            [{"criterion_id": "c1"}, {"criterion_id": "c2"}],
            [_E("e1", "c1")],
        )
        assert all_criteria_covered(snap) is False

    def test_phantom_key_no_longer_needed(self):
        """回归：快照无 ``all_covered`` 键也能正确判定（幻影键修复）。"""
        snap = _snapshot([{"criterion_id": "c1"}], [_E("e1", "c1")])
        assert "all_covered" not in snap
        assert all_criteria_covered(snap) is True


class TestBuildCompletionAuditRows:
    def test_row_shape_matches_original_formula(self):
        snap = _snapshot(
            [{"criterion_id": "c1"}, {"criterion_id": "c2"}],
            [_E("e1", "c1")],
        )
        rows = build_completion_audit_rows(snap, "recap-text")
        assert len(rows) == 2
        assert [r.criterion_id for r in rows] == ["c1", "c2"]
        assert all(r.result == "satisfied" for r in rows)
        # 原实现：每行带全量 evidence_ids（行形收敛另立批，本批逐字等价）
        assert all(r.evidence_ids == ["e1"] for r in rows)
        assert all(r.notes == "recap-text" for r in rows)

    def test_explicit_evidence_ids_override(self):
        snap = _snapshot([{"criterion_id": "c1"}], [_E("e1", "c1")])
        rows = build_completion_audit_rows(snap, "", evidence_ids=["e9"])
        assert rows[0].evidence_ids == ["e9"]

    def test_empty_criteria_gives_empty_rows(self):
        assert build_completion_audit_rows(_snapshot([], []), "") == []
