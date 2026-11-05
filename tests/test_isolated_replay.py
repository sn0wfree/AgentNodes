"""阶段三验收 ⑤：validation_gate 在容量闸 study 池的隔离 slot 中只读回放。

补 goal §3 阶段三的完成标准：
    「validation_gate 在隔离 Worker slot（来自阶段二容量闸的 study 池
      独立 slot）回放 holdout，只读」

此前的 test_evolution_gate 只用裸 lambda 当 replay，没有真正经过容量闸。
本文件用 CapacityGate 实测 slot 的取得/释放/池满排队。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.apps.evolution import (
    GenomeProposal,
    HoldoutSet,
    IsolatedSlotReplay,
    SlotUnavailable,
    Trajectory,
    ValidationGate,
)
from agentnodes.kernel.gateway.capacity import CapacityConfig, CapacityGate
from agentnodes.kernel.orchestration.genome_version import GenomeVersionStore


def _holdout(n: int = 2) -> HoldoutSet:
    return HoldoutSet(
        trajectories=tuple(
            Trajectory(
                event_id=f"e{i}", assignment_id=f"a{i}", ticket_id=f"t{i}",
                run_id=f"r{i}", status="failed",
                ts=f"2026-01-0{i+1}T00:00:00Z",
            )
            for i in range(n)
        ),
        seed=42,
        task_signature="sig",
    )


def _gate(*, study: int = 1) -> CapacityGate:
    return CapacityGate(CapacityConfig(chat=8, study=study, cron=2))


# ── slot 取得与释放 ──────────────────────────────────────────────────


class TestSlotLifecycle:
    def test_slot_acquired_and_released(self):
        gate = _gate(study=1)
        replay = IsolatedSlotReplay(
            gate=gate, replay_fn=lambda p, h: 1.0, wait_timeout=0.1
        )
        assert gate.usage("study")["used"] == 0
        replay(GenomeProposal.new("prompt", {}), _holdout())
        assert gate.usage("study")["used"] == 0, "回放结束必须归还 slot"
        assert replay.replays == 1

    def test_slot_held_during_replay(self):
        """回放执行期间 slot 应处于占用状态。"""
        gate = _gate(study=1)
        observed = {}

        def fn(proposal, holdout):
            observed["used_during"] = gate.usage("study")["used"]
            return 1.0

        IsolatedSlotReplay(gate=gate, replay_fn=fn, wait_timeout=0.1)(
            GenomeProposal.new("prompt", {}), _holdout()
        )
        assert observed["used_during"] == 1

    def test_slot_released_even_when_replay_raises(self):
        """回放抛异常也必须归还 slot（finally）。"""
        gate = _gate(study=1)

        def boom(p, h):
            raise RuntimeError("replay exploded")

        replay = IsolatedSlotReplay(gate=gate, replay_fn=boom, wait_timeout=0.1)
        with pytest.raises(RuntimeError):
            replay(GenomeProposal.new("prompt", {}), _holdout())
        assert gate.usage("study")["used"] == 0

    def test_no_gate_means_no_isolation(self):
        """gate=None 时不占位（测试路径），仍正常回放。"""
        replay = IsolatedSlotReplay(gate=None, replay_fn=lambda p, h: 2.0)
        assert replay(GenomeProposal.new("prompt", {}), _holdout()) == 2.0
        assert replay.stats()["pool_usage"] is None


# ── 池满 → 排队不拒绝 ────────────────────────────────────────────────


class TestPoolFullQueues:
    def test_waits_when_pool_full_then_proceeds(self):
        """study 池只有 1 个位；占住后回放应等待，释放后成功。"""
        gate = _gate(study=1)
        blocker = gate.acquire("study")
        assert blocker is not None

        released = {"done": False}

        def fn(proposal, holdout):
            released["done"] = True
            return 1.0

        replay = IsolatedSlotReplay(
            gate=gate, replay_fn=fn, wait_timeout=2.0, _poll_interval=0.01
        )

        # 在另一个"线程"里稍后释放——这里用简单的时间点模拟
        import threading
        import time

        def free_later():
            time.sleep(0.05)
            gate.release(blocker)

        t = threading.Thread(target=free_later)
        t.start()
        try:
            out = replay(GenomeProposal.new("prompt", {}), _holdout())
        finally:
            t.join()

        assert out == 1.0
        assert released["done"] is True
        assert replay.waits >= 1, "池满时应记录等待，而不是直接拒绝"

    def test_timeout_raises_slot_unavailable(self):
        gate = _gate(study=1)
        blocker = gate.acquire("study")
        replay = IsolatedSlotReplay(
            gate=gate, replay_fn=lambda p, h: 1.0,
            wait_timeout=0.1, _poll_interval=0.01,
        )
        with pytest.raises(SlotUnavailable):
            replay(GenomeProposal.new("prompt", {}), _holdout())
        gate.release(blocker)
        assert replay.slot_unavailable == 1


# ── study 池与 chat/cron 隔离 ────────────────────────────────────────


class TestPoolIsolation:
    def test_uses_study_pool_not_chat(self):
        gate = _gate(study=2)
        observed = {}

        def fn(p, h):
            observed["study"] = gate.usage("study")["used"]
            observed["chat"] = gate.usage("chat")["used"]
            return 1.0

        IsolatedSlotReplay(gate=gate, replay_fn=fn, wait_timeout=0.1)(
            GenomeProposal.new("prompt", {}), _holdout()
        )
        assert observed["study"] == 1
        assert observed["chat"] == 0, "回放不得占用 chat 池"

    def test_chat_full_does_not_block_replay(self):
        gate = _gate(study=1)
        for _ in range(8):
            gate.acquire("chat")  # 打满 chat
        assert gate.acquire("chat") is None
        replay = IsolatedSlotReplay(
            gate=gate, replay_fn=lambda p, h: 1.0, wait_timeout=0.5
        )
        assert replay(GenomeProposal.new("prompt", {}), _holdout()) == 1.0


# ── 与 ValidationGate 集成 ───────────────────────────────────────────


class _Emitter:
    def __init__(self):
        self.events = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def _wired_gate(*, study=2, metric=1.0, wait_timeout=1.0):
    capacity = _gate(study=study)
    emit = _Emitter()
    genome = GenomeVersionStore()
    replay = IsolatedSlotReplay(
        gate=capacity,
        replay_fn=lambda p, h: metric,
        wait_timeout=wait_timeout,
        _poll_interval=0.01,
    )
    gate = ValidationGate(
        genome=genome, emit=emit, replay=replay, baseline_metric=lambda: 1.0,
    )
    return gate, capacity, emit, genome, replay


class TestGateWithIsolatedReplay:
    def test_approve_releases_slot(self):
        gate, capacity, emit, genome, replay = _wired_gate(metric=1.0)
        out = gate.validate(GenomeProposal.new("prompt", {"r": "v2"}), _holdout())
        assert out.status.value == "approved"
        assert capacity.usage("study")["used"] == 0
        assert replay.replays == 1

    def test_reject_releases_slot(self):
        gate, capacity, _, _, _ = _wired_gate(metric=0.1)
        out = gate.validate(GenomeProposal.new("prompt", {"r": "v2"}), _holdout())
        assert out.status.value == "rejected"
        assert capacity.usage("study")["used"] == 0

    def test_rollback_releases_slot(self):
        gate, capacity, emit, genome, _ = _wired_gate(metric=0.1)
        for i in range(3):
            gate.validate(GenomeProposal.new("prompt", {"v": i}), _holdout())
        assert emit.of("genome.rollback"), "3 次失败应回滚"
        assert capacity.usage("study")["used"] == 0, "全部 slot 必须归还"

    def test_slot_unavailable_escalates_not_rollback(self):
        """slot 拿不到 → escalate，且**不**计入回滚失败计数。"""
        gate, capacity, emit, genome, replay = _wired_gate(
            study=1, metric=1.0, wait_timeout=0.05
        )
        blocker = capacity.acquire("study")
        try:
            results = [
                gate.validate(GenomeProposal.new("prompt", {"v": i}), _holdout())
                for i in range(3)
            ]
        finally:
            capacity.release(blocker)

        assert all(r.status.value == "rejected" for r in results)
        assert all("slot unavailable" in (r.rejection_reason or "") for r in results)
        assert emit.of("genome.rollback") == [], (
            "资源不足不应触发回滚——那是把基础设施故障误判成模型退化"
        )


# ── 只读性 ───────────────────────────────────────────────────────────


class TestReadOnlyContract:
    def test_replay_receives_only_immutable_inputs(self):
        """回放函数只能得到 frozen 的 proposal 与 holdout 集。"""
        seen = {}

        def fn(proposal, holdout):
            seen["proposal"] = proposal
            seen["holdout"] = holdout
            return 1.0

        IsolatedSlotReplay(gate=_gate(), replay_fn=fn, wait_timeout=0.1)(
            GenomeProposal.new("prompt", {}), _holdout()
        )
        # HoldoutSet 是 frozen
        with pytest.raises(Exception):
            seen["holdout"].seed = 999
        # GenomeProposal 是 frozen
        with pytest.raises(Exception):
            seen["proposal"].status = "approved"

    def test_holdout_has_no_write_methods(self):
        hs = _holdout()
        for m in ("append", "update", "delete", "write"):
            assert not hasattr(hs, m), f"HoldoutSet 不得有 {m}"
