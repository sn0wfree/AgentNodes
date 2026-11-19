# -*- coding: utf-8 -*-
"""RSI 滚动中位数基线策略（WP-8 批 3b.1 · 自 interfaces/rsi_wiring.py 原样迁入）。

零行为变化：窗口 / 分区失效 / 汇总逻辑与迁移前逐行等价；``rsi_wiring
._baseline_metric`` 改为薄委托，调用点与测试锚点不变。设计依据：

- docs/design/l1-three-frontends-one-core-design-20261119.md（批 3b：
  ``BaselinePolicy`` 可测类）
- docs/design/architecture-refactor-execution-roadmap-20261119.md WP-8

**懒 import 约束**：``run_golden_eval`` 必须保持函数内 import——
tests/test_golden_eval.py 对 ``kernel.evolution.golden_eval.run_golden_eval``
打模块属性 patch，改成模块级 import 会让 6 个基线测试静默失效
（roadmap WP-8 审计风险 3）。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _median(values: list[float]) -> float:
    """中位数（不改变输入）。滚动基线用它抗单轮离群值。"""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


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


def _genome_version_id(gw: Any) -> str:
    """当前 genome_version_id；拿不到返回空串（空串 = 不做分区失效）。"""
    genome = getattr(gw, "genome", None)
    if genome is None:
        return ""
    try:
        return str(genome.current_version_id())
    except Exception:  # noqa: BLE001
        return ""


class BaselinePolicy:
    """滚动中位数基线（C10 / 评审 F7）：当前（未应用提案的）配置在
    golden 抽样上的滚动窗口均分。

    **必须与候选用同一批任务**（同 seed、同 size），否则基线分与候选分
    不可比——那是"拿苹果比橘子"，退化阈值会算出无意义的百分比。

    无 golden task（降级路径）时返回 None，``ValidationGate`` 对 None 的
    处理是 escalate（"无法判断"），不是静默放行。

    **返回值是 ``ReplayOutcome``**（D3 双闸）：只返回能力分会让成本闸在
    候选侧有样本、基线侧恒为 None → 成本闸永远 skipped，整道闸形同虚设。

    **滚动中位数**：旧实现 ``cached["value"]`` 无条件缓存单轮值，长驻
    进程里多轮改进后基线永久停留在第一轮，候选正常提升就会被读成
    "暴涨 → reward hacking"（假阳性）。现在的口径：

    - 每次调用都**重新评测**，把结果推入有界滚动窗口，返回窗口**中位数**
    - 窗口按 genome_version 分区：基因组升版 = 基线失效，旧样本不再
      代表"当前配置"
    - 中位数而非均值：单轮偶发低分（网络抖动/超时率升高）不会把基线
      拖低——那正是"监考被收买"的入口

    ``run_golden_eval`` 返回 ``skipped`` 时**不写入窗口**（基线硬条款：
    无效轮次不得污染基线），返回窗口现状；窗口空则 None。
    """

    #: 有界窗口：最近 N 次有效基线观察。N=5 在"抗抖动"与"跟上真实漂移"
    #: 之间取折中，且必为奇数以便中位数落在一个真实观测上。
    WINDOW_SIZE = 5

    def __init__(self, gw: Any, tasks: list[Any], seed: int) -> None:
        self._gw = gw
        self._tasks = tasks
        self._seed = seed
        self._observations: list[dict[str, Any]] = []
        self._window_version: dict[str, str] = {}

    def __call__(self) -> Any:
        if not self._tasks:
            return None

        # 基因组升版 → 窗口失效（旧版本下的分数不代表当前基线）
        version = _genome_version_id(self._gw)
        if version and self._window_version.get("version") != version:
            self._observations.clear()
            self._window_version["version"] = version

        try:
            from agentnodes.kernel.evolution.golden_eval import (
                DEFAULT_SAMPLE_SIZE,
                run_golden_eval,
            )

            result = run_golden_eval(
                self._tasks, seed=self._seed, size=DEFAULT_SAMPLE_SIZE,
            )
            if result.skipped:
                # 无效轮次不入窗口（不装 0 分、不污染基线）
                logger.warning(
                    "baseline golden eval skipped: %s (window n=%d)",
                    result.skip_reason, len(self._observations),
                )
            else:
                self._observations.append({
                    "metric": float(result.metric),
                    "cost": result.cost,
                    "cost_samples": int(result.cost_samples),
                    "tokens": result.tokens,
                    "tokens_samples": int(result.tokens_samples),
                })
                if len(self._observations) > self.WINDOW_SIZE:
                    del self._observations[
                        0: len(self._observations) - self.WINDOW_SIZE
                    ]
            if not self._observations:
                return None
            return _aggregate_baseline(self._observations)
        except Exception as exc:  # noqa: BLE001
            logger.warning("baseline golden eval failed: %s", exc)
            return (
                _aggregate_baseline(self._observations)
                if self._observations else None
            )
