"""阶段三验收 ①：holdout 只读与确定性（施工方案 §4.10）。

覆盖 goal §3 阶段三的 holdout 完成标准：
- holdout.py 无 append/update/delete 方法（hasattr 断言）
- sample(task_signature, seed=N) 三次结果一致
- 抽样结果不包含当前 genome_version.activated_at 之后的轨迹
- 只能抽 failed / dropped / ready 三态
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.evolution import holdout as holdout_mod
from agentnodes.kernel.evolution import (
    HoldoutSet,
    Trajectory,
    compute_task_signature,
    sample,
)
from agentnodes.kernel.evolution.holdout import (
    EventStoreReportSource,
    StaticGenomeVersion,
)


# ── 测试替身 ─────────────────────────────────────────────────────────


class _Source:
    def __init__(self, reports: list[dict]):
        self._reports = reports

    def iter_assignment_reports(self):
        return iter(self._reports)


def _report(
    aid: str,
    status: str,
    ts: str,
    sig: str = "sig-A",
    **extra,
) -> dict:
    d = {
        "id": f"ev-{aid}",
        "assignment_id": aid,
        "ticket_id": f"tkt-{aid}",
        "run_id": f"run-{aid}",
        "status": status,
        "ts": ts,
        "task_signature": sig,
        "payload": {},
    }
    d.update(extra)
    return d


# ── H5：写保护 ───────────────────────────────────────────────────────


class TestReadOnlyContract:
    """H5：holdout 只暴露 sample()，没有写方法。"""

    def test_no_write_methods_on_module(self):
        for forbidden in ("append", "update", "delete", "write", "insert", "save"):
            assert not hasattr(holdout_mod, forbidden), (
                f"holdout module must not expose {forbidden}() (H5)"
            )

    def test_holdout_set_is_frozen(self):
        """HoldoutSet 是 frozen dataclass：赋值会抛 FrozenInstanceError。"""
        hs = HoldoutSet(trajectories=(), seed=1, task_signature="s")
        with pytest.raises(Exception):
            hs.seed = 2  # type: ignore[misc]

    def test_trajectory_is_frozen(self):
        t = Trajectory(
            event_id="e", assignment_id="a", ticket_id="t",
            run_id="r", status="failed", ts="2026-01-01T00:00:00Z",
        )
        with pytest.raises(Exception):
            t.status = "ready"  # type: ignore[misc]

    def test_holdout_set_trajectories_is_tuple(self):
        hs = sample("sig-A", 42, source=_Source([]))
        assert isinstance(hs.trajectories, tuple), "不可变容器，不是 list"


# ── 确定性 ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_three_calls_same_seed_identical(self):
        """同一 (task_signature, seed) 三次调用结果一致（附录 C）。"""
        reports = [
            _report(f"a{i}", "failed", f"2026-01-{i:02d}T00:00:00Z")
            for i in range(1, 11)
        ]
        src = _Source(reports)
        r1 = sample("sig-A", 42, source=src)
        r2 = sample("sig-A", 42, source=src)
        r3 = sample("sig-A", 42, source=src)
        ids = [
            [t.assignment_id for t in s] for s in (r1, r2, r3)
        ]
        assert ids[0] == ids[1] == ids[2]

    def test_different_seed_different_order(self):
        reports = [
            _report(f"a{i}", "failed", f"2026-01-{i:02d}T00:00:00Z")
            for i in range(1, 21)
        ]
        src = _Source(reports)
        a = [t.assignment_id for t in sample("sig-A", 1, source=src)]
        b = [t.assignment_id for t in sample("sig-A", 2, source=src)]
        assert a != b, "不同 seed 应产出不同顺序（否则 seed 没起作用）"

    def test_order_independent_of_source_ordering(self):
        """候选顺序取决于事件源返回顺序时，仍要产出同一结果。

        实现必须先按稳定 key 排序再 shuffle。
        """
        base = [
            _report(f"a{i}", "failed", f"2026-01-{i:02d}T00:00:00Z")
            for i in range(1, 11)
        ]
        forward = [t.assignment_id for t in sample("sig-A", 7, source=_Source(base))]
        backward = [
            t.assignment_id
            for t in sample("sig-A", 7, source=_Source(list(reversed(base))))
        ]
        assert forward == backward

    def test_seed_required(self):
        """seed 不允许隐式默认（附录 C）。"""
        with pytest.raises(TypeError):
            sample("sig-A", source=_Source([]))  # type: ignore[call-arg]


# ── 过滤规则 ─────────────────────────────────────────────────────────


class TestFiltering:
    def test_only_holdout_statuses(self):
        """只接受 failed / dropped / ready。"""
        reports = [
            _report("a1", "failed", "2026-01-01T00:00:00Z"),
            _report("a2", "dropped", "2026-01-02T00:00:00Z"),
            _report("a3", "ready", "2026-01-03T00:00:00Z"),
            _report("a4", "running", "2026-01-04T00:00:00Z"),
            _report("a5", "waiting", "2026-01-05T00:00:00Z"),
            _report("a6", "blocked", "2026-01-06T00:00:00Z"),
        ]
        hs = sample("sig-A", 1, source=_Source(reports))
        assert {t.status for t in hs} == {"failed", "dropped", "ready"}
        assert len(hs) == 3

    def test_filter_by_task_signature(self):
        reports = [
            _report("a1", "failed", "2026-01-01T00:00:00Z", sig="sig-A"),
            _report("a2", "failed", "2026-01-02T00:00:00Z", sig="sig-B"),
        ]
        hs = sample("sig-A", 1, source=_Source(reports))
        assert [t.assignment_id for t in hs] == ["a1"]

    def test_excludes_trajectories_after_activation(self):
        """不包含当前 genome_version.activated_at 之后的轨迹（防污染）。"""
        reports = [
            _report("old", "failed", "2026-01-01T00:00:00Z"),
            _report("new", "failed", "2026-03-01T00:00:00Z"),
        ]
        genome = StaticGenomeVersion("2026-02-01T00:00:00Z")
        hs = sample("sig-A", 1, source=_Source(reports), genome=genome)
        assert [t.assignment_id for t in hs] == ["old"]

    def test_no_genome_means_no_time_filter(self):
        reports = [
            _report("old", "failed", "2026-01-01T00:00:00Z"),
            _report("new", "failed", "2026-03-01T00:00:00Z"),
        ]
        hs = sample("sig-A", 1, source=_Source(reports))
        assert len(hs) == 2

    def test_limit_respected(self):
        reports = [
            _report(f"a{i}", "failed", f"2026-01-{i:02d}T00:00:00Z")
            for i in range(1, 31)
        ]
        hs = sample("sig-A", 1, source=_Source(reports), limit=5)
        assert len(hs) == 5

    def test_empty_source(self):
        hs = sample("sig-A", 1, source=_Source([]))
        assert len(hs) == 0


# ── task_signature ───────────────────────────────────────────────────


class TestTaskSignature:
    """签名契约（W4 · 2026-11-06 降维后）。

    口径变更：旧版 = ``sha256(objective 原文 | role | tools)``——objective 是
    agent 自由措辞，导致**同一类任务几乎永不聚合**（单签名样本恒 <
    MIN_HOLDOUT_SAMPLES=5，default_policy 永不提案）。
    新版 = ``sha256("v2|" + role + "|" + intent桶 + "|" + sorted(tools))``，
    确定性降维、无 LLM、意图桶是 5 个封闭常量（research/implement/
    evaluate/operate/other，关键词表可审计）。

    因此「不同措辞 → 不同签名」不再是契约，**同桶同措辞池化**才是。
    """

    def test_stable(self):
        a = compute_task_signature("研究动量", "researcher", {"x", "y"})
        b = compute_task_signature("研究动量", "researcher", {"y", "x"})
        assert a == b

    def test_objective_normalized(self):
        """同意图桶内：措辞不同（strip/lower 之外也一样）签名相同。"""
        a = compute_task_signature("  Momentum  ", "researcher", {"x"})
        b = compute_task_signature("momentum", "researcher", {"x"})
        assert a == b

    def test_same_bucket_pools(self):
        """降维的核心收益：同 role+同意图桶+同工具集 → 同一签名。

        （这正是让 holdout 能攒到 ≥5 样本、default_policy 能真正提案的前提）
        """
        a = compute_task_signature("研究动量因子", "researcher", {"x"})
        b = compute_task_signature("分析一下动量", "researcher", {"x"})
        assert a == b

    def test_different_inputs_differ(self):
        base = compute_task_signature("m", "r", {"x"})
        # role 不同 → 不同
        assert compute_task_signature("m", "r2", {"x"}) != base
        # 工具集不同 → 不同
        assert compute_task_signature("m", "r", {"y"}) != base
        # 意图桶不同 → 不同（"analyze" 落 evaluate，"fix" 落 implement）
        assert compute_task_signature("analyze m", "r", {"x"}) != base

    def test_length_16(self):
        assert len(compute_task_signature("m", "r", set())) == 16


# ── EventStore 适配器 ────────────────────────────────────────────────


class TestEventStoreAdapter:
    def test_adapts_replay_output(self):
        class _Ev:
            def __init__(self, data):
                self.data = data
                self.id = data["id"]

        class _Store:
            def replay(self, sid, types=None):
                return [
                    _Ev(_report("a1", "failed", "2026-01-01T00:00:00Z")),
                    _Ev(_report("a2", "running", "2026-01-02T00:00:00Z")),
                ]

        src = EventStoreReportSource(_Store(), ["s1"])
        hs = sample("sig-A", 1, source=src)
        assert [t.assignment_id for t in hs] == ["a1"]
