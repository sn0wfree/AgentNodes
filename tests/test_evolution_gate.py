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

from agentnodes.kernel.evolution import (
    COST_DEGRADATION_THRESHOLD,
    DEGRADATION_THRESHOLD,
    EVOLUTION_TOOLS,
    MIN_COST_SAMPLES,
    ROLLBACK_AFTER_FAILURES,
    EvolutionAgent,
    GenomeProposal,
    HoldoutSet,
    ProposalStatus,
    ReplayOutcome,
    TierViolation,
    Trajectory,
    ValidationGate,
    resolve_harness,
)
from agentnodes.kernel.evolution.genome_proposals import (
    TIER0_COMPONENTS,
    TIER1_COMPONENTS,
)
from agentnodes.kernel.orchestration.genome_version import (
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
    # C9（批次 3）：双集双门。held-out 侧复用**本次** held-in 的读数，
    # 于是两侧判定永远一致——本文件只测 held-in 机制（含 `metric_fn`
    # 是单次迭代器这种用法），双集/分歧的专门用例在 test_golden_sets.py。
    last: dict[str, float] = {}

    def _heldin(proposal, holdout):
        value = float(metric_fn())
        last["metric"] = value
        return value

    return ValidationGate(
        genome=genome,
        emit=emit,
        replay=_heldin,
        baseline_metric=lambda: baseline,
        heldout_replay=lambda proposal, holdout: last.get("metric", baseline),
        heldout_baseline=lambda: baseline,
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

    def test_rollback_does_not_discard_approved_version(self):
        """F2 回归（评审 C13）：三连败**不得**回退掉上一个已批准的好版本。

        旧实现 ``_rollback`` 调 ``genome.rollback()`` = 盲目 ``cursor−1``。
        由于 ``activate()`` 只在验证通过时调用，历史链上从来没有「候选的
        临时版本」，所以 ``cursor−1`` 退掉的是**已批准的合法版本**——
        「候选失败 ⇒ 当前基因组退化」这个推断根本不成立。

        实测（修复前）：``gv-c7a08b59 {'prompt': {'v': 1}}`` → ``gv-0 {}``。
        """
        genome = GenomeVersionStore()
        # 先造一个「已批准的好版本」
        genome.activate("good-1", {"prompt": {"v": 1}})
        good_version = genome.current_version_id()
        good_tier1 = dict(genome.tier1_active())
        assert good_version != "gv-0"

        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 0.1)  # 远低于基线 → 必定失败
        results = [
            gate.validate(
                GenomeProposal.new("tool_set", {"tools": ["read"]}), _holdout()
            )
            for _ in range(ROLLBACK_AFTER_FAILURES)
        ]

        assert results[-1].status is ProposalStatus.ROLLED_BACK
        # 核心断言：仍停在好版本上，tier1 一字未动
        assert genome.current_version_id() == good_version, (
            f"F2 未修：好版本 {good_version} 被回退成 "
            f"{genome.current_version_id()}"
        )
        assert dict(genome.tier1_active()) == good_tier1, "F2 未修：tier1 被清空"

        # 回滚事件必须带上锚，便于审计
        events = emit.of("genome.rollback")
        assert len(events) == 1
        assert events[0]["anchor_version"] == good_version
        assert events[0]["new_version_id"] == good_version

    def test_anchor_is_recorded_per_validate_call(self):
        """锚是**每次 validate() 入口**重记的，不是整个 gate 生命周期一份。

        这是有意的语义：锚回答的是「**这次**验证开始前的版本是什么」。
        如果做成一次性锚，第二次之后的验证就失去意义（回滚可能退到很久
        以前的版本）。这条测试把该语义钉住，防止将来「优化」成复用锚。
        """
        genome = GenomeVersionStore()
        genome.activate("good-1", {"prompt": {"v": 1}})
        v1 = genome.current_version_id()

        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 0.1)

        gate.validate(GenomeProposal.new("prompt", {"v": 2}), _holdout())
        assert gate._anchor_version == v1

        # 外部升版后，下一次 validate 的锚应当是**新版本**，不是 v1
        genome.activate("good-2", {"prompt": {"v": 2}})
        v2 = genome.current_version_id()
        assert v2 != v1

        gate.validate(GenomeProposal.new("prompt", {"v": 3}), _holdout())
        assert gate._anchor_version == v2, (
            "锚应在每次 validate() 入口按当时的生效版本重记"
        )

    def test_rollback_reports_anchor_in_event(self):
        """回滚事件必须带 anchor_version，便于审计「回的是哪个点」。"""
        genome = GenomeVersionStore()
        genome.activate("good-1", {"prompt": {"v": 1}})
        anchor = genome.current_version_id()

        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 0.1)
        for i in range(ROLLBACK_AFTER_FAILURES):
            gate.validate(GenomeProposal.new("prompt", {"v": i}), _holdout())

        events = emit.of("genome.rollback")
        assert len(events) == 1
        assert events[0]["anchor_version"] == anchor
        assert events[0]["new_version_id"] == anchor


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


