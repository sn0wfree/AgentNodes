"""阶段二验收 ②：容量组闸（施工方案 §3.7）。

覆盖 goal §3 阶段二的容量闸完成标准：
- chat 宽 / study 窄（= ExecutorBridge 池上限）/ cron 隔离
- 满 → 返回 None（调用方去 mailbox 排队，不拒绝）
- running 不抢占
- 池释放后可再取得
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.gateway.capacity import (
    DEFAULT_CHAT_LIMIT,
    DEFAULT_CRON_LIMIT,
    CapacityConfig,
    CapacityGate,
    bridge_default_pool_size,
    pool_for_kind,
)


class TestStudyLimitMatchesBridge:
    """附录 D：study 上限必须与 ExecutorBridge 实测默认值一致，不得猜。"""

    def test_bridge_default_formula(self):
        """ExecutorBridge: max(1, cpu_count-1)（executor_bridge.py:101）。"""
        expected = max(1, (os.cpu_count() or 2) - 1)
        assert bridge_default_pool_size() == expected

    def test_config_study_defaults_to_bridge(self):
        cfg = CapacityConfig()
        assert cfg.study == bridge_default_pool_size()

    def test_chat_and_cron_defaults(self):
        cfg = CapacityConfig()
        assert cfg.chat == DEFAULT_CHAT_LIMIT == 32
        assert cfg.cron == DEFAULT_CRON_LIMIT == 2


class TestAcquireRelease:
    def test_acquire_until_full(self):
        gate = CapacityGate(CapacityConfig(chat=2, study=1, cron=1))
        t1 = gate.acquire("chat")
        t2 = gate.acquire("chat")
        assert t1 is not None and t2 is not None
        assert gate.acquire("chat") is None, "池满必须返回 None（去 mailbox 排队）"

    def test_release_frees_slot(self):
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        t = gate.acquire("chat")
        assert gate.acquire("chat") is None
        gate.release(t)
        assert gate.acquire("chat") is not None

    def test_release_is_idempotent(self):
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        t = gate.acquire("chat")
        gate.release(t)
        gate.release(t)  # 不应抛
        assert gate.usage("chat")["used"] == 0

    def test_release_none_is_safe(self):
        gate = CapacityGate()
        gate.release(None)  # 不应抛

    def test_unknown_pool_rejected(self):
        gate = CapacityGate()
        with pytest.raises(ValueError, match="unknown pool"):
            gate.acquire("nope")


class TestPoolsIndependent:
    def test_study_full_does_not_block_cron(self):
        """cron 独立池：study 满不影响 cron（施工方案 §3.7）。"""
        gate = CapacityGate(CapacityConfig(chat=8, study=1, cron=2))
        assert gate.acquire("study") is not None
        assert gate.acquire("study") is None, "study 已满"
        # cron 不受影响
        assert gate.acquire("cron") is not None
        assert gate.acquire("cron") is not None
        assert gate.acquire("cron") is None, "cron 自己满"

    def test_chat_full_does_not_block_study(self):
        gate = CapacityGate(CapacityConfig(chat=1, study=4, cron=1))
        gate.acquire("chat")
        assert gate.acquire("chat") is None
        assert gate.acquire("study") is not None

    def test_snapshot_reports_all_pools(self):
        gate = CapacityGate(CapacityConfig(chat=2, study=2, cron=2))
        gate.acquire("chat")
        snap = gate.snapshot()
        assert snap["chat"]["used"] == 1
        assert snap["chat"]["available"] == 1
        assert snap["study"]["used"] == 0


class TestRunningNotPreempted:
    def test_held_token_survives_new_requests(self):
        """running 不抢占：已持 token 的不会被新请求挤掉。"""
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        first = gate.acquire("chat")
        assert first is not None
        # 后续请求全部失败，但第一个仍有效
        for _ in range(5):
            assert gate.acquire("chat") is None
        assert gate.usage("chat")["used"] == 1
        gate.release(first)
        assert gate.usage("chat")["used"] == 0


class TestSlots:
    def test_slots_are_distinct(self):
        gate = CapacityGate(CapacityConfig(chat=4, study=1, cron=1))
        tokens = [gate.acquire("chat") for _ in range(4)]
        slots = [t.slot for t in tokens]
        assert len(set(slots)) == 4, "同池内 slot 必须互不相同"

    def test_reused_slot_after_release(self):
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        t1 = gate.acquire("chat")
        gate.release(t1)
        t2 = gate.acquire("chat")
        assert t2 is not None and t2.slot == t1.slot


class TestDisable:
    def test_disabled_gate_always_acquires(self):
        """§3.8：容量闸可独立 disable（回滚开关）。"""
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        gate.disable()
        for _ in range(10):
            assert gate.acquire("chat") is not None
        assert gate.enabled is False

    def test_release_disabled_token_is_noop(self):
        gate = CapacityGate(CapacityConfig(chat=1, study=1, cron=1))
        gate.disable()
        t = gate.acquire("chat")
        assert t is not None and t.slot == -1
        gate.release(t)  # 不占位，release 是 no-op
        gate.enable()
        assert gate.usage("chat")["used"] == 0


class TestPoolForKind:
    def test_mapping(self):
        class K:
            def __init__(self, v):
                self.value = v

        assert pool_for_kind(K("chat")) == "chat"
        assert pool_for_kind(K("study")) == "study"
        assert pool_for_kind(K("cron")) == "cron"
        assert pool_for_kind(K("automation")) == "cron"
        # 未知 kind 归宽池兜底
        assert pool_for_kind("weird") == "chat"
        # 字符串也接受
        assert pool_for_kind("study") == "study"
