"""基因组的三个层级：Tier 0 快照 / Tier 1 单组件撤销 / Tier 2 研究产物。

补上此前三处**设计存在但实际未启用**的缺口：

1. ``tier0_frozen`` 永远为空——只被读取和继承，从无写入路径
2. ``tier2_self_modifiable`` 零写入路径——字段是空壳
3. 没有 ``deactivate`` ——只能整链回退，无法只撤一个组件

与 ``test_genome_persistence.py`` 的分工：那边测**存储与恢复**，本文件
测**三层语义**（谁能写、谁不能写、写入后如何继承）。
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


@pytest.fixture
def tier0():
    """真实采集结果（不 mock——要验证采集本身能跑通）。"""
    from strategy_research.interfaces.tier0_snapshot import collect_tier0

    return collect_tier0()


# ── 缺口 1：Tier 0 快照 ─────────────────────────────────────────────


class TestTier0SnapshotCollection:
    def test_collects_all_four_categories(self, tier0):
        assert set(tier0) == {"budget", "stop_policy", "state_machine", "methodology"}

    def test_budget_has_real_fields(self, tier0):
        budget = tier0["budget"]
        assert "max_tokens" in budget["fields"]
        assert budget["source"].startswith("kernel.agent.strategy.budget")

    def test_stop_policy_has_defaults(self, tier0):
        stop = tier0["stop_policy"]
        assert stop["defaults"]["max_iterations"] == 10
        assert stop["defaults"]["wrap_up_ratio"] == 0.8

    def test_state_machine_has_real_transitions(self, tier0):
        sm = tier0["state_machine"]
        assert sm["ticket"]["pending"] == ["active", "ended"]
        assert sm["assignment"]["ended"] == []
        assert "pending" in sm["assignment"]

    def test_methodology_stores_only_fingerprints(self, tier0):
        """方法论正文是 Tier 0 内容，只存指纹不复制全文。"""
        meth = tier0["methodology"]
        assert "principles.md" in meth["files"]
        for digest in meth["files"].values():
            assert len(digest) == 16  # sha256[:16]

    def test_collection_never_raises(self):
        """采集失败必须降级，不能因为审计资产采不到就起不来。"""
        from strategy_research.interfaces import tier0_snapshot

        # 默认环境下应该能采到；这里验证的是接口返回 dict 而非抛错
        result = tier0_snapshot.collect_tier0()
        assert isinstance(result, dict)


class TestTier0Immutable:
    def test_tier0_written_at_construction(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        assert g.tier0_frozen() == tier0

    def test_tier0_survives_activate(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.activate("p1", {"tool_set": ["read"]})
        assert g.tier0_frozen() == tier0

    def test_tier0_survives_deactivate(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.activate("p1", {"tool_set": ["read"]})
        g.deactivate("tool_set")
        assert g.tier0_frozen() == tier0

    def test_tier0_survives_set_tier2(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.set_tier2("strategy.py:x", "hash")
        assert g.tier0_frozen() == tier0

    def test_tier0_has_no_setter(self):
        """H3 的实现层保证：没有改 Tier 0 的入口。"""
        g = GenomeVersionStore()
        public = {
            n for n in dir(g)
            if not n.startswith("_") and callable(getattr(g, n))
        }
        # 任何含 tier0 的可调用入口都不该是 setter
        setters = {
            n for n in public
            if "tier0" in n.lower() and any(
                v in n.lower() for v in ("set", "update", "write", "modify")
            )
        }
        assert setters == set()

    def test_tier0_restored_from_disk(self, store, tier0):
        """重启后 Tier 0 来自磁盘（历史快照），不是当前进程重采。"""
        g = GenomeVersionStore(store=store, tier0=tier0)
        g.activate("p1", {"tool_set": ["read"]})

        g2 = GenomeVersionStore(store=store)  # 不传 tier0
        assert g2.tier0_frozen() == tier0

    def test_legacy_data_without_tier0_is_backfilled(self, store, tier0):
        """旧磁盘数据缺 Tier 0 时必须补齐一次。

        ``tier0_frozen`` 是后加的字段——早于它的快照里是空 dict。不补齐
        的话旧库的 ``tier0_frozen()`` 永远返回空，"某个版本当时的预算是
        多少"永远查不到。这是真实踩到的问题：本机 `.quantnodes/genome/`
        里有本轮之前留下的 gv-0，tier0 为空，导致生产路径上采集值不生效。
        """
        # 先造一份"旧格式"数据：直接写一个 tier0 为空的版本
        old = GenomeVersion(
            version_id="gv-legacy",
            tier0_frozen={},          # 旧格式：空
            tier1_active={"tool_set": ["read"]},
            activated_at="2026-01-01T00:00:00Z",
            activated_by_proposal="p-legacy",
        )
        from strategy_research.kernel.orchestration.genome_version import _to_dict

        store.append(_to_dict(old))
        store.set_cursor(0)

        # 恢复时不传 tier0：无法补齐
        g_no = GenomeVersionStore(store=store)
        assert g_no.tier0_frozen() == {}

        # 恢复时传 tier0：必须补齐
        g_yes = GenomeVersionStore(store=store, tier0=tier0)
        assert g_yes.tier0_frozen() == tier0
        # tier1 原样保留，不能被补齐动作冲掉
        assert g_yes.tier1_active() == {"tool_set": ["read"]}
        # 补齐是一次可追溯的版本变更
        assert g_yes.current().activated_by_proposal == "backfill:tier0"

    def test_backfill_does_not_overwrite_existing_tier0(self, store, tier0):
        """已有真实 Tier 0 的快照不能被当前进程的活动覆盖。"""
        custom = {"budget": {"max_tokens": 12345}}
        init = GenomeVersion(
            version_id="gv-has-tier0",
            tier0_frozen=custom,
            activated_at="2026-01-01T00:00:00Z",
        )
        from strategy_research.kernel.orchestration.genome_version import _to_dict

        store.append(_to_dict(init))
        store.set_cursor(0)

        g = GenomeVersionStore(store=store, tier0=tier0)
        assert g.tier0_frozen() == custom, "历史快照优先，不被当前采集覆盖"

    def test_backfill_noop_when_no_tier0_collected(self, store):
        """采集结果为空时不做补齐（避免写一个空版本）。"""
        old = GenomeVersion(
            version_id="gv-legacy", tier0_frozen={},
            activated_at="2026-01-01T00:00:00Z",
        )
        from strategy_research.kernel.orchestration.genome_version import _to_dict

        store.append(_to_dict(old))
        store.set_cursor(0)

        g = GenomeVersionStore(store=store, tier0=None)
        assert len(g.history()) == 1, "不应产生补齐版本"

    def test_tier0_empty_is_legal(self):
        """未采集时为空 dict 是合法状态，不是错误。"""
        g = GenomeVersionStore()
        assert g.tier0_frozen() == {}
        g.activate("p1", {"tool_set": ["read"]})
        assert g.tier0_frozen() == {}

    def test_explicit_initial_keeps_its_own_tier0(self, tier0):
        init = GenomeVersion(
            version_id="gv-custom",
            tier0_frozen={"custom": {"x": 1}},
            activated_at="2026-01-01T00:00:00Z",
        )
        g = GenomeVersionStore(initial=init, tier0=tier0)
        # 显式 initial 优先——tier0 参数只用于自动建 gv-0 的场景
        assert g.tier0_frozen() == {"custom": {"x": 1}}


# ── 缺口 2：Tier 2 研究产物 ─────────────────────────────────────────


class TestTier2WritePath:
    def test_set_tier2_creates_new_version(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        before = g.current_version_id()
        vid = g.set_tier2("strategy.py:s1", "sha256:abc", by="runner")

        assert vid != before
        assert g.current_version_id() == vid
        assert g.tier2_self_modifiable() == {"strategy.py:s1": "sha256:abc"}

    def test_tier2_accumulates(self):
        g = GenomeVersionStore()
        g.set_tier2("a", "1")
        g.set_tier2("b", "2")
        assert g.tier2_self_modifiable() == {"a": "1", "b": "2"}

    def test_tier2_records_writer(self):
        g = GenomeVersionStore()
        g.set_tier2("a", "1", by="study-runner")
        assert g.current().activated_by_proposal == "tier2:study-runner"

    def test_tier2_default_writer(self):
        g = GenomeVersionStore()
        g.set_tier2("a", "1")
        assert g.current().activated_by_proposal == "tier2:research-domain"

    def test_tier2_does_not_touch_tier1(self):
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"]})
        g.set_tier2("a", "1")
        assert g.tier1_active() == {"tool_set": ["read"]}

    def test_tier1_does_not_touch_tier2(self):
        g = GenomeVersionStore()
        g.set_tier2("a", "1")
        g.activate("p1", {"tool_set": ["read"]})
        assert g.tier2_self_modifiable() == {"a": "1"}

    def test_tier2_does_not_touch_tier0(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.set_tier2("a", "1")
        assert g.tier0_frozen() == tier0

    def test_tier2_persists(self, store):
        g = GenomeVersionStore(store=store)
        g.set_tier2("strategy.py:s1", "sha256:xyz", by="runner")

        g2 = GenomeVersionStore(store=store)
        assert g2.tier2_self_modifiable() == {"strategy.py:s1": "sha256:xyz"}

    def test_tier2_value_can_be_structured(self):
        g = GenomeVersionStore()
        g.set_tier2("s", {"path": "a.py", "hash": "abc", "metrics": {"ic": 0.05}})
        assert g.tier2_self_modifiable()["s"]["metrics"]["ic"] == 0.05


# ── 缺口 3：deactivate 单组件撤销 ───────────────────────────────────


class TestDeactivate:
    def test_removes_single_component(self):
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"], "prompt": {"chat": {"v": 1}}})
        g.deactivate("tool_set")

        assert "tool_set" not in g.tier1_active()
        assert g.tier1_active()["prompt"] == {"chat": {"v": 1}}

    def test_creates_new_version_not_inplace(self):
        """撤销也是一次可追溯的状态变更，必须留痕。"""
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"]})
        before_id = g.current_version_id()
        before_hist = len(g.history())

        g.deactivate("tool_set")

        assert g.current_version_id() != before_id
        assert len(g.history()) == before_hist + 1
        assert g.current().activated_by_proposal == "deactivate:tool_set"

    def test_idempotent_for_missing_component(self):
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"]})
        before = g.current_version_id()
        after = g.deactivate("nonexistent")
        assert before == after, "撤销不存在的组件不应产生新版本"

    def test_preserves_tier0(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.activate("p1", {"tool_set": ["read"]})
        g.deactivate("tool_set")
        assert g.tier0_frozen() == tier0

    def test_preserves_tier2(self):
        g = GenomeVersionStore()
        g.set_tier2("a", "1")
        g.activate("p1", {"tool_set": ["read"]})
        g.deactivate("tool_set")
        assert g.tier2_self_modifiable() == {"a": "1"}

    def test_deactivate_then_reapply(self):
        """撤销后重新提案同一组件应正常工作。"""
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"]})
        g.deactivate("tool_set")
        assert g.tier1_active() == {}

        g.activate("p2", {"tool_set": ["write"]})
        assert g.tier1_active() == {"tool_set": ["write"]}

    def test_deactivate_persists(self, store):
        g = GenomeVersionStore(store=store)
        g.activate("p1", {"tool_set": ["read"], "prompt": {"x": 1}})
        g.deactivate("tool_set")

        g2 = GenomeVersionStore(store=store)
        assert g2.tier1_active() == {"prompt": {"x": 1}}

    def test_deactivate_all_components(self):
        g = GenomeVersionStore()
        g.activate("p1", {"tool_set": ["read"], "prompt": {"x": 1}, "skill": ["s"]})
        for c in ("tool_set", "prompt", "skill"):
            g.deactivate(c)
        assert g.tier1_active() == {}


# ── 三层独立性（交叉验证） ──────────────────────────────────────────


class TestTierIndependence:
    def test_all_three_layers_coexist(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        g.activate("p1", {"tool_set": ["read"]})
        g.set_tier2("strategy.py:s", "hash")

        cur = g.current()
        assert cur.tier0_frozen == tier0
        assert cur.tier1_active == {"tool_set": ["read"]}
        assert cur.tier2_self_modifiable == {"strategy.py:s": "hash"}

    def test_each_operation_only_touches_its_layer(self, tier0):
        g = GenomeVersionStore(tier0=tier0)

        # activate 只动 tier1
        g.activate("p1", {"tool_set": ["read"]})
        assert g.tier0_frozen() == tier0 and g.tier2_self_modifiable() == {}

        # set_tier2 只动 tier2
        g.set_tier2("a", "1")
        assert g.tier0_frozen() == tier0 and g.tier1_active() == {"tool_set": ["read"]}

        # deactivate 只动 tier1
        g.deactivate("tool_set")
        assert g.tier0_frozen() == tier0 and g.tier2_self_modifiable() == {"a": "1"}

    def test_rollback_restores_all_layers_together(self, tier0):
        g = GenomeVersionStore(tier0=tier0)
        v1 = g.activate("p1", {"tool_set": ["read"]})
        g.set_tier2("a", "1")
        g.deactivate("tool_set")

        g.rollback_to(v1)
        assert g.tier1_active() == {"tool_set": ["read"]}
        assert g.tier2_self_modifiable() == {}
        assert g.tier0_frozen() == tier0
