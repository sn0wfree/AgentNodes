"""业务侧能力注册入口（ADR-003 端口化）。

kernel 侧不再 ``from strategy_research.apps.db import get_connection``，
而是 ``resolve("db.get_connection")``。本模块在装配期把实现注册进去。

**注册粒度按「kind = 能力」，不是按模块**：``db.get_connection`` 被 7 处
kernel 调用点共用，注册一次即可，这是端口化相对「逐 import 搬家」的核心收益。

**为什么 factory 收 registry 参数**：允许一个 capability 组合另一个
（例如 ``ticket_sink`` 依赖 ``study.store``）。registry 传递依赖，
避免在 factory 内部再做模块级 import 回 apps——那会让 L3→L2 从 import
语句转移到运行期，门禁看不见但架构已经破了。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from strategy_research.kernel.agent.capability import CapabilityRegistry

logger = logging.getLogger(__name__)


# ── db ───────────────────────────────────────────────────────────────


# ── factor / transform ───────────────────────────────────────────────


# ── data source ──────────────────────────────────────────────────────


# ── readiness / config ───────────────────────────────────────────────


# ── data clean ───────────────────────────────────────────────────────


# ── backtest / acceptance：已整体移除（2026-11-05 做减法批次 4）──────
#
# 删除 4 个 kind：backtest.from_yaml / backtest.script / backtest.decide /
# config.runner。连带删除 apps/{backtest,backtest_engine,backtest_models,
# config_runner,engine,strategy_acceptance,factor_validate,run_card}
# 共 35 文件 5,982 行，归档 testcase/golden/apps/。
#
# 保留的 datasource.* / data.readiness / data.clean 三个 kind 属批次 5
# （数据域），它们的实现住在 apps/{data_source,data_readiness,tools}/。


# ── goal / hypothesis ────────────────────────────────────────────────
#
# 已移除（2026-11-04 做减法批次 2）：goal.store / goal.models / goal.context /
# goal.expression / hypothesis.registry / hypothesis.auto_creator 六个 kind，
# 连同 apps/goal 与 apps/hypothesis 两个包。归档见
# testcase/golden/apps/{goal,hypothesis}/。
#
# kernel 侧的 goal 注入钩子（loop._get_goal_store / context_injector /
# swarm 分支条件）全部是惰性 resolve + try/except，kind 消失后运行时
# 优雅降级为 no-op，不影响框架其他功能。

# ── study：已整体移除（2026-11-05 做减法批次 3）──────────────────────
# apps/study + apps/autoresearch 全部归档到 testcase/golden/apps/。
# 连带删除 6 个 kind：study.graph / study.store / study.scheduler /
# study.status / study.bootstrap / study.ticket_sink。
# Ticket/Assignment 是通用任务身份，已迁到 kernel/orchestration/
# （ticket.py + ticket_store.py），不再经 CapabilityRegistry 解析。


# ── evolution（阶段三） ──────────────────────────────────────────────


def _evolution_resolve_harness(registry: "CapabilityRegistry") -> Any:
    """``(task_signature) -> EffectiveHarnessConfig``。

    返回的是**函数**而非配置：装配期调用点各自带自己的 task_signature，
    提前解析一份全局配置毫无意义。
    """
    from strategy_research.apps.evolution.skill_memory import resolve

    return resolve


def _evolution_holdout_sample(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.holdout import sample

    return sample


def _evolution_proposal_cls(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.genome_proposals import GenomeProposal

    return GenomeProposal


def _evolution_validation_gate_cls(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.validation_gate import ValidationGate

    return ValidationGate


def _evolution_agent_cls(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.evolution_agent import EvolutionAgent

    return EvolutionAgent


# EvolutionAgent 的 5 个工具（施工方案 §4.3）。kind 名与工具名一一对应，
# 这样 ``EvolutionAgent.with_capabilities()`` 能按名字解析。
def _evolution_read_journal(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.tools import read_journal

    return read_journal


def _evolution_read_scoreboard(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.tools import read_scoreboard

    return read_scoreboard


def _evolution_read_validation(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.tools import read_validation

    return read_validation


def _evolution_read_holdout(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.tools import read_holdout

    return read_holdout


def _evolution_propose_genome_update(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.evolution.tools import propose_genome_update

    return propose_genome_update


# ── 注册表 ───────────────────────────────────────────────────────────

#: kind → factory。kind 命名 = ``<域>.<能力>``，与调用点的语义一一对应。
CAPABILITIES: dict[str, Callable[["CapabilityRegistry"], Any]] = {
    # db
    # factor
    # data source
    # ── db / factor / datasource / data.* 共 12 个 kind 已随批次 5 移除 ──
    # （2026-11-05）：db.get_connection / db.init_db / db.save_ohlcv_to_db /
    # db.module / factor.compute / factor.compute_error / factor.long_to_wide /
    # datasource.registry / datasource.base / datasource.detect_market /
    # data.readiness / data.clean。
    # 连带删除 apps/{data_source,tools,compute_factor,db,data_import,
    # data_readiness,preflight} 共 22 文件 6,822 行，归档 testcase/golden/apps/。
    # backtest / config.runner 四个 kind 已随批次 4 移除（2026-11-05）：
    # backtest.from_yaml / backtest.script / backtest.decide / config.runner
    # goal / hypothesis 六个 kind 已随 apps/goal、apps/hypothesis 移除
    # （2026-11-04 做减法批次 2）
    # study 六个 kind 已随 apps/study 移除（2026-11-05 做减法批次 3）；
    # Ticket/Assignment 迁到 kernel/orchestration，不再经 registry
    # evolution（阶段三）
    "evolution.resolve_harness": _evolution_resolve_harness,
    "evolution.holdout_sample": _evolution_holdout_sample,
    "evolution.proposal": _evolution_proposal_cls,
    "evolution.validation_gate": _evolution_validation_gate_cls,
    "evolution.agent": _evolution_agent_cls,
    # EvolutionAgent 工具集（§4.3，kind 名 == 工具名）
    "evolution.read_journal": _evolution_read_journal,
    "evolution.read_scoreboard": _evolution_read_scoreboard,
    "evolution.read_validation": _evolution_read_validation,
    "evolution.read_holdout": _evolution_read_holdout,
    "evolution.propose_genome_update": _evolution_propose_genome_update,
}


def register_capabilities(registry: "CapabilityRegistry") -> None:
    """Register every business capability on ``registry``.

    装配期调用一次。重复调用会因 ``register_many`` 的 fail-fast 抛错——
    装配路径必须幂等的是调用方，不是 registry。
    """
    registry.register_many(dict(CAPABILITIES))


__all__ = ["CAPABILITIES", "register_capabilities"]