# ── 双闸：成本（D3，2026-11-06）───────────────────────────────────────


def _cost_gate(genome, emit, cand: ReplayOutcome, base: ReplayOutcome):
    # C9：held-out 侧沿用同一对桩值，保证成本闸用例只考察成本语义。
    return ValidationGate(
        genome=genome,
        emit=emit,
        replay=lambda proposal, holdout: cand,
        baseline_metric=lambda: base,
        heldout_replay=lambda proposal, holdout: cand.metric,
        heldout_baseline=lambda: base.metric,
    )


def _validate(gate, proposal_id: str = "p") -> tuple:
    """跑一次 validate，返回 ``(proposal, ValidationResult)``。

    ``validate()`` 的返回值是推进后的提案；判定细节在 ``gate.history[-1]``。
    """
    out = gate.validate(GenomeProposal.new("prompt", {"a": proposal_id}), _holdout())
    return out, gate.history[-1]


class TestReplayOutcomeCompat:
    """交付 2：float 向后兼容（现存 ~12 个测试桩零改动）。"""

    def test_bare_float_still_accepted(self):
        out = _gate(GenomeVersionStore(), _Emitter(), lambda: 1.0).validate(
            GenomeProposal.new("prompt", {"a": 1}), _holdout()
        )
        assert out.status is ProposalStatus.APPROVED

    def test_float_means_no_cost_samples(self):
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _gate(genome, emit, lambda: 1.0)
        gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        verdict = gate.history[-1]
        assert verdict.cost_checked is False
        # 无样本 → 跳过并留痕，不装 0 分、不误判
        assert emit.of("genome.cost_gate")[0]["verdict"] == "skipped"

    def test_outcome_baseline_also_accepted(self):
        out = _cost_gate(
            GenomeVersionStore(), _Emitter(),
            cand=ReplayOutcome(metric=1.0, cost=0.9, cost_samples=5),
            base=ReplayOutcome(metric=1.0, cost=0.9, cost_samples=5),
        ).validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED


