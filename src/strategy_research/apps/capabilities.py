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


def _db_get_connection(registry: "CapabilityRegistry") -> Callable[..., Any]:
    """``apps.db.get_connection(workspace_path, read_only=False)``."""
    import strategy_research.apps.db as db

    return db.get_connection


def _db_init_db(registry: "CapabilityRegistry") -> Callable[..., Any]:
    import strategy_research.apps.db as db

    return db.init_db


def _db_save_ohlcv(registry: "CapabilityRegistry") -> Callable[..., Any]:
    import strategy_research.apps.db as db

    return db.save_ohlcv_to_db


def _db_module(registry: "CapabilityRegistry") -> Any:
    """整个 ``apps.db`` 模块（少数调用点需要多个函数时用）。

    优先用细粒度 kind；只有确实需要模块级访问时才解析这个。
    """
    import strategy_research.apps.db as db

    return db


# ── factor / transform ───────────────────────────────────────────────


def _compute_factor(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.compute_factor import compute_factor

    return compute_factor


def _factor_compute_error(registry: "CapabilityRegistry") -> type[Exception]:
    from strategy_research.apps.compute_factor import FactorComputeError

    return FactorComputeError


def _long_to_wide(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.tools.data_transforms import long_to_single_asset_wide

    return long_to_single_asset_wide


# ── data source ──────────────────────────────────────────────────────


def _data_source_registry(registry: "CapabilityRegistry") -> Any:
    """``apps.data_source.registry`` 模块（LOADER_REGISTRY / resolve_loader /
    NoAvailableSourceError / _ensure_registered 都在这里）。

    这四个名字被 4 处 import 语句引用，注册成一个模块级 capability 比
    拆成四个 kind 更诚实——它们本就是同一个注册表的门面。
    """
    import strategy_research.apps.data_source.registry as reg

    return reg


def _data_source_base(registry: "CapabilityRegistry") -> Any:
    import strategy_research.apps.data_source.base as base

    return base


def _detect_market(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.data_source.utils import detect_market

    return detect_market


# ── readiness / config ───────────────────────────────────────────────


def _check_data_readiness(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.data_readiness import check_data_readiness

    return check_data_readiness


def _config_runner(registry: "CapabilityRegistry") -> Any:
    """``apps.config_runner`` 模块（load_yaml_config / load_data）。"""
    import strategy_research.apps.config_runner as cr

    return cr


# ── data clean ───────────────────────────────────────────────────────


def _data_clean(registry: "CapabilityRegistry") -> Any:
    """``apps.tools.data_clean`` 模块（PRESETS / clean_data）。"""
    import strategy_research.apps.tools.data_clean as dc

    return dc


# ── backtest / acceptance ────────────────────────────────────────────


def _run_backtest_from_yaml(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.backtest import run_backtest_from_yaml

    return run_backtest_from_yaml


def _run_backtest_script(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.backtest import run_backtest_script

    return run_backtest_script


def _strategy_decide(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.strategy_acceptance import decide

    return decide


# ── goal ─────────────────────────────────────────────────────────────


def _goal_store_cls(registry: "CapabilityRegistry") -> type:
    from strategy_research.apps.goal import GoalStore

    return GoalStore


def _goal_models(registry: "CapabilityRegistry") -> Any:
    """``apps.goal.models``（EvidenceInput / GoalStatus / StaleGoalError）。"""
    import strategy_research.apps.goal.models as m

    return m


def _goal_context(registry: "CapabilityRegistry") -> Any:
    """``apps.goal.context``（get_current_goal_context /
    goal_needs_continuation / format_goal_continuation_prompt /
    default_goal_criteria）。"""
    import strategy_research.apps.goal.context as ctx

    return ctx


def _goal_expression_evaluator(registry: "CapabilityRegistry") -> Callable[..., Any]:
    from strategy_research.apps.goal.expression_evaluator import evaluate_condition

    return evaluate_condition


# ── hypothesis ───────────────────────────────────────────────────────


def _hypothesis_registry_cls(registry: "CapabilityRegistry") -> type:
    from strategy_research.apps.hypothesis.registry import HypothesisRegistry

    return HypothesisRegistry


def _hypothesis_auto_creator(registry: "CapabilityRegistry") -> type:
    from strategy_research.apps.hypothesis import HypothesisAutoCreator

    return HypothesisAutoCreator


# ── study ────────────────────────────────────────────────────────────


def _study_graph(registry: "CapabilityRegistry") -> Any:
    """``apps.study.graph``（StudyGraph / GraphNode / GraphEdge）。"""
    import strategy_research.apps.study.graph as g

    return g


def _study_store_cls(registry: "CapabilityRegistry") -> type:
    from strategy_research.apps.study import StudyStore

    return StudyStore


def _study_scheduler_cls(registry: "CapabilityRegistry") -> type:
    from strategy_research.apps.study import StudyScheduler

    return StudyScheduler


def _study_status(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.study import StudyStatus

    return StudyStatus


def _study_bootstrap(registry: "CapabilityRegistry") -> Any:
    from strategy_research.apps.study.bootstrap import create_study_record

    return create_study_record


def _ticket_sink(registry: "CapabilityRegistry") -> Any:
    """阶段一：Ticket/Assignment 持久化钩子（供 kernel 注入 SessionController）。"""
    from strategy_research.apps.study.ticket_sink import TicketSink

    return TicketSink


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


# ── 注册表 ───────────────────────────────────────────────────────────

#: kind → factory。kind 命名 = ``<域>.<能力>``，与调用点的语义一一对应。
CAPABILITIES: dict[str, Callable[["CapabilityRegistry"], Any]] = {
    # db
    "db.get_connection": _db_get_connection,
    "db.init_db": _db_init_db,
    "db.save_ohlcv_to_db": _db_save_ohlcv,
    "db.module": _db_module,
    # factor
    "factor.compute": _compute_factor,
    "factor.compute_error": _factor_compute_error,
    "factor.long_to_wide": _long_to_wide,
    # data source
    "datasource.registry": _data_source_registry,
    "datasource.base": _data_source_base,
    "datasource.detect_market": _detect_market,
    # readiness / config
    "data.readiness": _check_data_readiness,
    "config.runner": _config_runner,
    # data clean
    "data.clean": _data_clean,
    # backtest
    "backtest.from_yaml": _run_backtest_from_yaml,
    "backtest.script": _run_backtest_script,
    "backtest.decide": _strategy_decide,
    # goal
    "goal.store": _goal_store_cls,
    "goal.models": _goal_models,
    "goal.context": _goal_context,
    "goal.expression": _goal_expression_evaluator,
    # hypothesis
    "hypothesis.registry": _hypothesis_registry_cls,
    "hypothesis.auto_creator": _hypothesis_auto_creator,
    # study
    "study.graph": _study_graph,
    "study.store": _study_store_cls,
    "study.scheduler": _study_scheduler_cls,
    "study.status": _study_status,
    "study.bootstrap": _study_bootstrap,
    "study.ticket_sink": _ticket_sink,
    # evolution（阶段三）
    "evolution.resolve_harness": _evolution_resolve_harness,
    "evolution.holdout_sample": _evolution_holdout_sample,
    "evolution.proposal": _evolution_proposal_cls,
    "evolution.validation_gate": _evolution_validation_gate_cls,
    "evolution.agent": _evolution_agent_cls,
}


def register_capabilities(registry: "CapabilityRegistry") -> None:
    """Register every business capability on ``registry``.

    装配期调用一次。重复调用会因 ``register_many`` 的 fail-fast 抛错——
    装配路径必须幂等的是调用方，不是 registry。
    """
    registry.register_many(dict(CAPABILITIES))


__all__ = ["CAPABILITIES", "register_capabilities"]
