"""阶段三验收 ③：EvolutionAgent 五个工具经 CapabilityRegistry 解析并可工作。

补 goal §3 阶段三的完成标准：
    「EvolutionAgent 工具集只含 read_journal / read_scoreboard /
      read_validation / read_holdout / propose_genome_update，
      全部经 CapabilityRegistry.resolve 解析」

此前的 test_evolution_gate 只断言了白名单字面量，没有断言「真能 resolve」。
本文件补上后者——这是验证器指出的实际缺口。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.apps.evolution import (
    EVOLUTION_TOOLS,
    EvolutionAgent,
    GenomeProposal,
    HoldoutSet,
    TierViolation,
    Trajectory,
)
from agentnodes.apps.evolution.tools import TOOL_IMPLS
from agentnodes.kernel.agent.capability import (
    CapabilityNotFound,
    get_default_registry,
    reset_default_registry,
)


# ── 数据源替身 ───────────────────────────────────────────────────────


class _Journal:
    def read_journal(self, *, limit=50):
        return [{"id": "j1", "text": "发现动量衰减"}]


class _Scoreboard:
    def read_scoreboard(self):
        return {"lever_a": 1.5}


class _Validation:
    def read_validation(self, *, limit=50):
        return [{"proposal": "p1", "ok": True}]


class _Holdout:
    def read_holdout(self, sig, seed):
        return HoldoutSet(
            trajectories=(
                Trajectory(
                    event_id="e1", assignment_id="a1", ticket_id="t1",
                    run_id="r1", status="failed", ts="2026-01-01T00:00:00Z",
                ),
            ),
            seed=seed,
            task_signature=sig,
        )


def _sources():
    return dict(
        journal=_Journal(),
        scoreboard=_Scoreboard(),
        validation=_Validation(),
        holdout=_Holdout(),
    )


# ── 注册完整性 ───────────────────────────────────────────────────────


class TestToolKindRegistration:
    def test_all_five_kinds_registered(self):
        """每个工具名都有对应的 evolution.<name> kind。"""
        import agentnodes.apps  # noqa: F401 —— 触发注册副作用

        reg = get_default_registry()
        missing = [
            n for n in EVOLUTION_TOOLS if f"evolution.{n}" not in reg
        ]
        assert missing == [], f"未注册的工具 kind: {missing}"

    def test_each_kind_resolves_to_the_impl(self):
        """resolve 出来的对象就是 tools 模块里的实现（不是空壳）。"""
        import agentnodes.apps  # noqa: F401

        reg = get_default_registry()
        for name in EVOLUTION_TOOLS:
            resolved = reg.resolve(f"evolution.{name}")
            assert resolved is TOOL_IMPLS[name], (
                f"{name} 解析结果与实现不一致"
            )

    def test_with_capabilities_survives_registry_reset(self):
        """reset_default_registry() 之后 with_capabilities 仍能自愈。"""
        reset_default_registry()
        agent = EvolutionAgent.with_capabilities(**_sources())
        assert set(agent.resolved_tools) == EVOLUTION_TOOLS

    def test_unknown_kind_raises(self):
        with pytest.raises(CapabilityNotFound):
            get_default_registry().resolve("evolution.no_such_tool")


# ── 工具行为 ─────────────────────────────────────────────────────────


class TestToolImpls:
    def test_read_journal(self):
        out = TOOL_IMPLS["read_journal"](_Journal(), limit=10)
        assert out == [{"id": "j1", "text": "发现动量衰减"}]

    def test_read_scoreboard(self):
        assert TOOL_IMPLS["read_scoreboard"](_Scoreboard()) == {"lever_a": 1.5}

    def test_read_validation(self):
        out = TOOL_IMPLS["read_validation"](_Validation(), limit=5)
        assert out == [{"proposal": "p1", "ok": True}]

    def test_read_holdout_accepts_holdout_source(self):
        """HoldoutSource 形态直接委托（不试图 iter_assignment_reports）。"""
        hs = TOOL_IMPLS["read_holdout"](_Holdout(), "sig", 42)
        assert len(hs) == 1 and hs.seed == 42

    def test_read_holdout_accepts_report_source(self):
        """ReportSource 形态走 holdout.sample()。"""

        class _Reports:
            def iter_assignment_reports(self):
                yield {
                    "id": "e1", "assignment_id": "a1", "ticket_id": "t",
                    "run_id": "r", "status": "failed",
                    "ts": "2026-01-01T00:00:00Z",
                    "task_signature": "sig", "payload": {},
                }

        hs = TOOL_IMPLS["read_holdout"](_Reports(), "sig", 42, limit=5)
        assert [t.assignment_id for t in hs] == ["a1"]

    def test_propose_genome_update_builds_draft(self):
        p = TOOL_IMPLS["propose_genome_update"](
            "prompt", {"r": "v2"}, evidence_refs=["j1"]
        )
        assert isinstance(p, GenomeProposal)
        assert p.status.value == "draft"
        assert p.evidence_refs == ["j1"]

    def test_propose_genome_update_rejects_tier0(self):
        with pytest.raises(TierViolation):
            TOOL_IMPLS["propose_genome_update"]("budget", {"max": 999})


# ── 端到端：propose 真的经工具取证据 ─────────────────────────────────


class TestProposeUsesTools:
    def test_evidence_flows_through_resolved_tools(self):
        """证据必须经解析出的工具获取，而不是绕过它直接读 source。

        用一个计数工具替换 read_journal，断言它被调用过。
        """
        calls: list[str] = []
        agent = EvolutionAgent.with_capabilities(**_sources())

        real = agent.resolved_tools["read_journal"]

        def spy_journal(source, *, limit=50):
            calls.append("read_journal")
            return real(source, limit=limit)

        agent.resolved_tools["read_journal"] = spy_journal
        agent.policy = lambda ctx: GenomeProposal.new("prompt", {"r": "v2"})
        agent.propose(objective="o", role="r", tool_set=frozenset({"a"}), seed=1)

        assert calls == ["read_journal"], "propose 必须经工具取证据"

    def test_all_four_read_tools_are_used(self):
        used: list[str] = []
        agent = EvolutionAgent.with_capabilities(**_sources())
        for name in ("read_journal", "read_scoreboard",
                     "read_validation", "read_holdout"):
            real = agent.resolved_tools[name]

            def make(n, fn):
                def wrapper(*a, **kw):
                    used.append(n)
                    return fn(*a, **kw)
                return wrapper

            agent.resolved_tools[name] = make(name, real)

        agent.policy = lambda ctx: None
        agent.propose(objective="o", role="r", tool_set=frozenset({"a"}), seed=1)

        assert set(used) == {
            "read_journal", "read_scoreboard", "read_validation", "read_holdout"
        }

    def test_falls_back_to_sources_without_registry(self):
        """未注入 resolved_tools 时直接走数据源（简单路径仍可用）。"""
        agent = EvolutionAgent(**_sources())
        seen = {}
        agent.policy = lambda ctx: seen.update(
            journal=ctx.journal, n=len(ctx.holdout)
        ) or None
        agent.propose(objective="o", role="r", tool_set=frozenset({"a"}), seed=1)
        assert seen["journal"] == [{"id": "j1", "text": "发现动量衰减"}]
        assert seen["n"] == 1

    def test_propose_passes_seed_into_holdout(self):
        """seed 必须真的传到 holdout 抽样（附录 C）。"""
        captured = {}

        class _H:
            def read_holdout(self, sig, seed):
                captured["seed"] = seed
                return HoldoutSet((), seed, sig)

        agent = EvolutionAgent(
            journal=_Journal(), scoreboard=_Scoreboard(),
            validation=_Validation(), holdout=_H(),
        )
        agent.policy = lambda ctx: None
        agent.propose(objective="o", role="r", tool_set=frozenset({"a"}), seed=1234)
        assert captured["seed"] == 1234
