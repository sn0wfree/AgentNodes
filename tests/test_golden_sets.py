"""held-in / held-out 双集双门（框架 v1.1 C9 / GEP-RSI 第 5 步）。

覆盖四组交付：

1. **拆分**：sha256 分桶可复现、两集不相交、并集 == 原集、比例正确
2. **进程外持有**：held-out 只从仓库外路径读；仓库内无明文；
   缺失/空/条数不足 → gate **不 activate** 且 emit 事件
3. **双门判定**：held-in 门沿用 ±20%；held-out 门同阈值；任一门失败 → 拒
4. **分歧 kill-switch**：held-in 改进 + held-out 退化 → escalate +
   ``trip_halt`` + 独立告警事件

约定：本文件所有 held-out 侧都用**注入 runner**，不打真实 LLM。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentnodes.evals import golden_sets as gs  # noqa: E402
from agentnodes.evals.scorecard import GoldenTask, RunRecord  # noqa: E402
from agentnodes.kernel.evolution.genome_proposals import (  # noqa: E402
    GenomeProposal,
    ProposalStatus,
)
from agentnodes.kernel.evolution.holdout import HoldoutSet  # noqa: E402
from agentnodes.kernel.evolution.validation_gate import (  # noqa: E402
    DEGRADATION_THRESHOLD,
    HELDOUT_DEGRADATION_THRESHOLD,
    HELDOUT_MISSING_POLICY,
    HELDOUT_UNWIRED_POLICY,
    ValidationGate,
)
from agentnodes.kernel.orchestration.genome_version import (  # noqa: E402
    GenomeVersionStore,
)


# ── 测试替身 ─────────────────────────────────────────────────────────


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]

    def kinds(self):
        return [k for k, _ in self.events]


def _holdout() -> HoldoutSet:
    return HoldoutSet(trajectories=(), seed=42, task_signature="sig")


def _perfect(task, ws, max_iter):
    return RunRecord(
        task_id=task.id, tools_used=list(task.expected_tools),
        finished_reason="stop", answer="ok", success=True,
    )


def _half(task, ws, max_iter):
    """一半工具命中、内容缺失：分数明显低于 perfect 但非 0。"""
    return RunRecord(
        task_id=task.id, tools_used=list(task.expected_tools)[:1],
        finished_reason="stop", answer="", success=True,
    )


def _golden_tasks(n: int = 8) -> list[GoldenTask]:
    return [
        GoldenTask(id=f"heldout-{i}", prompt=f"p{i}", expected_tools=["read"])
        for i in range(n)
    ]


def _write_heldout(path: Path, tasks: list[GoldenTask]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps({"id": t.id, "prompt": t.prompt,
                              "expected_tools": t.expected_tools})
                  for t in tasks) + "\n",
        encoding="utf-8",
    )
    return path


def _gate(
    *,
    heldin_candidate: float = 1.0,
    heldin_baseline: float = 1.0,
    heldout_candidate: float = 1.0,
    heldout_baseline: float = 1.0,
    wire_heldout: bool = True,
    trip_halt=None,
    emit: _Emitter | None = None,
    **kwargs,
) -> tuple[ValidationGate, _Emitter, GenomeVersionStore]:
    emit = emit or _Emitter()
    genome = GenomeVersionStore()
    gate = ValidationGate(
        genome=genome,
        emit=emit,
        replay=lambda proposal, holdout: heldin_candidate,
        baseline_metric=lambda: heldin_baseline,
        heldout_replay=(
            (lambda proposal, holdout: heldout_candidate) if wire_heldout else None
        ),
        heldout_baseline=(
            (lambda: heldout_baseline) if wire_heldout else None
        ),
        trip_halt=trip_halt,
        **kwargs,
    )
    return gate, emit, genome


# ── 1. 拆分 ──────────────────────────────────────────────────────────


def _synthetic_rows(n: int = 200) -> list[dict]:
    """合成的源集：id 均匀散布在 200 个桶上，比例应逼近 70/30。

    拆分规则的断言用合成集（可控、可重复），**另有一条**对真实仓库
    held-in 文件的规则一致性断言（``test_repo_heldin_file_matches_rule``）。
    真实源集在拆分后只剩 held-in（held-out 明文不进仓库），拿它去断言
    "比例约 70/30" 必然失败——那是产物，不是源。
    """
    return [{"id": f"synthetic-task-{i:04d}", "prompt": f"p{i}"} for i in range(n)]


class TestSplitRule:
    def test_bucket_is_deterministic_and_stable(self):
        """同一 id 反复分桶必得同一值——拆分可复现的前提。"""
        for i in range(50):
            tid = f"task-{i}"
            assert gs.bucket_of(tid) == gs.bucket_of(tid)
            assert 0 <= gs.bucket_of(tid) < gs.BUCKET_MODULUS

    def test_bucket_depends_only_on_id(self):
        """与 id 的构造方式无关，只与字面值有关（可跨进程/跨版本复现）。"""
        expected = int.from_bytes(
            hashlib.sha256(b"factor-ic-check").digest()[:8], "big"
        ) % gs.BUCKET_MODULUS
        assert gs.bucket_of("factor-ic-check") == expected

    def test_partition_disjoint_and_covers_source(self):
        rows = _synthetic_rows()
        heldin, heldout = gs.split_rows(rows)
        hin = {r["id"] for r in heldin}
        hout = {r["id"] for r in heldout}
        assert not (hin & hout), "两集不得相交"
        assert hin | hout == {r["id"] for r in rows}, "并集必须等于原集"
        assert len(heldin) + len(heldout) == len(rows)

    def test_ratio_is_roughly_70_30(self):
        rows = _synthetic_rows(400)
        heldin, heldout = gs.split_rows(rows)
        total = len(rows)
        assert 0.60 <= len(heldin) / total <= 0.80, f"held-in {len(heldin)}/{total}"
        assert 0.20 <= len(heldout) / total <= 0.40, f"held-out {len(heldout)}/{total}"
        assert heldout, "held-out 集不得为空（空集等于没有双集）"

    def test_split_is_stable_across_input_order(self):
        """输入顺序不影响拆分结果（哈希只看 id）。"""
        rows = _synthetic_rows()
        forward = {r["id"] for r in gs.split_rows(rows)[1]}
        backward = {r["id"] for r in gs.split_rows(list(reversed(rows)))[1]}
        assert forward == backward

    def test_repo_heldin_file_matches_rule(self):
        """真实仓库的 held-in 文件：每一行都必须落在 held-in 桶。"""
        path = gs.heldin_path()
        if not path.exists():
            pytest.skip("held-in set not generated (run split_golden_sets.py)")
        rows = gs.read_rows(path)
        bad = [r["id"] for r in rows if not gs.is_heldin(str(r["id"]))]
        assert bad == [], f"held-in 集里混入了 held-out 桶的任务: {bad[:5]}"
        assert rows, "held-in 集不得为空"

    def test_manifest_counts_match_files(self):
        """清单的计数必须与磁盘上的 held-in 文件一致。"""
        manifest_file = gs.manifest_path()
        path = gs.heldin_path()
        if not manifest_file.exists() or not path.exists():
            pytest.skip("split products not generated")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest["heldin"]["count"] == len(gs.read_rows(path))
        assert manifest["heldin"]["file"] == gs.HELDIN_FILENAME

    def test_manifest_has_no_task_plaintext(self):
        """仓库内清单只放计数与聚合指纹，不放任务 id / prompt 明文。"""
        manifest_file = gs.manifest_path()
        if not manifest_file.exists():
            pytest.skip("manifest not generated (run split_golden_sets.py)")
        text = manifest_file.read_text(encoding="utf-8")
        manifest = json.loads(text)

        # 结构断言：只允许这几类字段，任何 "prompt"/"id" 明文都不该出现
        assert "prompt" not in text
        assert '"id"' not in text
        assert set(manifest) == {
            "split_version", "rule", "source", "total", "heldin",
            "heldout", "union_digest",
        }

        # 用 held-in（公开）的 id 做单调检验：清单连公开 id 都没有，
        # 自然不会有 held-out id。这条不依赖外部母本，CI 里也能跑。
        for row in gs.read_rows(gs.heldin_path()):
            assert str(row["id"]) not in text, f"manifest 泄漏任务 id: {row['id']}"

        assert manifest["heldout"]["ids_digest"], "清单应记录 held-out 的聚合指纹"


# ── 2. 进程外持有 ────────────────────────────────────────────────────


class TestExternalHolding:
    def test_default_path_is_not_in_package(self):
        """默认路径不得落在**包内**（包内文件随 wheel 分发，等于公开）。"""
        path = gs.heldout_path().resolve()
        pkg = gs._EVALS_DIR.resolve()
        assert pkg not in path.parents, f"held-out 落在 evals 包内: {path}"
        assert gs.HELDOUT_DIRNAME in path.parts

    def test_heldout_path_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "custom.jsonl"
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        assert gs.heldout_path() == target

    def test_no_heldout_plaintext_in_repo(self):
        """仓库内不得有 held-out 明文（文件名含 heldout 的 jsonl）。"""
        found = sorted((ROOT / "src").rglob("*heldout*.jsonl"))
        assert found == [], f"仓库内出现 held-out 明文: {found}"

    def test_load_missing_is_unavailable_with_reason(self, monkeypatch, tmp_path):
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(tmp_path / "absent.jsonl"))
        status = gs.load_heldout()
        assert status.available is False
        assert "not found" in status.reason
        assert status.count == 0

    def test_load_empty_is_unavailable(self, monkeypatch, tmp_path):
        target = tmp_path / "empty.jsonl"
        target.write_text("\n\n", encoding="utf-8")
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        status = gs.load_heldout()
        assert status.available is False
        assert "empty" in status.reason

    def test_load_too_few_is_unavailable(self, monkeypatch, tmp_path):
        target = _write_heldout(tmp_path / "few.jsonl", _golden_tasks(3))
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        status = gs.load_heldout(min_tasks=5)
        assert status.available is False
        assert "insufficient" in status.reason

    def test_load_enough_is_available(self, monkeypatch, tmp_path):
        target = _write_heldout(tmp_path / "ok.jsonl", _golden_tasks(6))
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        status = gs.load_heldout(min_tasks=5)
        assert status.available is True
        assert status.count == 6

    def test_make_heldout_replay_raises_when_missing(self, monkeypatch, tmp_path):
        """不可用时 replay/baseline 抛 HeldoutUnavailable，**不返回 0 分**。"""
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(tmp_path / "absent.jsonl"))
        replay, baseline, status = gs.make_heldout_replay(seed=0, size=3)
        assert status.available is False
        with pytest.raises(gs.HeldoutUnavailable):
            baseline()
        with pytest.raises(gs.HeldoutUnavailable):
            replay(GenomeProposal.new("prompt", {"a": 1}), _holdout())


# ── 2b. seed 进程外注入 ─────────────────────────────────────────────


class TestSeedInjection:
    def test_env_seed_wins(self, monkeypatch):
        monkeypatch.setenv(gs.SEED_ENV, "777")
        emit = _Emitter()
        seed, source = gs.resolve_golden_seed(emit=emit)
        assert (seed, source) == (777, "env")
        assert emit.of("rsi.golden_seed") == [], "显式注入不该发降级提示"

    def test_default_seed_is_fixed_and_emits(self, monkeypatch):
        monkeypatch.delenv(gs.SEED_ENV, raising=False)
        emit = _Emitter()
        seed, source = gs.resolve_golden_seed(emit=emit)
        assert seed == gs.DEFAULT_GOLDEN_SEED
        assert source == "default"
        events = emit.of("rsi.golden_seed")
        assert len(events) == 1, "未注入种子必须 emit 提示（不得静默）"
        assert events[0]["source"] == "default"

    def test_invalid_env_falls_back_and_emits(self, monkeypatch):
        monkeypatch.setenv(gs.SEED_ENV, "not-an-int")
        emit = _Emitter()
        seed, source = gs.resolve_golden_seed(emit=emit)
        assert seed == gs.DEFAULT_GOLDEN_SEED
        assert source == "default_invalid_env"
        assert emit.of("rsi.golden_seed")[0]["source"] == "default_invalid_env"


# ── 3. 双门判定 ──────────────────────────────────────────────────────


class TestDualGate:
    def test_both_pass_activates(self):
        gate, emit, genome = _gate()
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED
        assert genome.current_version_id() != "gv-0"
        assert emit.of("genome.heldout_gate")[0]["verdict"] == "passed"
        assert gate.history[-1].heldout_checked is True

    def test_heldout_degradation_rejects(self):
        """held-in 正常但 held-out 退化超阈值 → 拒（不得 activate）。"""
        gate, emit, genome = _gate(
            heldin_candidate=1.0, heldout_candidate=0.5,
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "held-out degraded" in out.rejection_reason
        assert genome.current_version_id() == "gv-0", "拒绝不得升版"
        assert emit.of("genome.heldout_gate")[0]["verdict"] == "rejected"

    def test_heldin_degradation_still_rejects(self):
        """held-in 门语义未变：held-out 再好也不能救 held-in 的退化。"""
        gate, _, _ = _gate(
            heldin_candidate=1.0 - DEGRADATION_THRESHOLD * 2,
            heldout_candidate=1.0,
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "degraded" in out.rejection_reason

    def test_heldout_threshold_equals_heldin(self):
        """两侧阈值必须同值：口径不一致会制造新的解释漏洞。"""
        assert HELDOUT_DEGRADATION_THRESHOLD == DEGRADATION_THRESHOLD

    def test_within_heldout_threshold_passes(self):
        gate, _, _ = _gate(
            heldin_candidate=1.0,
            heldout_candidate=1.0 - DEGRADATION_THRESHOLD / 2,
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.APPROVED

    def test_unwired_heldout_never_activates(self):
        """**核心**：held-out 未接线时，凭 held-in 单集**不得**通过。"""
        gate, emit, genome = _gate(wire_heldout=False)
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert genome.current_version_id() == "gv-0"
        assert gate.history[-1].heldout_checked is False
        event = emit.of("genome.heldout_gate")[0]
        assert event["verdict"] == "unavailable"
        assert event["kind"] == "unwired"
        assert event["policy"] == HELDOUT_UNWIRED_POLICY

    def test_unwired_is_escalated_not_counted_as_failure(self):
        """未接线是运维问题：escalate 不计入回滚计数（与 slot 不可用同原则）。"""
        gate, _, _ = _gate(wire_heldout=False)
        for _ in range(3):
            gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert gate.consecutive_failures == 0
        assert all(r.escalated for r in gate.history)

    def test_missing_heldout_file_never_activates(
        self, monkeypatch, tmp_path
    ):
        """held-out 文件不在 → 不 activate 且 emit（不得静默降级成单集）。"""
        target = tmp_path / "absent.jsonl"
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        kwargs = gs.heldout_gate_kwargs(
            seed=0, size=3, runner=_perfect, emit=_Emitter(),
        )
        emit = _Emitter()
        genome = GenomeVersionStore()
        gate = ValidationGate(
            genome=genome, emit=emit,
            replay=lambda p, h: 1.0, baseline_metric=lambda: 1.0,
            **kwargs,
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "held-out unavailable" in out.rejection_reason
        assert genome.current_version_id() == "gv-0"
        event = emit.of("genome.heldout_gate")[0]
        assert event["verdict"] == "unavailable"
        assert event["kind"] == "missing"
        assert event["policy"] == HELDOUT_MISSING_POLICY

    def test_missing_policy_reject_counts_failure(self, monkeypatch, tmp_path):
        """``reject`` 策略：按普通失败计数（更严的部署选择）。"""
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(tmp_path / "absent.jsonl"))
        kwargs = gs.heldout_gate_kwargs(seed=0, size=3, runner=_perfect)
        gate = ValidationGate(
            genome=GenomeVersionStore(), emit=_Emitter(),
            replay=lambda p, h: 1.0, baseline_metric=lambda: 1.0,
            heldout_missing_policy="reject",
            **kwargs,
        )
        gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert gate.consecutive_failures == 1

    def test_invalid_policy_rejected_at_construction(self):
        with pytest.raises(ValueError, match="not one of"):
            ValidationGate(
                genome=GenomeVersionStore(), emit=_Emitter(),
                replay=lambda p, h: 1.0, baseline_metric=lambda: 1.0,
                heldout_missing_policy="allow",
            )

    def test_heldout_implausible_jump_escalates(
        self, monkeypatch, tmp_path
    ):
        """held-out 上"暴涨"与 held-in 同口径：可疑，escalate 不放行。"""
        target = _write_heldout(tmp_path / "h.jsonl", _golden_tasks(6))
        monkeypatch.setenv(gs.HELDOUT_PATH_ENV, str(target))
        kwargs = gs.heldout_gate_kwargs(
            seed=0, size=3, runner=_perfect, emit=_Emitter(),
        )
        emit = _Emitter()
        gate = ValidationGate(
            genome=GenomeVersionStore(), emit=emit,
            replay=lambda p, h: 1.0, baseline_metric=lambda: 1.0,
            **kwargs,
        )
        # held-in 1.0；held-out 基线 0.5 → 候选 1.0 = 提升 100%
        gate.heldout_baseline = lambda: 0.5
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert "implausible jump" in out.rejection_reason
        assert gate.history[-1].escalated is True


# ── 4. 分歧 kill-switch ──────────────────────────────────────────────


class TestDivergenceKillSwitch:
    def test_divergence_trips_halt(self):
        """held-in 改进 + held-out 退化 → escalate + trip_halt + 告警事件。"""
        calls: list[str] = []
        gate, emit, genome = _gate(
            heldin_candidate=1.15,     # held-in 提升 15%
            heldout_candidate=0.5,     # held-out 退化 50%
            trip_halt=lambda reason: calls.append(reason),
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert genome.current_version_id() == "gv-0", "分歧提案不得 activate"

        verdict = gate.history[-1]
        assert verdict.heldout_divergence is True
        assert verdict.escalated is True, "分歧必须 escalate（人工介入）"

        divergences = emit.of("genome.heldout_divergence")
        assert len(divergences) == 1
        assert divergences[0]["halted"] is True
        assert divergences[0]["halt_error"] == ""
        assert divergences[0]["heldin_degradation"] < 0
        assert divergences[0]["heldout_degradation"] > 0
        assert emit.of("genome.heldout_gate")[0]["verdict"] == "divergence"

        assert len(calls) == 1, "kill-switch 必须真的被调用"
        assert "divergence" in calls[0]
        assert "held-in improved" in calls[0]

    def test_divergence_does_not_count_toward_rollback(self):
        """分歧已 escalate；再累计就会在 3 次后回滚一个健康的基因组。"""
        gate, _, _ = _gate(
            heldin_candidate=1.15, heldout_candidate=0.5,
            trip_halt=lambda reason: None,
        )
        for _ in range(3):
            gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert gate.consecutive_failures == 0

    def test_no_divergence_when_heldin_also_degrades(self):
        """held-in 也没变好 + held-out 退化 = 普通失败，不该停机。"""
        calls: list[str] = []
        gate, emit, _ = _gate(
            heldin_candidate=0.95, heldout_candidate=0.5,
            trip_halt=lambda reason: calls.append(reason),
        )
        out = gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        assert out.status is ProposalStatus.REJECTED
        assert calls == [], "非分歧不得触发 kill-switch"
        assert emit.of("genome.heldout_divergence") == []
        assert gate.history[-1].heldout_divergence is False

    def test_missing_trip_halt_is_reported_not_faked(self):
        """未注入 trip_halt 时不假装护栏生效：事件里 halted=False + 原因。"""
        gate, emit, _ = _gate(
            heldin_candidate=1.15, heldout_candidate=0.5, trip_halt=None,
        )
        gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        event = emit.of("genome.heldout_divergence")[0]
        assert event["halted"] is False
        assert "not injected" in event["halt_error"]

    def test_trip_halt_failure_does_not_hide_divergence(self):
        def boom(reason):
            raise RuntimeError("disk full")

        gate, emit, _ = _gate(
            heldin_candidate=1.15, heldout_candidate=0.5, trip_halt=boom,
        )
        gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
        event = emit.of("genome.heldout_divergence")[0]
        assert event["halted"] is False
        assert "disk full" in event["halt_error"]
        # 分歧本身仍然被记为 escalate
        assert gate.history[-1].heldout_divergence is True


class TestKillSwitchCrossProcess:
    def test_trip_halt_persists_to_disk(self, monkeypatch, tmp_path):
        """kill-switch 复用 halt.py：状态落盘 → 跨进程可见。"""
        from agentnodes.interfaces.cli import halt

        monkeypatch.setenv("AGENTNODES_HALT_PATH", str(tmp_path / "halt.json"))
        halt.clear_halt()
        try:
            gate, emit, _ = _gate(
                heldin_candidate=1.15, heldout_candidate=0.5,
                trip_halt=halt.trip_halt,
            )
            gate.validate(GenomeProposal.new("prompt", {"a": 1}), _holdout())
            assert halt.is_halted() is True
            state = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))
            assert state["halted"] is True
            assert "divergence" in state["reason"]
            assert emit.of("genome.heldout_divergence")[0]["halted"] is True
        finally:
            halt.clear_halt()