class TestCostGate:
    """交付 3 探针 ③：双闸各自独立触发。"""

    def test_probe3_cost_degrades_while_metric_flat_rejected(self):
        """成本退化但能力不变 → 硬拒（能力闸本身会放行）。"""
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _cost_gate(
            genome, emit,
            cand=ReplayOutcome(metric=1.0, cost=0.5, cost_samples=6),
            base=ReplayOutcome(metric=1.0, cost=1.0, cost_samples=6),
        )
        out, verdict = _validate(gate)
        assert out.status is ProposalStatus.REJECTED
        assert "cost degraded 50.0%" in out.rejection_reason
        assert "hard reject" in out.rejection_reason
        assert genome.current_version_id() == "gv-0", "硬拒不得升版"
        assert emit.of("genome.cost_gate")[0]["verdict"] == "rejected"
        assert verdict.degradation == 0.0, "能力闸读数必须仍是 0（两闸独立）"

    def test_cost_within_threshold_passes(self):
        out = _cost_gate(
            GenomeVersionStore(), _Emitter(),
            cand=ReplayOutcome(metric=1.0, cost=0.9, cost_samples=6),
            base=ReplayOutcome(metric=1.0, cost=1.0, cost_samples=6),
        ).validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED

    def test_ability_improves_but_cost_spikes_escalates(self):
        """能力升 + 成本涨超 tradeshold → escalate（不静默通过、不计回滚）。"""
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _cost_gate(
            genome, emit,
            cand=ReplayOutcome(metric=1.05, cost=0.8, cost_samples=6),
            base=ReplayOutcome(metric=1.0, cost=1.0, cost_samples=6),
        )
        out, verdict = _validate(gate)
        assert out.status is ProposalStatus.REJECTED
        assert verdict.escalated is True
        assert "cost rose" in out.rejection_reason
        assert gate.consecutive_failures == 0, "escalate 不计入回滚计数"
        assert genome.current_version_id() == "gv-0"
        assert emit.of("genome.cost_gate")[0]["verdict"] == "escalated"

    def test_ability_gate_unchanged_threshold(self):
        assert DEGRADATION_THRESHOLD == 0.20
        assert COST_DEGRADATION_THRESHOLD == 0.30

    def test_cost_improvement_passes(self):
        out = _cost_gate(
            GenomeVersionStore(), _Emitter(),
            cand=ReplayOutcome(metric=1.0, cost=1.0, cost_samples=6),
            base=ReplayOutcome(metric=1.0, cost=0.5, cost_samples=6),
        ).validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED


