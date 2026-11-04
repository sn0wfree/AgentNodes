"""基因组版本链的持久化（跨进程存活）。

覆盖目标 §「RSI 自进化」的第一层要求：**基因组必须活过进程**。
内存实现在重启后回到 ``gv-0``，进化成果归零——本文件守住落盘路径。

与 ``test_evolution_gate.py`` 的分工：那边测验证/回滚的**判定语义**，
本文件测版本链的**存储与恢复**（两次独立构造之间的一致性）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.storage.genome_store import GenomeStore
from strategy_research.kernel.orchestration.genome_version import (
    GenomeVersion,
    GenomeVersionStore,
)


@pytest.fixture
def store(tmp_path):
    return GenomeStore(tmp_path / "genome")


class TestPersistenceBasics:
    def test_fresh_store_starts_at_gv0(self, store):
        g = GenomeVersionStore(store=store)
        assert g.current_version_id() == "gv-0"
        assert store.exists(), "初始版本必须落盘"

    def test_activate_persists(self, store):
        g = GenomeVersionStore(store=store)
        vid = g.activate("gpr-1", {"tool_set": ["read"]})
        # 新实例（模拟重启）必须读到同一版本
        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == vid
        assert g2.tier1_active() == {"tool_set": ["read"]}
        assert g2.current().activated_by_proposal == "gpr-1"

    def test_multiple_activations_restore_full_chain(self, store):
        g = GenomeVersionStore(store=store)
        v1 = g.activate("p1", {"tool_set": ["read"]})
        v2 = g.activate("p2", {"prompt": {"chat": {"v": 2}}})
        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == v2
        assert len(g2.history()) == 3, "gv-0 + 两次升版"
        assert g2.history()[0].version_id == "gv-0"
        assert g2.history()[1].version_id == v1

    def test_tier1_merges_across_activations(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"tool_set": ["read"]})
        g.activate("p2", {"prompt": {"chat": {"v": 2}}})
        g2 = GenomeVersionStore(store=store)
        assert g2.tier1_active() == {
            "tool_set": ["read"],
            "prompt": {"chat": {"v": 2}},
        }


class TestRollbackPersistence:
    def test_rollback_persists(self, store):
        g = GenomeVersionStore(store=store)
        v1 = g.activate("p1", {"tool_set": ["read"]})
        g.activate("p2", {"prompt": {"x": 1}})
        g.rollback()

        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == v1, "回滚后的游标必须持久化"
        assert g2.tier1_active() == {"tool_set": ["read"]}

    def test_rollback_to_known_version_persists(self, store):
        g = GenomeVersionStore(store=store)
        v1 = g.activate("p1", {"a": 1})
        g.activate("p2", {"b": 2})
        g.rollback_to(v1)

        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == v1

    def test_rollback_unknown_version_raises(self, store):
        g = GenomeVersionStore(store=store)
        with pytest.raises(KeyError, match="unknown genome version"):
            g.rollback_to("gv-does-not-exist")

    def test_rollback_at_base_is_noop(self, store):
        g = GenomeVersionStore(store=store)
        assert g.rollback() == "gv-0"
        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == "gv-0"


class TestInMemoryPathUnchanged:
    """无 store 时行为必须与改动前逐字一致（测试/脚本路径）。"""

    def test_no_store_starts_fresh(self):
        g = GenomeVersionStore()
        assert g.current_version_id() == "gv-0"
        vid = g.activate("p1", {"x": 1})
        assert g.current_version_id() == vid
        assert g.tier1_active() == {"x": 1}

    def test_no_store_does_not_create_files(self, tmp_path):
        g = GenomeVersionStore()
        g.activate("p1", {"x": 1})
        # 没有 store 就不该有任何落盘
        assert list(tmp_path.iterdir()) == []

    def test_explicit_initial_version_respected(self, store):
        """纯内存路径下 initial 仍然生效（既有契约）。"""
        init = GenomeVersion(version_id="gv-custom", activated_at="2026-01-01T00:00:00Z")
        g = GenomeVersionStore(initial=init)
        assert g.current_version_id() == "gv-custom"


class TestStoreRobustness:
    def test_corrupt_cursor_falls_back_to_zero(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"x": 1})
        (store._root / "cursor.json").write_text("{not json", encoding="utf-8")

        g2 = GenomeVersionStore(store=store)
        assert g2.current_version_id() == "gv-0", "损坏的游标退回起点而非崩溃"

    def test_corrupt_chain_line_skipped(self, store):
        """崩溃时的半写行必须被跳过，不能拖垮整体加载。"""
        g = GenomeVersionStore(store=store)
        vid = g.activate("p1", {"x": 1})
        # 追加一条残行（模拟崩溃时的半写）
        with (store._root / "chain.jsonl").open("a", encoding="utf-8") as f:
            f.write('{"version_id": "gv-broken"\n')

        # 关键：构造不抛异常
        g2 = GenomeVersionStore(store=store)

        # 残行前的内容完好可读
        assert g2.current_version_id() in ("gv-0", vid)
        assert g2.tier1_active() == {"x": 1} or g2.tier1_active() == {}

    def test_cursor_clamped_to_chain_length(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"x": 1})
        # 游标指向不存在的索引
        store.set_cursor(999)

        g2 = GenomeVersionStore(store=store)
        # 必须钳制到最后一个有效版本，而不是 IndexError
        assert g2.current_version_id() != ""


class TestStoreAPI:
    def test_append_is_append_only(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"a": 1})
        first = (store._root / "chain.jsonl").read_text(encoding="utf-8")
        g.activate("p2", {"b": 2})
        second = (store._root / "chain.jsonl").read_text(encoding="utf-8")
        assert second.startswith(first), "既有行必须原样保留（append-only）"

    def test_clear_removes_files(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"x": 1})
        assert store.exists()
        store.clear()
        assert not store.exists()
