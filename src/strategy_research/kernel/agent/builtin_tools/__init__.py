"""内置 Agent 工具（按域拆分模块，本文件为注册中枢）。

域模块:
    file_tools            文件: read_file / list_files / write_file
    workspace_tools       工作区: git_diff / list_history / list_skills / load_skill
    backtest_tools        回测编排: run_backtest + 4 子步骤
    factor_tools          因子: compute_factor / factor_analysis / 因子研究套件
    options_tools         期权定价: options_pricing
    backtest_analysis_tools  回测分析: strategy_compare / drawdown / benchmark
    data_clean_tools      数据清洗: clean_data
    help_tools            工具文档: tool_help
"""
from __future__ import annotations

import logging
from pathlib import Path

from strategy_research.kernel.agent.tools import ToolRegistry
from .backtest_analysis_tools import BenchmarkComparison, DrawdownAnalysis, StrategyCompare
from .file_tools import ListFilesTool, ReadFileTool, WriteFileTool
from .help_tools import ToolHelpTool
from .options_tools import OptionsPricingTool
from .workspace_tools import GitDiffTool, ListHistoryTool, ListSkillsTool, LoadSkillTool

logger = logging.getLogger(__name__)




# ── 2026-11-05 做减法批次 5：业务工具已摘除注册 ─────────────────────
#
# 以下 9 个工具类依赖批次 4/5 删除的 capability kind（backtest.script /
# factor.compute / db.get_connection / data.clean / datasource.* /
# data.readiness），已从 build_default_registry 摘除：
#
#   RunBacktestTool · ConfigLoadStep · DataPrepareStep · DataReadinessStep
#   EngineRunStep · ComputeFactorTool · FactorAnalysisTool
#   PatternRecognitionTool · DataCleanTool · StrategyCompare
#   DrawdownAnalysis · BenchmarkComparison
#   ListDataSourcesTool · SearchSymbolTool · CheckDataTool
#
# **文件保留到批次 6**（那时连代码一起删）。本批次只保证 agent 的 tool
# 列表里不再出现「调用必然失败」的项 —— 比留着让 LLM 撞墙好。


def build_default_registry(workspace: Path | None = None) -> ToolRegistry:
    """Build a ToolRegistry with all tools.

    Tools are stateless; AgentLoop injects `workspace` per call.
    No workspace is bound at construction time.

    When ``workspace`` is given, composite tools from
    ``<workspace>/tools/combo/*.yml`` are loaded and registered
    (paradigm v2 分层注册: 显式核心 + 能力组 + 组合库加载器).
    """
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(ListFilesTool())
    r.register(WriteFileTool())
    r.register(GitDiffTool())
    r.register(ListHistoryTool())
    r.register(ListSkillsTool())
    r.register(LoadSkillTool())
    r.register(OptionsPricingTool())
    # Phase 2: Web I/O tools (conditional on dependencies)
    try:
        from .web_tools import register_web_tools
        register_web_tools(r)
    except Exception:
        pass
    # Phase 3: Market data tools —— 2026-11-05 批次 5 摘除
    # register_data_tools 注册的 ListDataSourcesTool / SearchSymbolTool /
    # CheckDataTool 全部依赖 datasource.* / data.readiness 五个已删 kind。
    # 留着注册会让 LLM 选到「调用必然失败」的工具。文件保留到批次 6。
    # Goal management tools —— 2026-11-05 批次 5 摘除
    # apps/goal 已随批次 2 移除，goal_tools.py 是死代码（create_goal /
    # complete_goal / get_goal_status / list_goals 四个工具全部依赖
    # 已不存在的 GoalStore）。文件保留到批次 6 一起删。
    # Display tools (agent-driven right panel: show_chart / show_report)
    try:
        from .display_tools import register_display_tools
        register_display_tools(r)
    except Exception:
        pass
    # Shell tools (opt-in, gated by allow_shell_tools)
    try:
        from .shell_tools import register_shell_tools
        register_shell_tools(r)
    except Exception:
        pass
    # Background-command tools (opt-in, same gate; long-task nohup mode)
    try:
        from .bg_tools import register_bg_tools
        register_bg_tools(r)
    except Exception:
        pass
    # Tool documentation (self-referential; registered last)
    r.register(ToolHelpTool(r))
    # Sub-agent delegation
    from .subagent_tool import SubAgentTool
    r.register(SubAgentTool())
    # Todo / task tracking
    from .todo_tools import TodoWriteTool
    r.register(TodoWriteTool())

    # Paradigm v2 分层注册: 组合库加载器 (workspace tools/combo/*.yml)
    if workspace is not None:
        from strategy_research.kernel.agent.combo import load_combo_tools
        load_combo_tools(workspace, r)

    # DSH-inspired: plugin discovery via entry_points
    _discover_tool_plugins(r)

    return r


def _discover_tool_plugins(registry: ToolRegistry) -> None:
    """Discover and register tool plugins via importlib.metadata entry_points.

    Plugins register under the ``strategy_research.tools`` group::

        # pyproject.toml
        [project.entry-points."strategy_research.tools"]
        my_tools = "my_package.tools:register_tools"

    The entry point must be a callable that takes a ToolRegistry and
    registers tools on it.  Failures are logged and swallowed.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return  # Python < 3.10 fallback

    eps = entry_points()
    # Python 3.12+ returns a SelectableGroups; 3.10-3.11 returns dict
    if hasattr(eps, "select"):
        tool_eps = eps.select(group="strategy_research.tools")
    elif isinstance(eps, dict):
        tool_eps = eps.get("strategy_research.tools", [])
    else:
        tool_eps = []

    for ep in tool_eps:
        try:
            register_fn = ep.load()
            register_fn(registry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load tool plugin '%s': %s", ep.name, exc)


__all__ = [
    "BenchmarkComparison",
    "DrawdownAnalysis",
    "StrategyCompare",
    "ConfigLoadStep",
    "DataPrepareStep",
    "DataReadinessStep",
    "EngineRunStep",
    "RunBacktestTool",
    "DataCleanTool",
    "ComputeFactorTool",
    "FactorAnalysisTool",
    "FactorCrossSectionalAnalysis",
    "FactorICDecay",
    "FactorQuintileReturns",
    "FactorTurnover",
    "PatternRecognitionTool",
    "ListFilesTool",
    "ReadFileTool",
    "WriteFileTool",
    "ToolHelpTool",
    "OptionsPricingTool",
    "GitDiffTool",
    "ListHistoryTool",
    "ListSkillsTool",
    "LoadSkillTool",
    "build_default_registry",
]
