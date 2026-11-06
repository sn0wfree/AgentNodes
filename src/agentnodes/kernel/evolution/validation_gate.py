"""ValidationGate — 提案的验证与回滚闸（施工方案 §4.5 / §4.8）。

**职责**：在隔离 slot 中用**只读** holdout 回放提案，比较指标，决定生效
或拒绝。它是 Tier 1 变更的唯一入口——没有任何路径能绕过它直接改配置。

**三条硬规则**：
1. **Tier 0 拒绝**（H3）：``target_component ∈ TIER0_COMPONENTS`` 直接拒。
   提案对象在构造时已拒一次；这里再拒一次，防止绕过 ``GenomeProposal.new``
   构造的野生对象（例如从 DB 反序列化的旧数据）。
2. **±20% 阈值**：holdout 指标劣化超过 20% 即失败；指标**方向相反且无法
   解释**时暂停通道并 escalate（goal §6 第 3 条）。
3. **连续 3 次失败 → 回滚**：``status=rolled_back`` + emit
   ``genome.rollback``，genome_version 回到上一版本。

**双闸（框架 v1.1 D3，2026-11-06）**：能力闸（上面第 2 条）**语义与阈值
完全不变**；成本闸是**并列**的第二道闸，绝不并入能力指标：

- 成本退化 > ``COST_DEGRADATION_THRESHOLD``（30%）→ **硬拒**，无论能力涨
  多少（权重=汇率 = 自我评分交换面，D3 明确禁止）。
- 能力上升但成本涨幅 > ``COST_TRADE_ESCALATION_THRESHOLD`` → escalate
  人工审（复用「暴涨 escalate」先例）。
- **禁混合均值**：带 ``budget_max_tokens`` 的任务只贡献归一化 ``cost``
  （∈[0,1]）；无预算任务只贡献**绝对 tokens**，单列 ``tokens`` 维度。
  两类样本分开求均值、分别过闸——把数千量级的 token 与 0..1 的 cost 混
  进同一个均值，会把预算任务的退化信号稀释到噪声以下。
- **None 口径**：某侧完全没有 cost 样本 → 成本闸跳过并 emit
  ``genome.cost_gate``（``verdict=skipped``），**不装 0 分**；有样本但
  有效数 < ``MIN_COST_SAMPLES`` → 降级 escalate，而不是静默放行。

**双集双门（框架 v1.1 C9 / GEP-RSI 第 5 步，2026-11-06）**：单集双侧
（同一批 golden 上比基线 vs 候选）允许对固定验证集过拟合——每代挑对该
集有利的改动，指标一路合规上升而泛化能力下降。所以判定改成两道门：

- **held-in 门**：上面第 2 条（±20%）原样保留，逻辑与阈值**不变**。
- **held-out 门**：候选在**仓库外持有**的 held-out 集上须无显著退化，
  阈值同为 ``HELDOUT_DEGRADATION_THRESHOLD``。held-out 由
  ``agentnodes.evals.golden_sets`` 从 ``~/.agentnodes/eval_heldout/``
  （或 ``AN_GOLDEN_HELDOUT_PATH``）读，仓库内没有明文副本。
- **任一门失败 → 拒**；held-in 提了但 held-out 退了 = **分歧 kill-switch**：
  escalate + ``trip_halt``（护栏必须跨进程生效，见
  ``interfaces/cli/halt.py``）+ emit ``genome.heldout_divergence``。
- **held-out 不可用 → 只允许「不 activate」**（escalate，不计回滚计数：
  这是运维问题不是模型退化）。**绝不允许凭 held-in 单集通过**。

**可参数化**（框架 §0.5 元规则）：held-out 阈值、
``HELDOUT_MISSING_POLICY``（不可用时的拒法）、
``HELDOUT_UNWIRED_POLICY``（未接线时的策略）、
``DIVERGENCE_HELDIN_IMPROVEMENT``（判「held-in 确实改进」的下界）
都是模块常量，并可由 ``ValidationGate`` 构造参数覆盖。

**kill-switch 的注入口**：``trip_halt`` 是构造参数（默认 None）。gate 在
L3 内核层，直接 ``from agentnodes.interfaces.cli.halt import trip_halt``
会新增一条 L3→L1 上行 import（阶层层门禁的棘轮基线只许降）。所以由
组装根（L1，``rsi_wiring``）把 ``trip_halt`` 注入进来——与 ``emit`` /
``replay`` / ``baseline_metric`` 同一模式。未注入时分歧仍会 escalate +
emit 告警事件，但 ``halted=False`` 会如实写进事件，不假装护栏生效了。

**只读**：回放不写业务表，只写 ``validation_runs`` 审计表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .genome_proposals import (
    TIER0_COMPONENTS,
    GenomeProposal,
    ProposalStatus,
    TierViolation,
)
from .holdout import HoldoutSet

logger = logging.getLogger(__name__)

#: 指标劣化容忍阈值（施工方案 §4.5 第 3 步）。
DEGRADATION_THRESHOLD = 0.20

#: 成本退化硬拒阈值（D3，v1.1）。平均成本劣化超过它直接拒——能力涨多少
#: 都不能抵消。**可参数化**（§0.5 元规则）：模块常量只是缺省值，
#: ``ValidationGate.cost_degradation_threshold`` 构造参数可覆盖，装配根
#: （L1）可按环境调，内核侧不用改。
COST_DEGRADATION_THRESHOLD = 0.30

#: 「能力升但成本明显变贵」的人工审阈值。落在 30% 硬拒线以内、但超过这
#: 条线时，提案不再自动通过：这是治理层要看的交易（D3「能力升但成本暴涨
#: 超上限 → escalate」）。同样可参数化。
COST_TRADE_ESCALATION_THRESHOLD = 0.15

#: 成本维度参与判定所需的最小有效样本数。budget_max 实测只覆盖 7/70
#: 任务、seed=0 抽样仅 2 条——在 2 条 run 的均值上跑 30% 阈值等于掷硬币。
#: 不足此数 → 成本闸降级 escalate（人工确认），不静默放行、也不装 0 分。
MIN_COST_SAMPLES = 3

#: 连续失败多少次触发回滚（施工方案 §4.8）。
ROLLBACK_AFTER_FAILURES = 3

# ── held-in / held-out 双门常量（C9；可参数化，见模块 docstring）──────

#: held-out 门允许的劣化上限。与 held-in 同为 ±20%：两侧口径必须一致，
#: 否则「同一退化在两道门上结论不同」会成为新的解释漏洞。
HELDOUT_DEGRADATION_THRESHOLD = DEGRADATION_THRESHOLD

#: held-in **确实改进**的判据（退化率低于 -此值 = 提升超过此值）。
#: 分歧 kill-switch 只在「held-in 真的变好了」时才触发：held-in 也没变好
#: 而 held-out 退化，那是普通失败，按连续失败计数处理即可，不该停机。
DIVERGENCE_HELDIN_IMPROVEMENT = 0.0

#: held-out 不可用（文件缺失 / 空 / 条数不足）时的策略。
#: 只有两种合法值：
#:
#: - ``"reject_escalate"``（默认）：拒 + escalate，不计回滚计数。
#:   语义是「无法验证 → 不得激活」，把决策交给人。
#: - ``"reject"``：拒，按普通失败计入连续失败计数（更严）。
#:
#: **没有 "allow" / "skip"**：held-out 缺失时放行等于退回单集双侧，正是
#: 评审列为 NO-GO 的那个缺口。要放开这条必须改代码并重新评审。
HELDOUT_MISSING_POLICY = "reject_escalate"

#: gate **未接线 held-out**（没注入 ``heldout_replay`` / ``heldout_baseline``）
#: 时的策略。语义与 ``HELDOUT_MISSING_POLICY`` 相同，但前者是"文件不在"，
#: 这里是"装配没接"——分开表达，运维才知道该修路径还是修接线。
HELDOUT_UNWIRED_POLICY = "reject_escalate"

#: 允许的 held-out 策略取值（构造参数校验用）。
HELDOUT_POLICIES: frozenset[str] = frozenset({"reject_escalate", "reject"})


# ── 结果类型 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayOutcome:
    """回放的多维结果：能力指标 + 并列的成本维度（D3）。

    :param metric: 能力指标（0..1），进能力闸
    :param cost: 归一化 token 成本均值（∈[0,1]，1=很省、0=超预算）；
        只由**带 budget_max_tokens** 的任务贡献
    :param cost_samples: 贡献 ``cost`` 的有效任务数
    :param tokens: 无预算任务的**绝对 tokens 均值**——独立维度，绝不与
        ``cost`` 混入同一均值
    :param tokens_samples: 贡献 ``tokens`` 的有效任务数

    **向后兼容**：``ValidationGate`` 的 replay 仍接受裸 ``float``，按
    ``cost=None`` / ``tokens=None`` 处理；现存测试桩零改动。
    """

    metric: float
    cost: float | None = None
    cost_samples: int = 0
    tokens: float | None = None
    tokens_samples: int = 0


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str
    baseline_metric: float | None = None
    candidate_metric: float | None = None
    degradation: float | None = None
    escalated: bool = False
    # ── 成本闸观测（D3）────────────────────────────────────────────
    baseline_cost: float | None = None
    candidate_cost: float | None = None
    cost_degradation: float | None = None
    baseline_tokens: float | None = None
    candidate_tokens: float | None = None
    tokens_degradation: float | None = None
    #: 成本闸是否真正参与判定（False = 无样本/仅跳过）
    cost_checked: bool = False
    # ── held-out 双门观测（C9）─────────────────────────────────────
    #: held-out 侧的基线/候选读数。``None`` = 该侧没有可用样本。
    baseline_heldout: float | None = None
    candidate_heldout: float | None = None
    heldout_degradation: float | None = None
    #: held-out 门是否真正参与判定（False = 未接线 / 不可用）
    heldout_checked: bool = False
    #: 分歧 kill-switch 是否触发（held-in 升 + held-out 退）
    heldout_divergence: bool = False


def _as_outcome(value: Any) -> ReplayOutcome | None:
    """把 replay/baseline 的返回值统一成 ``ReplayOutcome``。

    ``float`` → ``ReplayOutcome(metric=value)``（``cost=None``）——这是
    向后兼容层：现存 ~12 个测试桩直接返回裸 float，零改动。
    """
    if value is None:
        return None
    if isinstance(value, ReplayOutcome):
        return value
    return ReplayOutcome(metric=float(value))


def _sample_pair(
    base_value: float | None,
    base_n: int,
    cand_value: float | None,
    cand_n: int,
) -> tuple[float, int, float, int] | None:
    """两侧都有可用样本才返回 ``(base, base_n, cand, cand_n)``，否则 None。

    任一侧为 ``None`` = 该侧没有可测样本（例如全是无预算任务）。此时
    该维度**不参与对比**——而不是当作 0 参与（装 0 会伪造出 100% 退化）。

    有值但没写样本数（``n<=0``）按 **1** 计：让它在最小样本数检查处
    escalate，而不是被当成"无样本"静默跳过——成本维度存在却被忽略，
    正是这道闸要防的失败形态。
    """
    if base_value is None or cand_value is None:
        return None
    base_n = int(base_n) if base_n > 0 else 1
    cand_n = int(cand_n) if cand_n > 0 else 1
    return float(base_value), base_n, float(cand_value), cand_n


def _has_cost_samples(baseline: ReplayOutcome, candidate: ReplayOutcome) -> bool:
    """是否至少有一个成本维度真正参与过对比（用于 ``cost_checked`` 观测位）。"""
    return _sample_pair(
        baseline.cost, baseline.cost_samples,
        candidate.cost, candidate.cost_samples,
    ) is not None or _sample_pair(
        baseline.tokens, baseline.tokens_samples,
        candidate.tokens, candidate.tokens_samples,
    ) is not None


class GenomeVersionStore(Protocol):
    """genome_version 的读写接口（由 ConfigHub 实现或测试替身）。"""

    def current_version_id(self) -> str: ...

    def activate(self, proposal_id: str, tier1_active: dict[str, Any]) -> str:
        """激活提案，返回新 version_id。"""
        ...

    def rollback(self) -> str:
        """回退到上一版本，返回回退后的 version_id。"""
        ...


class EventSink(Protocol):
    def __call__(self, event_type: str, data: dict[str, Any]) -> None: ...


def _min_heldout_tasks() -> int:
    """held-out 最小有效任务数（以 ``golden_sets`` 为准，缺依赖时取 5）。

    写成函数而不是模块常量：``MIN_HELDOUT_TASKS`` 的口径归双集模块
    （它才是"多少条算可用"的唯一事实源）；本处只在事件里回显，避免
    两处各写一个 5 而慢慢漂移。
    """
    try:
        from agentnodes.evals.golden_sets import MIN_HELDOUT_TASKS

        return int(MIN_HELDOUT_TASKS)
    except Exception:  # noqa: BLE001 —— evals 不可用时事件里给个占位值
        return 0


# ── Gate ─────────────────────────────────────────────────────────────


@dataclass
class ValidationGate:
    """提案验证闸。

    :param genome: 基因组版本存储
    :param emit: 事件发射器（``genome.rollback`` 等）
    :param replay: 回放函数 ``(proposal, holdout) -> metric | ReplayOutcome``。
        **只读**契约由调用方保证：它只能跑 holdout、不能写业务表。
        返回裸 ``float`` 时按 ``cost=None`` 处理（向后兼容）。
    :param baseline_metric: 当前基线指标提供者；可返回 ``float``、
        ``ReplayOutcome`` 或 ``None``（无法判断 → escalate）
    :param cost_degradation_threshold: 成本硬拒阈值（缺省
        ``COST_DEGRADATION_THRESHOLD``，可参数化）
    :param cost_trade_escalation_threshold: 能力升但成本变贵 → 人工审阈值
    :param min_cost_samples: 成本维度最小有效样本数（不足 → escalate）
    :param heldout_replay: **held-out 门**的候选回放
        ``(proposal, holdout) -> float``。None = 未接线（按
        ``heldout_unwired_policy`` 处理，**不会**退化成单集通过）。
    :param heldout_baseline: held-out 门的基线提供者 ``() -> float``。
        必须与 ``heldout_replay`` 跑同一批 held-out 任务、同 seed 同 size。
        None = 未接线。
    :param heldout_threshold: held-out 门劣化上限（缺省与 held-in 同值）
    :param heldout_unwired_policy: 未接线时的策略（见模块常量）
    :param heldout_missing_policy: 接线了但数据不可用时的策略
    :param trip_halt: kill-switch 注入口。接受 ``(reason: str)`` 位置参数
        或 ``(*, reason: str)`` 关键字参数（``interfaces.cli.halt.trip_halt``
        是后者）；缺省 None = 不注入 —— 分歧时只 escalate + emit，
        事件里 ``halted=False`` 如实反映护栏未落盘
    :param heldout_required: held-out 门是否**必须**参与判定。
        **生产装配必须保持 True（默认）**——True 时未接线/不可用一律
        不得 activate。只有「只测 held-in 判定机制」的单元测试才可显式
        传 False；即使如此也会 emit ``genome.heldout_gate``
        （``verdict="skipped_disabled"``），不会静默。
    """

    genome: GenomeVersionStore
    emit: EventSink
    replay: Callable[[GenomeProposal, HoldoutSet], float | ReplayOutcome]
    baseline_metric: Callable[[], float | None | ReplayOutcome]
    cost_degradation_threshold: float = COST_DEGRADATION_THRESHOLD
    cost_trade_escalation_threshold: float = COST_TRADE_ESCALATION_THRESHOLD
    min_cost_samples: int = MIN_COST_SAMPLES
    # ── held-in / held-out 双门（C9）─────────────────────────────────
    heldout_replay: Callable[[GenomeProposal, Any], float] | None = None
    heldout_baseline: Callable[[], float] | None = None
    heldout_threshold: float = HELDOUT_DEGRADATION_THRESHOLD
    heldout_unwired_policy: str = HELDOUT_UNWIRED_POLICY
    heldout_missing_policy: str = HELDOUT_MISSING_POLICY
    trip_halt: Callable[[str], None] | None = None
    heldout_required: bool = True

    _consecutive_failures: int = field(default=0, init=False)
    _history: list[ValidationResult] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        # 策略值拼错时早失败：静默回落到某个默认策略会让「怎么处置
        # held-out 缺失」变成不可审计的隐式行为。
        for name, value in (
            ("heldout_unwired_policy", self.heldout_unwired_policy),
            ("heldout_missing_policy", self.heldout_missing_policy),
        ):
            if value not in HELDOUT_POLICIES:
                raise ValueError(
                    f"{name}={value!r} is not one of {sorted(HELDOUT_POLICIES)}"
                )

    # ── 主入口 ──────────────────────────────────────────────────────

    def validate(
        self, proposal: GenomeProposal, holdout: HoldoutSet
    ) -> GenomeProposal:
        """验证提案并推进其状态。

        返回推进后的提案（approved / rejected / rolled_back）。
        """
        # 规则 1：Tier 0 一律拒绝（H3）。即便提案对象绕过了 new()，
        # 这里也必须拦住。
        if proposal.target_component in TIER0_COMPONENTS or proposal.is_tier0:
            return self._reject(
                proposal,
                f"Tier 0 component is frozen: {proposal.target_component!r}",
                escalated=False,
            )

        result = self._evaluate(proposal, holdout)
        self._history.append(result)

        # draft → validating 是进入评估的标记（施工方案 §4.5 流程第 1-2 步）
        working = (
            proposal
            if proposal.status is ProposalStatus.VALIDATING
            else proposal.to_validating()
        )

        if result.ok:
            self._consecutive_failures = 0
            approved = working.to_approved()
            self.genome.activate(
                approved.proposal_id, {approved.target_component: approved.change}
            )
            return approved

        # 基础设施级失败（如 slot 拿不到）只 escalate，不计入回滚计数——
        # 回滚语义是"模型退化"，而资源不足是运维问题。混在一起会让一次
        # 池容量不足被误判成三次模型退化，直接回滚一个没问题的基因组。
        if result.escalated:
            return self._reject(working, result.reason, escalated=True)

        # 规则 3：连续失败累计
        self._consecutive_failures += 1
        rejected = self._reject(working, result.reason, escalated=False)

        if self._consecutive_failures >= ROLLBACK_AFTER_FAILURES:
            return self._rollback(rejected, result.reason)
        return rejected

    # ── 评估 ────────────────────────────────────────────────────────

    def _evaluate(
        self, proposal: GenomeProposal, holdout: HoldoutSet
    ) -> ValidationResult:
        baseline_raw = self.baseline_metric()
        try:
            candidate_raw = self.replay(proposal, holdout)
        except Exception as exc:  # noqa: BLE001 —— 回放崩溃算验证失败
            # slot 拿不到属于"无法验证"，必须 escalate 而不是静默算作
            # 普通失败：普通失败会累积到回滚计数，那会让资源不足误触发
            # 回滚（把系统状态与基础设施状态混为一谈）。
            from .isolated_replay import SlotUnavailable

            if isinstance(exc, SlotUnavailable):
                logger.warning(
                    "replay slot unavailable for %s: %s", proposal.proposal_id, exc
                )
                return ValidationResult(
                    ok=False,
                    reason=f"replay slot unavailable: {exc} — escalated",
                    escalated=True,
                )
            logger.warning("replay failed for %s: %s", proposal.proposal_id, exc)
            return ValidationResult(ok=False, reason=f"replay failed: {exc}")

        baseline = _as_outcome(baseline_raw)
        candidate = _as_outcome(candidate_raw)

        metric = candidate.metric
        if baseline is None or baseline.metric == 0:
            # 无基线：不能判断优劣，按"需人工确认"处理（不静默放行）
            return ValidationResult(
                ok=False,
                reason="no baseline metric; cannot judge — escalated",
                candidate_metric=metric,
                escalated=True,
            )

        degradation = (baseline.metric - metric) / abs(baseline.metric)
        if degradation > DEGRADATION_THRESHOLD:
            return ValidationResult(
                ok=False,
                reason=(
                    f"metric degraded {degradation:.1%} "
                    f"(threshold {DEGRADATION_THRESHOLD:.0%})"
                ),
                baseline_metric=baseline.metric,
                candidate_metric=metric,
                degradation=degradation,
            )

        # 方向相反（候选远好于基线）也需警惕：可能是奖励黑客信号。
        # 施工方案 §4.5 只给了"不劣于基线"的下界；这里对"暴涨"要求
        # 同样走 escalation，而不是直接通过。
        if degradation < -DEGRADATION_THRESHOLD:
            return ValidationResult(
                ok=False,
                reason=(
                    f"metric improved {(-degradation):.1%} — implausible jump, "
                    "possible reward hacking; escalated"
                ),
                baseline_metric=baseline.metric,
                candidate_metric=metric,
                degradation=degradation,
                escalated=True,
            )

        # 能力闸通过 → 成本闸（D3 双闸的第二次判定；能力闸阈值/语义未动）
        cost_verdict, cost_deg, tokens_deg = self._check_cost(
            baseline, candidate, degradation
        )
        if cost_verdict is not None:
            return cost_verdict

        # 双集门（C9）：held-out 上再判一次。held-in 通过**不构成**放行
        # 依据——held-out 不可用时本函数返回 escalate 结果（不得 activate）。
        heldout_verdict, hb, hc, hd = self._check_heldout(
            proposal, holdout, metric_degradation=degradation,
        )
        if heldout_verdict is not None:
            return heldout_verdict

        return ValidationResult(
            ok=True,
            reason="within tolerance (held-in + held-out)",
            baseline_metric=baseline.metric,
            candidate_metric=metric,
            degradation=degradation,
            baseline_cost=baseline.cost,
            candidate_cost=candidate.cost,
            cost_degradation=cost_deg,
            baseline_tokens=baseline.tokens,
            candidate_tokens=candidate.tokens,
            tokens_degradation=tokens_deg,
            cost_checked=_has_cost_samples(baseline, candidate),
            baseline_heldout=hb,
            candidate_heldout=hc,
            heldout_degradation=hd,
            heldout_checked=hd is not None,
        )

    # ── 成本闸（D3）────────────────────────────────────────────────

    def _cost_result(
        self,
        *,
        baseline: ReplayOutcome,
        candidate: ReplayOutcome,
        reason: str,
        cost_degradation: float | None = None,
        tokens_degradation: float | None = None,
        metric_degradation: float | None = None,
        escalated: bool = False,
        cost_checked: bool = True,
    ) -> tuple[ValidationResult, float | None, float | None]:
        """构造成本闸结果，**同时带上能力读数**。

        两闸必须各自可读：成本闸拦下提案时，``degradation``（能力退化）
        仍要如实给出（通常是 0 或负值）——否则事后审计只看得到"被拒"，
        分不清是"能力变差"还是"能力没变但变贵了"。

        返回 ``(判定, cost 退化, tokens 退化)``，与 ``_check_cost`` 的契约
        一致（后者把本函数的返回值原样透出）。
        """
        return ValidationResult(
            ok=False,
            reason=reason,
            baseline_metric=baseline.metric,
            candidate_metric=candidate.metric,
            degradation=metric_degradation,
            escalated=escalated,
            baseline_cost=baseline.cost,
            candidate_cost=candidate.cost,
            cost_degradation=cost_degradation,
            baseline_tokens=baseline.tokens,
            candidate_tokens=candidate.tokens,
            tokens_degradation=tokens_degradation,
            cost_checked=cost_checked,
        ), cost_degradation, tokens_degradation

    def _check_cost(
        self,
        baseline: ReplayOutcome,
        candidate: ReplayOutcome,
        metric_degradation: float,
    ) -> tuple[ValidationResult | None, float | None, float | None]:
        """成本闸。返回 ``(判定, cost 退化, tokens 退化)``。

        判定为 ``None`` = 放行（后两个值回填到通过结果，便于审计读交易方向）。

        **分开算、分开过闸**：
        - 带 budget 的任务 → 归一化 ``cost`` 的均值（∈[0,1]）
        - 无预算的任务 → 绝对 ``tokens`` 的均值（独立维度）

        两侧样本都为空 → 跳过闸并 emit ``genome.cost_gate``；有样本但有效
        数不足 ``min_cost_samples`` → 降级 escalate（人工确认），不静默放行。
        """
        cost_pair = _sample_pair(baseline.cost, baseline.cost_samples,
                                 candidate.cost, candidate.cost_samples)
        tokens_pair = _sample_pair(baseline.tokens, baseline.tokens_samples,
                                   candidate.tokens, candidate.tokens_samples)

        # 两个维度都没有可用样本 → 不装 0 分，跳过并留痕
        if cost_pair is None and tokens_pair is None:
            self._emit_cost_gate({
                "verdict": "skipped",
                "reason": "no cost samples on either side",
                "baseline_cost": baseline.cost,
                "candidate_cost": candidate.cost,
                "baseline_tokens": baseline.tokens,
                "candidate_tokens": candidate.tokens,
            })
            logger.info("cost gate skipped: no cost samples on either side")
            return None, None, None

        # 有效样本数不足 → escalate（阈值跑在 2 条 run 的均值上等于掷硬币）
        for name, pair in (("cost", cost_pair), ("tokens", tokens_pair)):
            if pair is None:
                continue
            _, base_n, _, cand_n = pair
            if min(base_n, cand_n) < self.min_cost_samples:
                reason = (
                    f"insufficient {name} samples "
                    f"(baseline={base_n}, candidate={cand_n}, "
                    f"required>={self.min_cost_samples}) — escalated"
                )
                self._emit_cost_gate({
                    "verdict": "insufficient_samples",
                    "dimension": name,
                    "baseline_samples": base_n,
                    "candidate_samples": cand_n,
                    "required_samples": self.min_cost_samples,
                })
                return self._cost_result(
                    baseline=baseline,
                    candidate=candidate,
                    reason=reason,
                    tokens_degradation=None,
                    metric_degradation=metric_degradation,
                    escalated=True,
                    cost_checked=False,
                )

        # 归一化 cost 维度：cost 越小越贵（1=省，0=超预算）。退化 = 降幅。
        # ⚠ 归一化在 0 处饱和：基线已超预算（cost=0）时，候选再怎么涨也是
        # 0，这一维度读不出变化。这是 b2 归一化的已知边界（D3 选型时已接受
        # 「超预算 = 0，与既有 budget_ok 无缝衔接」）。此处**不**用绝对
        # tokens 去补——那正是禁止的混合均值。饱和时 emit 标记，让人看见
        # 这一轮成本维度是瞎的，而不是假装它通过了。
        cost_degradation: float | None = None
        cost_saturated = False
        if cost_pair is not None:
            base_cost, _, cand_cost, _ = cost_pair
            if base_cost > 0:
                cost_degradation = (base_cost - cand_cost) / base_cost
            elif cand_cost <= base_cost:
                cost_saturated = True

        tokens_degradation: float | None = None
        if tokens_pair is not None:
            base_tokens, _, cand_tokens, _ = tokens_pair
            if base_tokens > 0:
                tokens_degradation = (cand_tokens - base_tokens) / base_tokens

        # 硬拒：任一维度退化超阈值（能力涨多少都不抵消）
        for name, deg in (("cost", cost_degradation), ("tokens", tokens_degradation)):
            if deg is not None and deg > self.cost_degradation_threshold:
                reason = (
                    f"{name} degraded {deg:.1%} "
                    f"(threshold {self.cost_degradation_threshold:.0%}) — hard reject"
                )
                self._emit_cost_gate({
                    "verdict": "rejected",
                    "dimension": name,
                    "degradation": round(deg, 6),
                    "threshold": self.cost_degradation_threshold,
                    "metric_degradation": round(metric_degradation, 6),
                })
                return self._cost_result(
                    baseline=baseline,
                    candidate=candidate,
                    reason=reason,
                    cost_degradation=cost_degradation,
                    tokens_degradation=tokens_degradation,
                    metric_degradation=metric_degradation,
                )

        # 能力升但成本明显变贵 → escalate 人工审（复用「暴涨 escalate」先例）
        improved = metric_degradation < 0
        for name, deg in (("cost", cost_degradation), ("tokens", tokens_degradation)):
            if deg is not None and improved and deg > self.cost_trade_escalation_threshold:
                reason = (
                    f"metric improved {(-metric_degradation):.1%} but {name} "
                    f"cost rose {deg:.1%} (trade escalation threshold "
                    f"{self.cost_trade_escalation_threshold:.0%}) — escalated"
                )
                self._emit_cost_gate({
                    "verdict": "escalated",
                    "dimension": name,
                    "degradation": round(deg, 6),
                    "threshold": self.cost_trade_escalation_threshold,
                    "metric_degradation": round(metric_degradation, 6),
                })
                return self._cost_result(
                    baseline=baseline,
                    candidate=candidate,
                    reason=reason,
                    cost_degradation=cost_degradation,
                    tokens_degradation=tokens_degradation,
                    metric_degradation=metric_degradation,
                    escalated=True,
                )

        self._emit_cost_gate({
            "verdict": "passed",
            "cost_degradation": None if cost_degradation is None else round(cost_degradation, 6),
            "tokens_degradation": (
                None if tokens_degradation is None else round(tokens_degradation, 6)
            ),
            "cost_samples": (cost_pair[1] if cost_pair else 0),
            "tokens_samples": (tokens_pair[1] if tokens_pair else 0),
            # b2 归一化在 0 饱和：基线已超预算 → 本维度对进一步变贵失明
            "cost_saturated": cost_saturated,
        })
        return None, cost_degradation, tokens_degradation

    def _emit_cost_gate(self, data: dict[str, Any]) -> None:
        """成本闸事件。发不出去不得影响判定（与 skip monitor 同原则）。"""
        try:
            self.emit("genome.cost_gate", data)
        except Exception:  # noqa: BLE001
            logger.warning("cost gate emit failed", exc_info=True)

    # ── held-out 门（C9）───────────────────────────────────────────

    def _heldout_unavailable(
        self, reason: str, *, policy: str, kind: str
    ) -> tuple[ValidationResult, float | None, float | None, float | None]:
        """held-out 不可用的统一结果：**一律 ok=False**（不得 activate）。

        ``policy="reject_escalate"`` → ``escalated=True``（不计回滚计数：
        数据不在是运维问题）；``"reject"`` → 计入连续失败（更严的部署选择）。

        emit ``genome.heldout_gate``（``verdict="unavailable"``）——这是
        「held-out 闸被跳过」的唯一可见通道；静默降级成单集正是评审
        列为 NO-GO 的形态。
        """
        escalated = policy != "reject"
        self._emit_heldout_gate({
            "verdict": "unavailable",
            "kind": kind,                     # "unwired" | "missing"
            "policy": policy,
            "reason": reason,
            "min_tasks": _min_heldout_tasks(),
        })
        logger.warning("held-out gate unavailable (%s): %s", kind, reason)
        return (
            ValidationResult(
                ok=False,
                reason=f"held-out unavailable ({kind}): {reason} — not activated",
                escalated=escalated,
                heldout_checked=False,
            ),
            None, None, None,
        )

    def _check_heldout(
        self,
        proposal: GenomeProposal,
        holdout: HoldoutSet,
        *,
        metric_degradation: float,
    ) -> tuple[ValidationResult | None, float | None, float | None, float | None]:
        """held-out 门。返回 ``(判定, 基线分, 候选分, 退化率)``。

        判定为 ``None`` = 放行（后三个值回填到通过结果，供审计）。

        **顺序**：先查接线（两个 callable 是否都在），再跑评测。
        评测异常分两类：
        - ``HeldoutUnavailable``（文件缺失/空/条数不足）→ 走
          ``heldout_missing_policy``；
        - 其它异常（LLM 挂了）→ escalate，不冒充「候选变差」。

        **分歧 kill-switch**：held-in 退化率 < ``-DIVERGENCE_HELDIN_IMPROVEMENT``
        （确实改进）且 held-out 退化率 > 阈值 → escalate + ``trip_halt``
        + emit ``genome.heldout_divergence``。这是「过拟合到验证集」的
        结构性特征：模型把 held-in 刷上去了，泛化却退了。
        """
        if self.heldout_replay is None or self.heldout_baseline is None:
            if not self.heldout_required:
                # 显式关闭（只测 held-in 判定机制）。**仍然留痕**：静默
                # 退化到单集正是评审 NO-GO 的形态，哪怕是测试。
                self._emit_heldout_gate({
                    "verdict": "skipped_disabled",
                    "kind": "unwired",
                    "reason": "heldout_required=False (held-in only mode)",
                })
                logger.warning(
                    "held-out gate disabled (heldout_required=False): "
                    "held-in only judgement for %s", proposal.proposal_id,
                )
                return None, None, None, None
            return self._heldout_unavailable(
                "held-out gate not wired (heldout_replay/heldout_baseline "
                "are None)",
                policy=self.heldout_unwired_policy,
                kind="unwired",
            )

        # 延迟导入：validation_gate 是 L3，golden_sets 也是 L3（evals 归 L3），
        # 但顶层 import 会把「评测实现」绑进闸的模块图；延迟导入让只跑
        # held-in 的老调用方（测试替身 / 降级路径）不必拉起 evals 依赖链。
        from agentnodes.evals.golden_sets import HeldoutUnavailable

        try:
            heldout_baseline = float(self.heldout_baseline())
            heldout_candidate = float(self.heldout_replay(proposal, holdout))
        except HeldoutUnavailable as exc:
            return self._heldout_unavailable(
                str(exc), policy=self.heldout_missing_policy, kind="missing",
            )
        except Exception as exc:  # noqa: BLE001 —— 基础设施故障 ≠ 变差
            self._emit_heldout_gate({
                "verdict": "unavailable",
                "kind": "error",
                "policy": self.heldout_missing_policy,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            logger.warning("held-out eval failed: %s", exc, exc_info=True)
            return (
                ValidationResult(
                    ok=False,
                    reason=f"held-out eval failed: {exc} — escalated",
                    escalated=True,
                    heldout_checked=False,
                ),
                None, None, None,
            )

        if heldout_baseline == 0:
            # 基线 0 分：任何候选都"无穷好"或"无法比"。不能判定。
            self._emit_heldout_gate({
                "verdict": "unavailable",
                "kind": "zero_baseline",
                "policy": self.heldout_missing_policy,
                "reason": "held-out baseline metric is 0",
            })
            return (
                ValidationResult(
                    ok=False,
                    reason=(
                        "held-out baseline metric is 0; cannot judge — "
                        "not activated"
                    ),
                    baseline_heldout=heldout_baseline,
                    candidate_heldout=heldout_candidate,
                    escalated=True,
                    heldout_checked=False,
                ),
                heldout_baseline, heldout_candidate, None,
            )

        degradation = (
            heldout_baseline - heldout_candidate
        ) / abs(heldout_baseline)

        # 门 1：held-out 显著退化 → 拒（与 held-in 同阈值）
        if degradation > self.heldout_threshold:
            # 分歧 kill-switch：held-in 确实改进 + held-out 退化
            improving = metric_degradation < -DIVERGENCE_HELDIN_IMPROVEMENT
            if improving:
                return (
                    self._divergence_result(
                        proposal,
                        reason=(
                            f"held-in improved {(-metric_degradation):.1%} but "
                            f"held-out degraded {degradation:.1%} "
                            f"(threshold {self.heldout_threshold:.0%}) — "
                            "overfitting signal, halting"
                        ),
                        metric_degradation=metric_degradation,
                        heldout_degradation=degradation,
                        baseline_heldout=heldout_baseline,
                        candidate_heldout=heldout_candidate,
                    ),
                    heldout_baseline, heldout_candidate, degradation,
                )
            self._emit_heldout_gate({
                "verdict": "rejected",
                "degradation": round(degradation, 6),
                "threshold": self.heldout_threshold,
                "baseline": round(heldout_baseline, 6),
                "candidate": round(heldout_candidate, 6),
                "heldin_degradation": round(metric_degradation, 6),
            })
            return (
                ValidationResult(
                    ok=False,
                    reason=(
                        f"held-out degraded {degradation:.1%} "
                        f"(threshold {self.heldout_threshold:.0%}) — not activated"
                    ),
                    degradation=metric_degradation,
                    baseline_heldout=heldout_baseline,
                    candidate_heldout=heldout_candidate,
                    heldout_degradation=degradation,
                    heldout_checked=True,
                ),
                heldout_baseline, heldout_candidate, degradation,
            )

        # 与 held-in 一致：held-out 上"暴涨"同样可疑（奖励黑客信号）。
        if degradation < -self.heldout_threshold:
            self._emit_heldout_gate({
                "verdict": "escalated",
                "kind": "implausible_jump",
                "degradation": round(degradation, 6),
                "threshold": self.heldout_threshold,
                "baseline": round(heldout_baseline, 6),
                "candidate": round(heldout_candidate, 6),
            })
            return (
                ValidationResult(
                    ok=False,
                    reason=(
                        f"held-out improved {(-degradation):.1%} — implausible "
                        "jump, possible reward hacking; escalated"
                    ),
                    degradation=metric_degradation,
                    baseline_heldout=heldout_baseline,
                    candidate_heldout=heldout_candidate,
                    heldout_degradation=degradation,
                    heldout_checked=True,
                    escalated=True,
                ),
                heldout_baseline, heldout_candidate, degradation,
            )

        self._emit_heldout_gate({
            "verdict": "passed",
            "degradation": round(degradation, 6),
            "threshold": self.heldout_threshold,
            "baseline": round(heldout_baseline, 6),
            "candidate": round(heldout_candidate, 6),
            "heldin_degradation": round(metric_degradation, 6),
        })
        return None, heldout_baseline, heldout_candidate, degradation

    def _divergence_result(
        self,
        proposal: GenomeProposal,
        *,
        reason: str,
        metric_degradation: float,
        heldout_degradation: float,
        baseline_heldout: float,
        candidate_heldout: float,
    ) -> ValidationResult:
        """分歧 kill-switch：escalate + trip_halt + 独立告警事件。

        ``trip_halt`` 未注入（None）时**不假装成功**：事件里
        ``halted=False`` + ``halt_error`` 说明原因，escalate 仍然发生
        ——护栏降级必须可见（评审「D4 护栏失效」的同一原则）。
        """
        halted = False
        halt_error = ""
        if self.trip_halt is None:
            halt_error = "trip_halt not injected (assembly root must pass it)"
            logger.warning("held-out divergence: %s", halt_error)
        else:
            reason_text = (
                f"held-out divergence on {proposal.proposal_id}: "
                f"held-in improved {(-metric_degradation):.1%} but "
                f"held-out degraded {heldout_degradation:.1%}"
            )
            try:
                # ``halt.trip_halt`` 的签名是 ``( *, reason )``——关键字限定。
                # 先按关键字调（与生产实现一致），失败再退回位置参数（兼容
                # 更宽松的注入实现）。两条都失败才算护栏不可用。
                try:
                    self.trip_halt(reason=reason_text)
                except TypeError:
                    self.trip_halt(reason_text)
                halted = True
            except Exception as exc:  # noqa: BLE001 —— 停不下来也不能吞分歧
                halt_error = f"{type(exc).__name__}: {exc}"
                logger.warning("trip_halt failed: %s", exc, exc_info=True)

        self._emit_heldout_gate({
            "verdict": "divergence",
            "degradation": round(heldout_degradation, 6),
            "threshold": self.heldout_threshold,
            "heldin_degradation": round(metric_degradation, 6),
            "baseline": round(baseline_heldout, 6),
            "candidate": round(candidate_heldout, 6),
        })
        self._emit_divergence({
            "proposal_id": proposal.proposal_id,
            "target_component": proposal.target_component,
            "heldin_degradation": round(metric_degradation, 6),
            "heldout_degradation": round(heldout_degradation, 6),
            "heldout_threshold": self.heldout_threshold,
            "halted": halted,
            "halt_error": halt_error,
            "reason": reason,
        })
        return ValidationResult(
            ok=False,
            reason=reason,
            degradation=metric_degradation,
            baseline_heldout=baseline_heldout,
            candidate_heldout=candidate_heldout,
            heldout_degradation=heldout_degradation,
            heldout_checked=True,
            heldout_divergence=True,
            escalated=True,
        )

    def _emit_heldout_gate(self, data: dict[str, Any]) -> None:
        """held-out 门事件。发不出去不得影响判定。"""
        try:
            self.emit("genome.heldout_gate", data)
        except Exception:  # noqa: BLE001
            logger.warning("held-out gate emit failed", exc_info=True)

    def _emit_divergence(self, data: dict[str, Any]) -> None:
        """分歧告警事件。与 ``genome.heldout_gate`` 分开：这条是**事故**，
        监控按这个 kind 直接告警；门事件是常规审计流水。"""
        try:
            self.emit("genome.heldout_divergence", data)
        except Exception:  # noqa: BLE001
            logger.warning("held-out divergence emit failed", exc_info=True)

    # ── 状态推进 ────────────────────────────────────────────────────

    @staticmethod
    def _reject(
        proposal: GenomeProposal, reason: str, *, escalated: bool
    ) -> GenomeProposal:
        status = ProposalStatus.VALIDATING
        working = (
            proposal
            if proposal.status is status
            else proposal.to_validating()
        )
        return working.to_rejected(reason)

    def _rollback(
        self, rejected: GenomeProposal, reason: str
    ) -> GenomeProposal:
        rolled = rejected.to_rolled_back(
            f"rolled back after {ROLLBACK_AFTER_FAILURES} consecutive failures: {reason}"
        )
        new_version = self.genome.rollback()
        self.emit("genome.rollback", {
            "proposal_id": rolled.proposal_id,
            "target_component": rolled.target_component,
            "reason": rolled.rejection_reason,
            "new_version_id": new_version,
            "consecutive_failures": self._consecutive_failures,
        })
        self._consecutive_failures = 0
        return rolled

    # ── 观测 ────────────────────────────────────────────────────────

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def history(self) -> list[ValidationResult]:
        return list(self._history)


__all__ = [
    "COST_DEGRADATION_THRESHOLD",
    "COST_TRADE_ESCALATION_THRESHOLD",
    "DEGRADATION_THRESHOLD",
    "DIVERGENCE_HELDIN_IMPROVEMENT",
    "GenomeVersionStore",
    "HELDOUT_DEGRADATION_THRESHOLD",
    "HELDOUT_MISSING_POLICY",
    "HELDOUT_POLICIES",
    "HELDOUT_UNWIRED_POLICY",
    "MIN_COST_SAMPLES",
    "ROLLBACK_AFTER_FAILURES",
    "ReplayOutcome",
    "ValidationGate",
    "ValidationResult",
    "TierViolation",
]
