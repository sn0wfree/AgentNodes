"""业务侧能力注册入口（ADR-003 端口化）。

kernel 侧不再 ``from agentnodes.apps.db import get_connection``，
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
    from agentnodes.kernel.agent.capability import CapabilityRegistry

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


# ── evolution：已迁到 kernel/evolution/（2026-11-05 批次 8）─────────
#
# 10 个 evolution.* kind 不再由 apps 注册。evolution 是**内核的自我进化
# 机制**，已从 apps/（L2）搬到 kernel/evolution/（L3），工具工厂改由
# EvolutionAgent.with_capabilities() 在 L3 内部注册（同层依赖合法）。
#
# 这消除了原先的一处结构倒置：L2 的工厂 import L3 模块，只为给 L3
# 提供它自己的实现。

# ── 业务内置工具（批次 6 归位 L2）─────────────────────────────────────
#
# 这 6 个模块原住 ``kernel/agent/builtin_tools/``（L3），但调用的全是业务
# 能力，属「L3 里的 L2」。批次 6 起住在 ``apps/builtin_tools/``，由本文件
# 注册成 ``tool.*`` kind，kernel 的 ``_register_business_tools`` 从 registry 拉。
#
# **契约**：每个工厂返回 ``list[BaseTool]``（一个模块可产多个工具）。
# 工厂内部 import 在本层（apps → kernel 方向合法），且失败时由 kernel 侧
# 记 warning 并跳过该组——业务能力缺失不阻断框架启动。


def _tool_factor(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.factor_tools import (
        ComputeFactorTool,
        FactorAnalysisTool,
        FactorCrossSectionalAnalysis,
        FactorICDecay,
        FactorQuintileReturns,
        FactorTurnover,
        PatternRecognitionTool,
    )

    return [
        ComputeFactorTool(),
        FactorAnalysisTool(),
        PatternRecognitionTool(),
        FactorCrossSectionalAnalysis(),
        FactorQuintileReturns(),
        FactorICDecay(),
        FactorTurnover(),
    ]


def _tool_data(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.data_tools import (
        CheckDataTool,
        GetMarketDataTool,
        ImportDataTool,
        ListDataSourcesTool,
        SearchSymbolTool,
    )

    return [
        GetMarketDataTool(),
        ListDataSourcesTool(),
        SearchSymbolTool(),
        ImportDataTool(),
        CheckDataTool(),
    ]


def _tool_backtest(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.backtest_tools import (
        ConfigLoadStep,
        DataPrepareStep,
        DataReadinessStep,
        EngineRunStep,
        RunBacktestTool,
    )

    return [
        RunBacktestTool(),
        ConfigLoadStep(),
        DataPrepareStep(),
        DataReadinessStep(),
        EngineRunStep(),
    ]


def _tool_backtest_analysis(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.backtest_analysis_tools import (
        BenchmarkComparison,
        DrawdownAnalysis,
        StrategyCompare,
    )

    return [StrategyCompare(), DrawdownAnalysis(), BenchmarkComparison()]


def _tool_data_clean(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.data_clean_tools import DataCleanTool

    return [DataCleanTool()]


def _tool_goal(registry: "CapabilityRegistry") -> Any:
    from agentnodes.apps.builtin_tools.goal_tools import (
        AddEvidenceTool,
        CompleteGoalTool,
        CreateGoalTool,
        GetGoalStatusTool,
        ListGoalsTool,
    )

    return [
        CreateGoalTool(),
        AddEvidenceTool(),
        CompleteGoalTool(),
        GetGoalStatusTool(),
        ListGoalsTool(),
    ]


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
    # 业务内置工具（批次 6：原住 L3 builtin_tools，现归位 L2）
    "tool.factor": _tool_factor,
    "tool.data": _tool_data,
    "tool.backtest": _tool_backtest,
    "tool.backtest_analysis": _tool_backtest_analysis,
    "tool.data_clean": _tool_data_clean,
    "tool.goal": _tool_goal,
}


def register_capabilities(registry: "CapabilityRegistry") -> None:
    """Register every business capability on ``registry``.

    装配期调用一次。重复调用会因 ``register_many`` 的 fail-fast 抛错——
    装配路径必须幂等的是调用方，不是 registry。
    """
    registry.register_many(dict(CAPABILITIES))


__all__ = ["CAPABILITIES", "register_capabilities"]