class TestNoMixedMean:
    """禁混合均值：cost(0..1) 与绝对 tokens 是两个独立维度。"""

    def test_probe2_tokens_drive_gate_when_cost_is_none(self):
        """探针 ②：无预算任务 → cost=None，token 绝对量独立过闸。

        token 翻倍（+100%）必须被拒——即便 cost 侧**完全没样本**。
        若把绝对 tokens 混进 cost 均值（或把 None 装成 0），这条退化就
        会被稀释/伪造掉，这正是评审 P 条的 90% 盲区。
        """
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _cost_gate(
            genome, emit,
            cand=ReplayOutcome(metric=1.0, cost=None, tokens=8000.0, tokens_samples=8),
            base=ReplayOutcome(metric=1.0, cost=None, tokens=4000.0, tokens_samples=8),
        )
        out, verdict = _validate(gate)
        assert out.status is ProposalStatus.REJECTED
        assert "tokens degraded 100.0%" in out.rejection_reason
        assert verdict.cost_degradation is None, "cost 侧无样本，不得伪造 0"
        assert verdict.tokens_degradation == pytest.approx(1.0)
        assert emit.of("genome.cost_gate")[0]["dimension"] == "tokens"

    def test_tokens_do_not_enter_cost_mean(self):
        """tokens 的巨幅变化不改变 cost 维度读数（两均值物理隔离）。"""
        gate = _cost_gate(
            GenomeVersionStore(), _Emitter(),
            cand=ReplayOutcome(
                metric=1.0, cost=0.95, cost_samples=5,
                tokens=9000.0, tokens_samples=5,
            ),
            base=ReplayOutcome(
                metric=1.0, cost=1.0, cost_samples=5,
                tokens=1000.0, tokens_samples=5,
            ),
        )
        out, verdict = _validate(gate)
        # tokens +800% 触发硬拒，但被判维度必须是 tokens，cost 读数独立
        assert out.status is ProposalStatus.REJECTED
        assert "tokens degraded 800.0%" in out.rejection_reason
        assert verdict.cost_degradation == pytest.approx(0.05), "cost 只反映自身均值"

    def test_no_cost_samples_skips_gate_not_zero(self):
        """两侧 cost 均 None → 跳过 + emit，不装 0 分（不误拒也不误放）。"""
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _cost_gate(
            genome, emit,
            cand=ReplayOutcome(metric=1.0, cost=None),
            base=ReplayOutcome(metric=1.0, cost=None),
        )
        out, verdict = _validate(gate)
        assert out.status is ProposalStatus.APPROVED
        assert verdict.cost_checked is False
        ev = emit.of("genome.cost_gate")
        assert ev and ev[0]["verdict"] == "skipped"

    def test_insufficient_samples_escalates(self):
        """有效样本 < 最小值 → 降级 escalate，而不是静默放行。"""
        genome = GenomeVersionStore()
        emit = _Emitter()
        gate = _cost_gate(
            genome, emit,
            cand=ReplayOutcome(metric=1.0, cost=0.5, cost_samples=2),
            base=ReplayOutcome(metric=1.0, cost=1.0, cost_samples=2),
        )
        out, verdict = _validate(gate)
        assert out.status is ProposalStatus.REJECTED
        assert verdict.escalated is True
        assert "insufficient cost samples" in out.rejection_reason
        assert MIN_COST_SAMPLES == 3
        assert emit.of("genome.cost_gate")[0]["verdict"] == "insufficient_samples"
        assert gate.consecutive_failures == 0, "escalate 不计入回滚计数"


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

    # ── F1 回归：tool_set 形状契约（评审 F1 / 2026-11-06）────────────
    #
    # 病理：``default_policy`` 产出 ``{"tools": [...]}``，``activate()`` 把
    # change 原样写进 ``tier1_active["tool_set"]``，旧 resolver 直接
    # ``frozenset(那个 dict)`` → ``{'tools'}`` → ``apply_tool_set`` 交集后
    # **工具清零**。修法是两侧对齐：产出侧用 ``_tool_set_change``，
    # 读取侧用 ``_normalise_tool_set``。

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"tools": ["read", "list"]}, frozenset({"read", "list"})),
            ({"tools": None}, None),
            (None, None),
            (["read", "write"], frozenset({"read", "write"})),
            (("read",), frozenset({"read"})),
            (frozenset({"read"}), frozenset({"read"})),
            ("read", frozenset({"read"})),
            ({"no_tools_key": 1}, None),
            (123, None),  # 无法识别的形状 → 放开而非清空
        ],
    )
    def test_tool_set_shape_normalisation(self, raw, expected):
        """读取侧接受历史 dataset 的所有形状，绝不退化成 {'tools'}。"""
        from agentnodes.kernel.evolution.skill_memory import _normalise_tool_set

        assert _normalise_tool_set(raw) == expected

    def test_proposal_tool_set_does_not_wipe_tools(self):
        """端到端：提案通过 → activate → resolver 读回 → 工具不被清零。"""
        g = GenomeVersionStore()
        gate = _gate(g, _Emitter(), lambda: 0.9)  # 高通过率 → approved
        out = gate.validate(
            GenomeProposal.new(
                "tool_set", {"tools": ["read", "list", "write"]}
            ),
            _holdout(),
        )
        assert out.status is ProposalStatus.APPROVED

        cfg = resolve_harness("sig", source=g)
        assert cfg.tool_set == frozenset({"read", "list", "write"})

        # 模拟 apply_tool_set 的交集：必须保留工具
        available = ["read", "list", "write", "shell"]
        filtered = [t for t in available if t in cfg.tool_set]
        assert filtered == ["read", "list", "write"], (
            f"F1 未修：工具被清零 -> {filtered}"
        )

    def test_default_policy_produces_readable_shape(self):
        """产出侧：default_policy 的 change 必须能被 resolver 读回。"""
        from agentnodes.kernel.evolution.default_policy import _tool_set_change

        narrow = _tool_set_change(["read", "list"])
        assert narrow == {"tools": ["read", "list"]}
        from agentnodes.kernel.evolution.skill_memory import _normalise_tool_set

        assert _normalise_tool_set(narrow) == frozenset({"read", "list"})

        unrestricted = _tool_set_change(None)
        assert unrestricted == {"tools": None}
        assert _normalise_tool_set(unrestricted) is None

    def test_legacy_bad_shape_on_disk_is_readable(self):
        """已入库的老 genome（坏形状）升级后不得把工具清空。"""
        src = _Tier1({"tool_set": {"tools": ["read", "search"]}})
        cfg = resolve_harness("sig", source=src)
        assert cfg.tool_set == frozenset({"read", "search"}), (
            "老数据的 {'tools': [...]} 形状必须仍可读"
        )
        # 反向：绝不能是 {'tools'} 这种把 key 当工具名的结果
        assert cfg.tool_set != frozenset({"tools"})


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
