"""RSI 闭环的装配（组装根，L1）。

把阶段三的五个零件接成一条**能跑的**链路：

    holdout(只读) → EvolutionAgent(只读证据) → GenomeProposal
                  → ValidationGate(±20% 闸 / 连续 3 次失败回滚)
                  → GenomeVersionStore(Tier 1 生效)

**这个模块存在的理由**：五个零件各自有完整测试，但没有任何地方把它们
串起来——`EvolutionAgent(` / `ValidationGate(` / `IsolatedSlotReplay(`
在生产代码里出现次数为 0。零件齐全 ≠ 链路通了；本模块是那条链路。

**为什么在 L1 而不是 apps 内部**：它要同时拿 kernel 的容量闸、基因组
版本链与 apps 的 Agent/Gate/回放实现。放 apps 里会逼 kernel 侧反向依赖；
放 kernel 里会破 H1。L1 组装根是唯一合法位置。

**只读保证**：整条链路没有任何写业务表的调用。holdout 是 frozen 且无写
方法（H5），EvolutionAgent 的五个工具全部只读或只构造提案对象（H3），
回放只读 holdout。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class RsiLoop:
    """RSI Tier 1 闭环的一次装配结果。

    调用 ``run_once()`` 完成一轮：读证据 → 出提案 → 验证 → 需要时回滚。
    不是常驻线程——驱动由宿主决定（CLI 命令 / 定时任务 / 测试）。
    """

    def __init__(
        self,
        *,
        agent: Any,
        gate: Any,
        holdout_source: Any,
        genome: Any,
        replay: Any = None,
        seed: int = 0,
        limit: int = 20,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._agent = agent
        self._gate = gate
        self._holdout_source = holdout_source
        self._genome = genome
        self._replay = replay
        self._seed = seed
        self._limit = limit
        self._emit = emit or (lambda kind, data: None)

    # ── 一轮 ────────────────────────────────────────────────────────

    def run_once(
        self,
        task_signature: str,
        *,
        objective: str = "",
        role: str = "",
        tool_set: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """跑一轮 RSI：抽 holdout → 出提案 → 验证。

        返回本轮摘要（供 CLI 打印 / 测试断言）。**永不抛出**：RSI 是
        增强通道，任何一环失败都不该让宿主进程崩掉——失败写进返回值和
        ``rsi.cycle_skipped`` 事件。

        :param task_signature: 用于抽 holdout 的签名
        :param objective/role/tool_set: 传给 ``EvolutionAgent.propose()``；
            它们会**重新计算**签名（``compute_task_signature``），因此调用方
            需要保证算出来与 ``task_signature`` 一致，否则抽的样本与提案的
            对象不是同一任务。
        """
        try:
            from agentnodes.kernel.evolution.holdout import sample

            holdout = sample(
                task_signature,
                self._seed,
                source=self._holdout_source,
                genome=self._genome,
                limit=self._limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("holdout sampling failed: %s", exc)
            self._emit("rsi.cycle_skipped", {
                "reason": "holdout_failed", "error": str(exc),
                "task_signature": task_signature,
            })
            return {"ok": False, "reason": "holdout_failed", "error": str(exc)}

        self._emit("rsi.holdout_sampled", {
            "task_signature": task_signature,
            "size": len(holdout),
            "seed": self._seed,
        })

        # 空 holdout：没有可验证的证据，不出提案（出了也验证不了）。
        if not len(holdout):
            self._emit("rsi.cycle_skipped", {
                "reason": "empty_holdout", "task_signature": task_signature,
            })
            return {"ok": False, "reason": "empty_holdout", "holdout_size": 0}

        try:
            proposal = self._agent.propose(
                objective=objective,
                role=role,
                tool_set=tool_set,
                seed=self._seed,
                limit=self._limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("proposal failed: %s", exc)
            self._emit("rsi.cycle_skipped", {
                "reason": "proposal_failed", "error": str(exc),
            })
            return {"ok": False, "reason": "proposal_failed", "error": str(exc)}

        if proposal is None:
            self._emit("rsi.no_proposal", {"task_signature": task_signature})
            return {"ok": False, "reason": "no_proposal", "holdout_size": len(holdout)}

        self._emit("rsi.proposal_created", {
            "proposal_id": proposal.proposal_id,
            "target_component": proposal.target_component,
        })

        verdict = self._gate.validate(proposal, holdout)
        status = getattr(getattr(verdict, "status", None), "value", None)
        self._emit("rsi.validated", {
            "proposal_id": getattr(verdict, "proposal_id", ""),
            "status": status,
            "task_signature": task_signature,
        })
        return {
            "ok": status == "approved",
            "reason": status or "unknown",
            "proposal_id": getattr(verdict, "proposal_id", ""),
            "target_component": getattr(verdict, "target_component", ""),
            "holdout_size": len(holdout),
            "genome_version": _version_of(self._genome),
        }


def _version_of(genome: Any) -> str:
    try:
        return str(genome.current().version)
    except Exception:  # noqa: BLE001
        return ""


def build_rsi_loop(gw: Any, *, seed: int = 0) -> Any | None:
    """按 Gateway 上已接线的子系统组装 RSI 闭环。

    缺任何一环（容量闸 / 基因组 / TicketSink 事件流）都返回 None 并
    发 ``rsi.wiring_skipped``——静默返回 None 会让"RSI 没跑"变成
    无法诊断的幽灵问题。

    :param gw: 已由 ``wire_gateway`` 接线的 Gateway
    :param seed: holdout 抽样种子（附录 C：必须显式给，不许默认随机）

    **C9 seed 进程外注入**：若设置了 ``AN_GOLDEN_SEED``，它**覆盖**本参数。
    理由：抽样种子是评测指纹的一部分，让同进程的调用方决定种子等于把
    「按固定抽样过拟合」的入口留在被检方手里。未设时沿用 ``seed``，并
    emit ``rsi.golden_seed``（``source=default``）让默认值可见。
    """
    emit = getattr(gw, "_combined_sink", None) or (lambda k, d: None)
    genome = getattr(gw, "genome", None)
    capacity = getattr(gw, "capacity", None)

    seed = _resolve_seed(seed, emit=emit)

    missing = [
        name for name, val in (("genome", genome), ("capacity", capacity))
        if val is None
    ]
    if missing:
        emit("rsi.wiring_skipped", {"missing": missing})
        return None

    try:
        from agentnodes.kernel.evolution.default_policy import default_policy
        from agentnodes.kernel.evolution.evolution_agent import EvolutionAgent
        from agentnodes.kernel.evolution.holdout import (
            EventStoreReportSource,
        )
        from agentnodes.kernel.evolution.isolated_replay import (
            IsolatedSlotReplay,
        )
        from agentnodes.kernel.evolution.validation_gate import ValidationGate

        # 事件源：Gateway 的事件存储（只读 replay）。
        store = getattr(gw, "store", None)
        if store is None:
            emit("rsi.wiring_skipped", {"missing": ["event_store"]})
            return None
        source = EventStoreReportSource(store)

        # ── 真实验证信号（用户裁决：固定抽样 10 条 golden eval） ──
        #
        # 早先实现用 ``holdout_replay`` 的确定性代理分（状态权重 + 提案
        # 哈希偏移）——那个偏移让"好坏"取决于哈希值而非实际效果，能测
        # 链路但**不能证明提案真的更好**。换成真实 golden eval：
        #
        #   用当前基因组装配 agent → 跑 10 条 golden task → scorecard 打分
        #
        # 每轮约 10 次 LLM 调用。抽到的 10 条由 seed 固定 → 两次验证可比。
        replay_fn, golden_tasks = _build_replay(gw, seed=seed, emit=emit)
        replay = IsolatedSlotReplay(gate=capacity, replay_fn=replay_fn)

        agent = EvolutionAgent.with_capabilities(
            journal=source, scoreboard=source, validation=source, holdout=source,
            policy=default_policy,
        )

        # ── C9 双集双门：held-out 门 + 分歧 kill-switch ──────────────
        #
        # held-out 集住**仓库外**（``ensure_data_dir()/eval_heldout/``），
        # 由 ``evals.golden_sets`` 负责路径与加载；gate 只收两个 callable。
        # ``trip_halt`` 必须从这里注入：gate 在 L3，直接 import
        # ``interfaces.cli.halt`` 会新增一条 L3→L1 上行（层门禁只许降）。
        #
        # held-out 缺失时 gate **不会**放行——它会 escalate 并 emit
        # ``genome.heldout_gate``；``rsi.heldout_status`` 提前把缺失告知
        # 运维，免得每轮 RSI 都在"看似跑通、实际全拒"的状态里空转。
        gate = ValidationGate(
            genome=genome,
            emit=emit,
            replay=replay,
            baseline_metric=_baseline_metric(gw, golden_tasks, seed),
            **_heldout_kwargs(gw, seed=seed, emit=emit),
        )
        return RsiLoop(
            agent=agent,
            gate=gate,
            holdout_source=source,
            genome=genome,
            replay=replay,
            seed=seed,
            emit=emit,
        )
    except Exception as exc:  # noqa: BLE001 —— 装配失败不得影响 chat
        logger.warning("rsi loop assembly failed: %s", exc)
        emit("rsi.wiring_skipped", {"error": str(exc)})
        return None


def _resolve_seed(
    seed: int, *, emit: Callable[[str, dict], None]
) -> int:
    """C9：``AN_GOLDEN_SEED`` 优先于调用方传值；未设则用传入值并 emit 提示。

    ``heldout_gate_kwargs`` 与 held-in 抽样必须用**同一个** seed——两侧
    抽到不同任务时，基线/候选的对比仍成立，但"同一轮验证"的可审计性
    就断了（审计时无法用一条 seed 复现整轮）。所以本函数是唯一解析点，
    结果同时喂给两条通道。
    """
    try:
        from agentnodes.evals.golden_sets import SEED_ENV, resolve_golden_seed

        resolved, source = resolve_golden_seed(emit=emit, default=int(seed))
        if source.startswith("default") and int(seed) != resolved:
            # 不该发生（default 就是 seed），留作断言式的防御
            logger.warning(
                "seed resolution drifted: requested=%s resolved=%s", seed, resolved,
            )
        return resolved
    except Exception as exc:  # noqa: BLE001 —— evals 不可用时用传入值
        logger.warning("seed injection unavailable, using seed=%s: %s", seed, exc)
        return int(seed)


def _heldout_kwargs(
    gw: Any, *, seed: int, emit: Callable[[str, dict], None]
) -> dict[str, Any]:
    """C9：构造 ``ValidationGate`` 的 held-out 关键字参数（失败即空 dict）。

    失败（``evals.golden_sets`` 不可用）时返回 ``{}``——此时 gate 走
    ``heldout_unwired_policy``（拒 + escalate），**不会**退回单集通过。
    这是刻意的：装配半边失败不等于验证可以降级。

    ``trip_halt`` 在这里导入并注入：kill-switch 必须落盘（halt.py 已改
    持久化），而 gate 在 L3 不能自己 import L1。
    """
    try:
        from agentnodes.evals.golden_sets import heldout_gate_kwargs
        from agentnodes.interfaces.cli.halt import trip_halt

        return heldout_gate_kwargs(
            seed=seed, emit=emit, trip_halt=trip_halt,
        )
    except Exception as exc:  # noqa: BLE001 —— 装配失败按未接线处理
        logger.warning("held-out gate wiring failed (gate will reject): %s", exc)
        emit("rsi.heldout_wiring_skipped", {"error": str(exc)})
        return {}


def _build_replay(
    gw: Any, *, seed: int, emit: Callable[[str, dict], None]
) -> tuple[Any, list[Any]]:
    """构造 replay 与它用的 golden task 集。

    优先真实 golden eval；``evals`` 不可用或 golden 文件缺失时退回代理分
    并 emit ``rsi.replay_degraded``——**静默退回不可接受**：那会让"验证
    无效"看起来像"验证通过"。
    """
    try:
        from pathlib import Path

        from agentnodes.evals.scorecard import load_golden_tasks
        from agentnodes.kernel.evolution.golden_eval import (
            DEFAULT_SAMPLE_SIZE,
            make_golden_replay,
        )

        # ``evals`` 是 ``agentnodes`` 的子包（``src/agentnodes/evals``），不
        # 是仓库根下的独立包。本文件在 ``src/agentnodes/interfaces/``：
        #   parents[0] = interfaces
        #   parents[1] = agentnodes    ← evals 的父目录
        #   parents[2] = src
        #   parents[3] = 仓库根
        # 早先写成 ``parents[4]``（= 仓库的上一级，如 D:\Github），路径必然
        # 不存在 → 每轮都静默降级到代理分（评审簇二：golden 路径错一级恒降级）。
        #
        # C9 双集：held-in 门只该跑 **held-in** 子集。``golden_tasks.jsonl``
        # 在拆分后被改成 held-in 的兼容副本（见 split_golden_sets.py），
        # 所以这里的路径不变；显式优先 ``golden_tasks_heldin.jsonl``，让
        # 「读的是哪一份」在代码里可读。
        evals_dir = Path(__file__).resolve().parents[1] / "evals"
        golden_path = evals_dir / "golden_tasks_heldin.jsonl"
        if not golden_path.exists():
            golden_path = evals_dir / "golden_tasks.jsonl"
        if not golden_path.exists():
            # 安装形态 / 非源码运行时兜底：先看 cwd 下的 evals，再按已 import
            # 的 ``agentnodes.evals`` 包的 __file__ 定位（包内资源目录一律
            # 用「目标模块 __file__.parent」定位，不要数 parent 层数拼包内段
            # ——那会被 scripts/arch/check_path_literals.py 当层名门禁报错）。
            import agentnodes.evals as _evals_pkg

            for candidate in (
                Path.cwd() / "evals" / "golden_tasks_heldin.jsonl",
                Path.cwd() / "evals" / "golden_tasks.jsonl",
                Path(_evals_pkg.__file__).resolve().parent
                / "golden_tasks_heldin.jsonl",
                Path(_evals_pkg.__file__).resolve().parent / "golden_tasks.jsonl",
            ):
                if candidate.exists():
                    golden_path = candidate
                    break

        tasks = load_golden_tasks(golden_path)
        if not tasks:
            raise RuntimeError(f"no golden tasks at {golden_path}")

        emit("rsi.replay_selected", {
            "kind": "golden_eval",
            "tasks_available": len(tasks),
            "sample_size": DEFAULT_SAMPLE_SIZE,
            "seed": seed,
        })
        return (
            make_golden_replay(
                tasks, seed=seed, size=DEFAULT_SAMPLE_SIZE,
                apply_proposal=_make_applier(gw),
            ),
            tasks,
        )
    except Exception as exc:  # noqa: BLE001 —— 降级但必须可见
        logger.warning("golden eval unavailable, falling back to proxy: %s", exc)
        emit("rsi.replay_degraded", {
            "reason": str(exc),
            "fallback": "holdout_proxy",
            "note": "代理分不能证明提案更好，只用于保持链路可跑",
        })
        from agentnodes.kernel.evolution.holdout_replay import make_replay

        return make_replay(), []


def _make_applier(gw: Any) -> Callable[[Any], Any]:
    """把提案临时应用到基因组，返回复原函数。

    **这是"验证"与"自证"的分界线**：不应用提案，候选评测跑的是当前配置，
    分数必然与基线相同，验证闸会算出 0% 退化并判为通过——激活一个从未
    被真正验证过的提案。

    实现：``genome.activate(proposal_id, {component: change})`` 临时升版
    （这会持久化，但紧接着的 undo 会回滚到原版本），评测结束后
    ``rollback_to(原版本)`` 精确复原。若验证通过，``ValidationGate`` 自己
    会再调一次 ``activate``——那时才是真正的生效。
    """
    genome = getattr(gw, "genome", None)

    def _apply(proposal: Any) -> Any:
        if genome is None:
            return None
        try:
            before = genome.current_version_id()
            genome.activate(
                f"replay:{getattr(proposal, 'proposal_id', '')}",
                {getattr(proposal, "target_component", ""): getattr(proposal, "change", {})},
            )

            def _undo() -> None:
                try:
                    genome.rollback_to(before)
                except Exception:  # noqa: BLE001
                    logger.warning("replay undo failed", exc_info=True)

            return _undo
        except Exception as exc:  # noqa: BLE001 —— 应用失败按"无法验证"处理
            logger.warning("proposal apply failed: %s", exc)
            return None

    return _apply


def _baseline_metric(
    gw: Any, tasks: list[Any], seed: int
) -> Callable[[], float | None]:
    """基线：当前（未应用提案的）配置在 golden 抽样上的均分。

    **必须与候选用同一批任务**（同 seed、同 size），否则基线分与候选分
    不可比——那是"拿苹果比橘子"，退化阈值会算出无意义的百分比。

    无 golden task（降级路径）时返回 None，``ValidationGate`` 对 None 的
    处理是 escalate（"无法判断"），不是静默放行。

    **返回值是 ``ReplayOutcome``**（D3 双闸）：只返回能力分会让成本闸在
    候选侧有样本、基线侧恒为 None → 成本闸永远 skipped，整道闸形同虚设。
    这里把同一轮评测的 cost / tokens 一起交出去，两侧口径一致。

    **滚动中位数（C10 / 评审 F7）**：旧实现 ``cached["value"]`` 无条件
    缓存单轮值，长驻进程里多轮改进后基线永久停留在第一轮，候选正常提升
    就会被读成"暴涨 → reward hacking"（假阳性）。现在的口径：

    - 每次调用都**重新评测**，把结果推入有界滚动窗口，返回窗口**中位数**
    - 窗口按 genome_version 分区：基因组升版 = 基线失效，旧样本不再
      代表"当前配置"
    - 中位数而非均值：单轮偶发低分（网络抖动/超时率升高）不会把基线
      拖低——那正是"监考被收买"的入口

    ``run_golden_eval`` 返回 ``skipped`` 时**不写入窗口**（基线硬条款：
    无效轮次不得污染基线），返回窗口现状；窗口空则 None。
    """
    # 有界窗口：最近 N 次有效基线观察。N=5 在"抗抖动"与"跟上真实漂移"
    # 之间取折中，且必为奇数以便中位数落在一个真实观测上。
    window_size = 5
    observations: list[dict[str, Any]] = []
    window_version: dict[str, str] = {}

    def _baseline() -> Any:
        if not tasks:
            return None

        # 基因组升版 → 窗口失效（旧版本下的分数不代表当前基线）
        version = _genome_version_id(gw)
        if version and window_version.get("version") != version:
            observations.clear()
            window_version["version"] = version

        try:
            from agentnodes.kernel.evolution.golden_eval import (
                DEFAULT_SAMPLE_SIZE,
                run_golden_eval,
            )

            result = run_golden_eval(tasks, seed=seed, size=DEFAULT_SAMPLE_SIZE)
            if result.skipped:
                # 无效轮次不入窗口（不装 0 分、不污染基线）
                logger.warning(
                    "baseline golden eval skipped: %s (window n=%d)",
                    result.skip_reason, len(observations),
                )
            else:
                observations.append({
                    "metric": float(result.metric),
                    "cost": result.cost,
                    "cost_samples": int(result.cost_samples),
                    "tokens": result.tokens,
                    "tokens_samples": int(result.tokens_samples),
                })
                if len(observations) > window_size:
                    del observations[0: len(observations) - window_size]
            if not observations:
                return None
            return _aggregate_baseline(observations)
        except Exception as exc:  # noqa: BLE001
            logger.warning("baseline golden eval failed: %s", exc)
            return _aggregate_baseline(observations) if observations else None

    return _baseline


def _aggregate_baseline(observations: list[dict[str, Any]]) -> Any:
    """把窗口内的多轮观察汇成 ``ReplayOutcome``（各维度独立取中位数）。

    ``*_samples`` 取窗口内**最小**的有效任务数：中位数跨轮平滑了值，
    但没有凭空产生独立任务样本——最小样本数检查必须按真实任务数算。
    """
    from agentnodes.kernel.evolution.validation_gate import ReplayOutcome

    metrics = [o["metric"] for o in observations]
    costs = [o["cost"] for o in observations if o["cost"] is not None]
    tokens = [o["tokens"] for o in observations if o["tokens"] is not None]
    cost_counts = [o["cost_samples"] for o in observations if o["cost"] is not None]
    token_counts = [o["tokens_samples"] for o in observations if o["tokens"] is not None]

    return ReplayOutcome(
        metric=_median(metrics),
        cost=_median(costs) if costs else None,
        cost_samples=min(cost_counts) if cost_counts else 0,
        tokens=_median(tokens) if tokens else None,
        tokens_samples=min(token_counts) if token_counts else 0,
    )


def _median(values: list[float]) -> float:
    """中位数（不改变输入）。滚动基线用它抗单轮离群值。"""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _genome_version_id(gw: Any) -> str:
    """当前 genome_version_id；拿不到返回空串（空串 = 不做分区失效）。"""
    genome = getattr(gw, "genome", None)
    if genome is None:
        return ""
    try:
        return str(genome.current_version_id())
    except Exception:  # noqa: BLE001
        return ""


def _baseline_from_source(source: Any, genome: Any) -> Callable[[], float | None]:
    """基线指标：当前 genome 生效之前那段 holdout 的平均分。

    没有历史（首次运行、事件流为空）时返回 None —— ``ValidationGate``
    对 None 的处理是 escalate（"无法判断优劣，需人工确认"），不是静默
    放行。这正是我们要的：没有基线就不要假装能判断退化。
    """

    def _baseline() -> float | None:
        try:
            reports = list(source.iter_assignment_reports())
        except Exception:  # noqa: BLE001
            return None
        if not reports:
            return None
        ready = sum(1 for r in reports if r.get("status") == "ready")
        return ready / len(reports)

    return _baseline


__all__ = ["RsiLoop", "build_rsi_loop"]
