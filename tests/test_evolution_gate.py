"""阶段三验收 ②：EvolutionAgent + validation_gate + genome_version（§4.10）。

覆盖 goal §3 阶段三的完成标准：
- EvolutionAgent 工具集只含 §4.3 那五个，且经 CapabilityRegistry.resolve
- target_component ∈ Tier 0 → validation_gate 拒绝
- holdout 通过 → approved，genome_version 递增
- 连续 3 次失败 → rolled_back + genome.rollback 事件
- SkillMemoryResolver 装配期解析，无 Tier 0 泄漏
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.evolution import (
    DEGRADATION_THRESHOLD,
    EVOLUTION_TOOLS,
    EvolutionAgent,
    GenomeProposal,
    HoldoutSet,
    ProposalStatus,
    ROLLBACK_AFTER_FAILURES,
    TierViolation,
    Trajectory,
    ValidationGate,
    resolve_harness,
)
from strategy_research.apps.evolution.genome_proposals import (
    TIER0_COMPONENTS,
    TIER1_COMPONENTS,
)
from strategy_research.kernel.orchestration.genome_version import (
    GenomeVersion,
    GenomeVersionStore,
)


# ── 测试替身 ─────────────────────────────────────────────────────────


class _Emitter:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def _holdout(n: int = 3) -> HoldoutSet:
    trajs = tuple(
        Trajectory(
            event_id=f"e{i}", assignment_id=f"a{i}", ticket_id=f"t{i}",
            run_id=f"r{i}", status="failed", ts=f"2026-01-0{i+1}T00:00:00Z",
        )
        for i in range(n)
    )
    return HoldoutSet(trajectories=trajs, seed=42, task_signature="sig")


def _gate(genome, emit, metric_fn, baseline=1.0):
    return ValidationGate(
        genome=genome,
        emit=emit,
        replay=lambda proposal, holdout: metric_fn(),
        baseline_metric=lambda: baseline,
    )


# ── Tier 划分 ────────────────────────────────────────────────────────


class TestTierEnforcement:
    def test_tier0_rejected_at_construction(self):
        """构造时就拒 Tier 0（最靠近源头）。"""
        for comp in TIER0_COMPONENTS:
            with pytest.raises(TierViolation, match="Tier 0"):
                GenomeProposal.new(comp, {"x": 1})

    def test_unknown_component_rejected(self):
        with pytest.raises(TierViolation, match="unknown target_component"):
            GenomeProposal.new("made_up_component", {})

    def test_tier1_accepted(self):
        for comp in TIER1_COMPONENTS:
            p = GenomeProposal.new(comp, {"k": "v"})
            assert p.status is ProposalStatus.DRAFT
            assert p.target_component == comp

    def test_gate_rejects_tier0_even_if_constructed_directly(self):
        """绕过 new() 用 dataclass 构造的野生对象，gate 仍必须拒。"""
        wild = GenomeProposal(
            proposal_id="gpr-x",
            target_component="budget",
            change={"max_tokens": 999999},
        )
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 1.0)
        out = gate.validate(wild, _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "Tier 0" in out.rejection_reason
        assert genome.current_version_id() == "gv-0", "拒绝不得升版"

    def test_gate_rejects_tier0_of_all_four(self):
        genome = GenomeVersionStore()
        gate = _gate(genome, _Emitter(), lambda: 1.0)
        for comp in TIER0_COMPONENTS:
            wild = GenomeProposal(proposal_id=f"g-{comp}", target_component=comp, change={})
            out = gate.validate(wild, _holdout())
            assert out.status is ProposalStatus.REJECTED


# ── 验证通过/拒绝 ────────────────────────────────────────────────────


class TestValidationFlow:
    def test_pass_approves_and_bumps_version(self):
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 1.0)
        p = GenomeProposal.new("prompt", {"researcher": "v2"})
        out = gate.validate(p, _holdout())
        assert out.status is ProposalStatus.APPROVED
        assert out.validated_at is not None
        assert genome.current_version_id() != "gv-0"
        assert genome.current().activated_by_proposal == p.proposal_id
        assert genome.tier1_active()["prompt"] == {"researcher": "v2"}

    def test_degradation_within_threshold_passes(self):
        genome = GenomeVersionStore()
        gate = _gate(genome, _Emitter(), lambda: 1.0 - DEGRADATION_THRESHOLD / 2)
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED

    def test_degradation_over_threshold_rejected(self):
        genome = GenomeVersionStore()
        gate = _gate(genome, _Emitter(), lambda: 1.0 - DEGRADATION_THRESHOLD * 2)
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "degraded" in out.rejection_reason
        assert genome.current_version_id() == "gv-0"

    def test_implausible_improvement_escalates(self):
        """指标暴涨可能是奖励黑客信号 → 不静默放行，走 escalation。"""
        genome = GenomeVersionStore()
        gate = _gate(genome, _Emitter(), lambda: 5.0)
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "reward hacking" in out.rejection_reason

    def test_no_baseline_does_not_silently_pass(self):
        genome = GenomeVersionStore()
        gate = _gate(genome, _Emitter(), lambda: 1.0, baseline=None)
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "no baseline" in out.rejection_reason


# ── 回滚 ─────────────────────────────────────────────────────────────


class TestRollback:
    def test_three_consecutive_failures_rollback(self):
        """连续 3 次失败 → rolled_back + genome.rollback 事件（§4.8）。"""
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 0.1)  # 远低于基线
        results = [
            gate.validate(GenomeProposal.new("prompt", {"v": i}), _holdout())
            for i in range(ROLLBACK_AFTER_FAILURES)
        ]
        assert results[-1].status is ProposalStatus.ROLLED_BACK
        events = emit.of("genome.rollback")
        assert len(events) == 1
        assert events[0]["consecutive_failures"] == ROLLBACK_AFTER_FAILURES
        assert "rolled back" in events[0]["reason"]

    def test_success_resets_failure_counter(self):
        genome = GenomeVersionStore()
        emit = _Emitter()
        metrics = [0.1, 0.1, 1.0, 0.1, 0.1]  # 中间成功一次
        it = iter(metrics)
        gate = _gate(genome, emit, lambda: next(it))
        statuses = [
            gate.validate(GenomeProposal.new("prompt", {"v": i}), _holdout()).status
            for i in range(len(metrics))
        ]
        assert ProposalStatus.ROLLED_BACK not in statuses, (
            "成功一次后计数必须归零，不应触发回滚"
        )
        assert emit.of("genome.rollback") == []

    def test_rollback_returns_previous_version(self):
        genome = GenomeVersionStore()
        v0 = genome.current_version_id()
        genome.activate("p1", {"prompt": {"a": 1}})
        v1 = genome.current_version_id()
        assert v1 != v0
        assert genome.rollback() == v0

    def test_rollback_at_genesis_is_idempotent(self):
        genome = GenomeVersionStore()
        v0 = genome.current_version_id()
        assert genome.rollback() == v0
        assert genome.rollback() == v0


# ── genome_version 层 ────────────────────────────────────────────────


class TestGenomeVersionStore:
    def test_tier0_frozen_inherited_and_unsettable(self):
        """tier0_frozen 只能继承，没有修改入口（H3 实现层）。"""
        g = GenomeVersionStore(
            GenomeVersion(version_id="gv-x", tier0_frozen={"budget": {"max": 100}})
        )
        g.activate("p1", {"prompt": {"a": 1}})
        assert g.current().tier0_frozen == {"budget": {"max": 100}}
        assert not hasattr(g, "set_tier0"), "不得暴露改 Tier 0 的入口"

    def test_activate_merges_tier1(self):
        g = GenomeVersionStore()
        g.activate("p1", {"prompt": {"a": 1}})
        g.activate("p2", {"skill": {"b": 2}})
        t1 = g.tier1_active()
        assert t1["prompt"] == {"a": 1}, "前一次生效项应保留"
        assert t1["skill"] == {"b": 2}

    def test_rollback_to_specific_version(self):
        g = GenomeVersionStore()
        v0 = g.current_version_id()
        g.activate("p1", {"prompt": {"a": 1}})
        g.activate("p2", {"prompt": {"a": 2}})
        assert g.rollback_to(v0) == v0
        assert g.tier1_active() == {}

    def test_rollback_to_unknown_raises(self):
        g = GenomeVersionStore()
        with pytest.raises(KeyError):
            g.rollback_to("gv-nope")


# ── SkillMemoryResolver ──────────────────────────────────────────────


class _Tier1:
    def __init__(self, d):
        self._d = d

    def tier1_active(self):
        return self._d


class TestSkillMemoryResolver:
    def test_resolves_tier1(self):
        src = _Tier1({
            "prompt": {"researcher": "v2"},
            "tool_set": ["a", "b"],
            "compaction": {"threshold": 0.8},
        })
        cfg = resolve_harness("sig", source=src)
        assert cfg.prompt_version == {"researcher": "v2"}
        assert cfg.tool_set == frozenset({"a", "b"})
        assert cfg.compaction_config == {"threshold": 0.8}

    def test_drops_tier0_leak(self):
        """即便上游错误地把 Tier 0 混进 tier1，resolver 也丢弃（H3）。"""
        src = _Tier1({"budget": {"max": 999}, "prompt": {"a": 1}})
        cfg = resolve_harness("sig", source=src)
        assert cfg.prompt_version == {"a": 1}
        assert "budget" not in cfg.prompt_version

    def test_empty_without_source(self):
        cfg = resolve_harness("sig")
        assert cfg.is_empty()
        assert cfg.task_signature == "sig"

    def test_integrates_with_store(self):
        g = GenomeVersionStore()
        g.activate("p1", {"prompt": {"r": "v2"}})
        cfg = resolve_harness("sig", source=g)
        assert cfg.prompt_version == {"r": "v2"}


# ── EvolutionAgent ───────────────────────────────────────────────────


class _Journal:
    def read_journal(self, *, limit=50):
        return [{"id": "j1"}]


class _Scoreboard:
    def read_scoreboard(self):
        return {"lever_a": 1.0}


class _Validation:
    def read_validation(self, *, limit=50):
        return [{"ok": True}]


class _Holdout:
    def read_holdout(self, sig, seed):
        return _holdout()


def _agent(policy=None, tools=None):
    return EvolutionAgent(
        journal=_Journal(),
        scoreboard=_Scoreboard(),
        validation=_Validation(),
        holdout=_Holdout(),
        policy=policy,
        resolved_tools=tools or {},
    )


class TestEvolutionAgent:
    def test_tool_whitelist_is_exactly_five(self):
        assert EVOLUTION_TOOLS == {
            "read_journal", "read_scoreboard", "read_validation",
            "read_holdout", "propose_genome_update",
        }

    def test_rejects_missing_tool(self):
        with pytest.raises(ValueError, match="missing required tools"):
            _agent(tools={"read_journal": lambda: None})

    def test_rejects_extra_tool(self):
        tools = {name: (lambda: None) for name in EVOLUTION_TOOLS}
        tools["write_config"] = lambda: None
        with pytest.raises(ValueError, match="outside the whitelist"):
            _agent(tools=tools)

    def test_no_policy_returns_none(self):
        assert _agent().propose(
            objective="o", role="r", tool_set=frozenset({"a"}), seed=1
        ) is None

    def test_policy_can_produce_proposal(self):
        def policy(ctx):
            return GenomeProposal.new("prompt", {"r": "v2"}, evidence_refs=["j1"])

        out = _agent(policy=policy).propose(
            objective="o", role="r", tool_set=frozenset({"a"}), seed=1
        )
        assert out is not None
        assert out.target_component == "prompt"
        assert out.evidence_refs == ["j1"]

    def test_policy_producing_tier0_is_blocked(self):
        """policy 若产出越权提案，agent 本地就拦住。"""
        def policy(ctx):
            return GenomeProposal(
                proposal_id="bad", target_component="budget", change={}
            )

        with pytest.raises(TierViolation, match="out-of-whitelist"):
            _agent(policy=policy).propose(
                objective="o", role="r", tool_set=frozenset({"a"}), seed=1
            )

    def test_context_carries_holdout_and_signature(self):
        seen = {}

        def policy(ctx):
            seen["sig"] = ctx.task_signature
            seen["n"] = len(ctx.holdout)
            seen["rate"] = ctx.failure_rate
            return None

        _agent(policy=policy).propose(
            objective="o", role="r", tool_set=frozenset({"a"}), seed=1
        )
        assert seen["n"] == 3
        assert seen["rate"] == 1.0, "全部 failed 的 holdout 失败率应为 1.0"


# ── 回归：阶段二成果保持 ─────────────────────────────────────────────


class TestNoNewLayerViolation:
    def test_l3l2_still_zero(self):
        """阶段三不引入新的 L3→L2（goal H2）。"""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        import test_layer_matrix as lm

        viol, _ = lm._collect()
        assert viol.get("L3>L2", 0) == 0
