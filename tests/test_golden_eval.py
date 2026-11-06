"""golden eval 作为 RSI 验证信号（固定抽样 + 提案应用 + 降级语义）。

覆盖用户裁决的三个要点：
- 固定抽样 10 条（成本可控、可比）
- 真实执行而非代理指标
- 基础设施失败降级为 skipped（不计入回滚计数）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agentnodes.evals.scorecard import GoldenTask, RunRecord  # noqa: E402
from agentnodes.kernel.evolution.genome_proposals import (  # noqa: E402
    GenomeProposal,
)
from agentnodes.kernel.evolution.golden_eval import (  # noqa: E402
    DEFAULT_SAMPLE_SIZE,
    make_golden_replay,
    run_golden_eval,
    select_tasks,
)
from agentnodes.kernel.evolution.holdout import HoldoutSet  # noqa: E402
from agentnodes.kernel.evolution.validation_gate import ReplayOutcome  # noqa: E402
from agentnodes.kernel.orchestration.genome_version import (  # noqa: E402
    GenomeVersionStore,
)


def _tasks(n: int = 30, groups: int = 5) -> list[GoldenTask]:
    return [
        GoldenTask(
            id=f"t{i}",
            prompt=f"task {i}",
            expected_tools=["read"],
            toolset=f"role{i % groups}",
        )
        for i in range(n)
    ]


def _perfect(task, ws, max_iter):
    return RunRecord(
        task_id=task.id, tools_used=list(task.expected_tools),
        finished_reason="stop", answer="ok", success=True,
    )


def _zero(task, ws, max_iter):
    return RunRecord(
        task_id=task.id, tools_used=[], finished_reason="error",
        answer="", success=False,
    )


def _boom(task, ws, max_iter):
    raise RuntimeError("LLM down")


# ── 抽样 ────────────────────────────────────────────────────────────


class TestSelectTasks:
    def test_deterministic_for_same_seed(self):
        tasks = _tasks()
        a = select_tasks(tasks, seed=42, size=10)
        b = select_tasks(tasks, seed=42, size=10)
        assert [t.id for t in a] == [t.id for t in b]

    def test_different_seed_differs(self):
        tasks = _tasks(50)
        a = {t.id for t in select_tasks(tasks, seed=1, size=10)}
        b = {t.id for t in select_tasks(tasks, seed=2, size=10)}
        assert a != b

    def test_respects_size(self):
        assert len(select_tasks(_tasks(), seed=1, size=7)) == 7

    def test_size_larger_than_input(self):
        tasks = _tasks(3)
        assert len(select_tasks(tasks, seed=1, size=10)) == 3

    def test_zero_or_negative_size(self):
        assert select_tasks(_tasks(), seed=1, size=0) == []
        assert select_tasks(_tasks(), seed=1, size=-5) == []

    def test_empty_input(self):
        assert select_tasks([], seed=1, size=10) == []

    def test_layered_coverage(self):
        """应该覆盖多个 toolset，而不是全撞同一类。"""
        picked = select_tasks(_tasks(30, groups=5), seed=42, size=5)
        assert len({t.toolset for t in picked}) >= 4

    def test_default_sample_size_is_ten(self):
        assert DEFAULT_SAMPLE_SIZE == 10


# ── 评测 ────────────────────────────────────────────────────────────


class TestRunGoldenEval:
    def test_perfect_run_gives_high_metric(self):
        r = run_golden_eval(_tasks(), seed=42, size=5, runner=_perfect)
        assert not r.skipped
        assert r.tasks_run == 5
        assert r.metric > 0.8

    def test_zero_run_gives_low_metric(self):
        """全零 run 的分数由 scorecard 的加权规则决定。

        scorecard 是加权和（tools 0.4 / 无违规 0.2 / 预算 0.15 /
        内容 0.15 / 完成 0.1）。全零时仍拿到 0.5 —— 因为"没违规"、
        "没超预算"、"内容检查（无 must_contain 要求）"这三项本来就是满分。
        这不是缺陷，是 scorecard 的既有口径；关键是它显著低于满分。
        """
        r = run_golden_eval(_tasks(), seed=42, size=5, runner=_zero)
        assert not r.skipped
        assert r.metric < 0.6
        assert r.metric < 0.85, "必须明显低于满分 run"

    def test_perfect_beats_zero(self):
        good = run_golden_eval(_tasks(), seed=42, size=5, runner=_perfect)
        bad = run_golden_eval(_tasks(), seed=42, size=5, runner=_zero)
        assert good.metric > bad.metric

    def test_all_errored_is_skipped_not_zero(self):
        """全部异常 = 基础设施故障，必须 skipped（不计回滚计数）。

        若返回 metric=0 且 skipped=False，验证闸会判为"退化 100%"，
        累积三次就把健康基因组回滚掉——一次网络故障足以废掉全部进化。
        """
        r = run_golden_eval(_tasks(), seed=42, size=5, runner=_boom)
        assert r.skipped is True
        assert "infrastructure" in r.skip_reason or "errored" in r.skip_reason
        assert r.errors

    def test_partial_failure_excluded_not_scored_zero(self):
        """单任务异常**剔除**，不记 0.0（C10 基线硬条款 1）。

        旧实现把异常折算成 0.0 计入均值：每轮随机踩到几条超时 → 基线
        系统性偏低 → 候选只要"少崩一点"就自动"变好"。这是基线投毒通道。
        现在有效样本全部满分时，均值必须仍是 1.0。
        """
        calls = {"n": 0}

        def one_in_ten_boom(task, ws, max_iter):
            calls["n"] += 1
            if calls["n"] == 5:  # 10 条里坏 1 条
                raise RuntimeError("boom")
            return _perfect(task, ws, max_iter)

        r = run_golden_eval(_tasks(20), seed=42, size=10, runner=one_in_ten_boom)
        assert not r.skipped
        assert r.tasks_run == 9, "异常任务必须被剔除出有效样本"
        assert r.tasks_selected == 10
        assert r.metric == pytest.approx(1.0), "异常不得折算成 0 分进均值"
        assert len(r.errors) == 1
        assert r.error_rate == pytest.approx(0.1)

    def test_high_error_rate_skips_round(self):
        """报错率超健康度上限 → 整轮 skipped（基线硬条款 2）。"""
        calls = {"n": 0}

        def alternate_boom(task, ws, max_iter):
            calls["n"] += 1
            if calls["n"] % 2:
                raise RuntimeError("boom")
            return _perfect(task, ws, max_iter)

        r = run_golden_eval(_tasks(), seed=42, size=6, runner=alternate_boom)
        assert r.skipped is True
        assert "error rate" in r.skip_reason
        assert r.tasks_run == 3, "幸存者仍记录下来，但不得据此给结论"

    def test_insufficient_valid_samples_skips_round(self):
        """有效样本 < 最小值 → 整轮 skipped（基线硬条款 1）。"""
        calls = {"n": 0}

        def mostly_boom(task, ws, max_iter):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("boom")
            return _perfect(task, ws, max_iter)

        r = run_golden_eval(_tasks(), seed=42, size=6, runner=mostly_boom)
        assert r.skipped is True
        assert "insufficient valid samples" in r.skip_reason
        assert r.tasks_run == 2

    def test_errors_recorded(self):
        r = run_golden_eval(_tasks(), seed=42, size=3, runner=_boom)
        assert len(r.errors) == 3


# ── replay 接口 ─────────────────────────────────────────────────────


class TestMakeGoldenReplay:
    def test_returns_metric(self):
        replay = make_golden_replay(_tasks(), seed=42, size=5, runner=_perfect)
        out = replay(GenomeProposal.new("tool_set", {"tools": ["read"]}), HoldoutSet((), 1, "s"))
        # D3: replay 返回 ReplayOutcome（能力 + 并列成本维度）
        assert isinstance(out, ReplayOutcome)
        assert out.metric > 0.8
        # 无预算任务 → cost 无样本，但绝对 tokens 单列
        assert out.cost is None
        assert out.cost_samples == 0

    def test_skipped_raises_slot_unavailable(self):
        """无法验证必须走 escalate（抛 SlotUnavailable），不能静默返回 0。"""
        from agentnodes.kernel.evolution.isolated_replay import (
            SlotUnavailable,
        )

        replay = make_golden_replay(_tasks(), seed=42, size=5, runner=_boom)
        with pytest.raises(SlotUnavailable):
            replay(GenomeProposal.new("tool_set", {"tools": []}), HoldoutSet((), 1, "s"))


class TestBaselineRollingMedian:
    """交付 4：基线滚动中位数（C10 / 评审 F7 假阳性来源）。

    旧实现 ``cached["value"]`` 无条件缓存单轮值 → 长驻进程里基线永不更新，
    多轮改进后正常候选被读成"暴涨 → reward hacking"。
    """

    def _gw(self, version: str = "gv-0"):
        class _Genome:
            def current_version_id(self) -> str:
                return version

        class _Gw:
            genome = _Genome()

        return _Gw()

    def _run(self, monkeypatch, values, *, skipped=False):
        """把 rsi_wiring 里的 run_golden_eval 换成按序返回 fixed result。

        ``values`` 依次消费；用完后重复最后一个（便于断言稳态）。
        """
        import agentnodes.interfaces.rsi_wiring as rw
        import agentnodes.kernel.evolution.golden_eval as ge

        seq = list(values)
        calls = {"n": 0}

        def fake_run(tasks, *, seed, size=ge.DEFAULT_SAMPLE_SIZE):
            idx = min(calls["n"], len(seq) - 1)
            calls["n"] += 1
            value = seq[idx]
            if value is None:
                return ge.GoldenEvalResult(
                    metric=0.0, tasks_run=0, skipped=True,
                    skip_reason="insufficient valid samples", tasks_selected=10,
                )
            return ge.GoldenEvalResult(
                metric=value, tasks_run=10, tasks_selected=10,
                cost=0.8, cost_samples=5, tokens=1200.0, tokens_samples=5,
            )

        monkeypatch.setattr(ge, "run_golden_eval", fake_run)
        baseline = rw._baseline_metric(self._gw(), tasks=[object()], seed=0)
        return baseline, calls

    def test_median_smooths_single_low_round(self, monkeypatch):
        """单轮低分不得把基线拖低（均值会被拖，中位数不会）。"""
        baseline, _ = self._run(monkeypatch, [1.0, 1.0, 0.2])
        observed = [baseline().metric for _ in range(3)]
        assert observed[:2] == [1.0, 1.0]
        assert observed[2] == 1.0, "三轮 [1.0, 1.0, 0.2] 的中位数必须是 1.0"

    def test_baseline_tracks_multi_round_improvement(self, monkeypatch):
        """连续改进后基线跟着上移（旧实现的假阳性根因）。"""
        baseline, _ = self._run(monkeypatch, [0.60, 0.70, 0.80, 0.90, 0.95])
        values = [baseline().metric for _ in range(5)]
        assert values[0] == 0.60
        assert values[-1] == 0.80, "5 轮窗口的中位数应是中间观测，而非第一轮"
        assert values[-1] > values[0]

    def test_skipped_round_does_not_pollute_window(self, monkeypatch):
        """skipped 轮次不进窗口：基线不得因无效轮次被覆盖。"""
        baseline, _ = self._run(monkeypatch, [1.0, None, None])
        assert baseline().metric == 1.0
        assert baseline().metric == 1.0, "skipped 后仍应返回窗口内有效值，而非 None"

    def test_no_valid_round_yet_returns_none(self, monkeypatch):
        baseline, _ = self._run(monkeypatch, [None])
        assert baseline() is None, "从无有效样本 → None（Gate 会 escalate）"

    def test_window_carries_cost_and_tokens(self, monkeypatch):
        """基线必须带 cost/tokens，否则成本闸在基线侧恒无样本 → 永跳过。"""
        baseline, _ = self._run(monkeypatch, [0.9])
        out = baseline()
        assert isinstance(out, ReplayOutcome)
        assert out.cost == pytest.approx(0.8)
        assert out.cost_samples == 5
        assert out.tokens == pytest.approx(1200.0)
        assert out.tokens_samples == 5

    def test_empty_tasks_returns_none(self, monkeypatch):
        import agentnodes.interfaces.rsi_wiring as rw

        assert rw._baseline_metric(self._gw(), tasks=[], seed=0)() is None


class TestApplyProposal:
    def test_proposal_applied_during_eval(self):
        """关键：评测期间 genome 必须是"应用了提案"的状态。

        不应用的话候选分与基线分相同，验证闸会算出 0% 退化、判为通过，
        然后激活一个从未被真正验证过的提案。
        """
        g = GenomeVersionStore()
        seen: dict = {}

        def runner(task, ws, max_iter):
            seen["tier1"] = g.tier1_active()
            seen["version"] = g.current_version_id()
            return _perfect(task, ws, max_iter)

        def applier(proposal):
            before = g.current_version_id()
            g.activate(f"replay:{proposal.proposal_id}",
                       {proposal.target_component: proposal.change})

            def undo():
                g.rollback_to(before)
            return undo

        replay = make_golden_replay(
            _tasks(), seed=42, size=3, runner=runner, apply_proposal=applier,
        )
        replay(GenomeProposal.new("tool_set", {"tools": ["read"]}), HoldoutSet((), 1, "s"))

        assert seen["tier1"] == {"tool_set": {"tools": ["read"]}}
        assert seen["version"] != "gv-0"

    def test_genome_restored_after_eval(self):
        g = GenomeVersionStore()

        def applier(proposal):
            before = g.current_version_id()
            g.activate(f"replay:{proposal.proposal_id}",
                       {proposal.target_component: proposal.change})
            return lambda: g.rollback_to(before)

        replay = make_golden_replay(
            _tasks(), seed=42, size=3, runner=_perfect, apply_proposal=applier,
        )
        replay(GenomeProposal.new("tool_set", {"tools": ["read"]}), HoldoutSet((), 1, "s"))

        assert g.current_version_id() == "gv-0"
        assert g.tier1_active() == {}

    def test_genome_restored_even_when_eval_raises(self):
        g = GenomeVersionStore()

        def applier(proposal):
            before = g.current_version_id()
            g.activate("replay:x", {proposal.target_component: proposal.change})
            return lambda: g.rollback_to(before)

        replay = make_golden_replay(
            _tasks(), seed=42, size=3, runner=_boom, apply_proposal=applier,
        )
        with pytest.raises(Exception):
            replay(GenomeProposal.new("tool_set", {"tools": []}), HoldoutSet((), 1, "s"))

        # undo 必须在 finally 里执行——评测炸了也不能把 genome 留在临时版本
        assert g.current_version_id() == "gv-0"

    def test_no_applier_leaves_genome_untouched(self):
        g = GenomeVersionStore()
        seen: dict = {}

        def runner(task, ws, max_iter):
            seen["tier1"] = g.tier1_active()
            return _perfect(task, ws, max_iter)

        replay = make_golden_replay(_tasks(), seed=42, size=3, runner=runner)
        replay(GenomeProposal.new("tool_set", {"tools": ["read"]}), HoldoutSet((), 1, "s"))

        assert seen["tier1"] == {}, "未传 applier 时提案不应生效"
